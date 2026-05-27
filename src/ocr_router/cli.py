"""Command-line interface for OCR Router."""

import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import Progress
from rich.panel import Panel

from ocr_router.config import load_config, get_config_from_env
from ocr_router.extractor import PdfTextExtractor, MetadataExtractor
from ocr_router.feedback import FeedbackLog, FeedbackRecord
from ocr_router.folder_resolver import FolderResolver
from ocr_router.manifest import ManifestWriter, ManifestEntry
from ocr_router.ocr_engine import OcrEngine
from ocr_router.router import DocumentRouter

logging.basicConfig(level=logging.WARNING)   # suppress info noise in interactive mode
logger = logging.getLogger(__name__)
console = Console()

_STATUS_ICON = {
    'exact':   '[green]📁 exact[/]',
    'created': '[cyan]📂✨ new[/]',
    'flat':    '[blue]📂~ flat[/]',
    'suggest': '[yellow]⚠ suggest[/]',
}


@dataclass
class Proposal:
    """A single file move proposal produced during the analysis phase."""
    index: int
    pdf_file: Path
    pdf_to_extract: Path
    text: str
    confidence: float
    metadata: dict
    category: str
    route_path: str
    dest_dir: Path
    folder_status: str
    new_filename: str
    dest_file: Path          # final path (collision-resolved)
    issues: list[str] = field(default_factory=list)

    # ── L4 LLM diagnostics (populated only when --llm) ─────────────────────
    keyword_category: Optional[str] = None        # what keyword router said
    llm_category: Optional[str] = None            # what LLM said
    llm_issuer: Optional[str] = None
    llm_confidence: Optional[float] = None
    llm_reasons: list[str] = field(default_factory=list)
    backend_label: str = "keyword"                # keyword | local:llama3.2:3b | hybrid
    llm_duration_ms: Optional[int] = None
    llm_fewshot_count: Optional[int] = None
    llm_error: Optional[str] = None


@click.group()
@click.version_option()
def cli():
    """OCR Router — Intelligent batch PDF processing."""
    pass


@cli.command()
@click.option('--input',  type=click.Path(exists=True), required=True,
              help='Folder with PDFs to process')
@click.option('--output', type=click.Path(), required=True,
              help='Root Documents folder (destination tree root)')
@click.option('--config', type=click.Path(exists=True), default=get_config_from_env,
              help='Config YAML path')
@click.option('--max-files', type=int, default=None)
@click.option('--skip-ocr', is_flag=True, help='Skip PDF24 OCR step')
@click.option('--archive/--no-archive', default=True,
              help='Copy originals to _processed-originals/')
@click.option('--dry-run', is_flag=True,
              help='Show proposals and exit without moving anything')
@click.option('--interactive/--no-interactive', '-i', default=True,
              help='Show review table and confirm before moving files (default: on)')
@click.option('--llm/--no-llm', default=None,
              help='Ask local LLM (Ollama) for a second opinion. '
                   'Default: follow config llm.enabled.')
def process(input: str, output: str, config: str, max_files: int,
            skip_ocr: bool, archive: bool, dry_run: bool, interactive: bool,
            llm: Optional[bool]):
    """Process PDFs: OCR → extract → classify → rename → route.

    By default shows a full review table before moving any file.
    Use --no-interactive to skip confirmation (batch / cron mode).
    Use --llm to add an LLM second opinion (Ollama required).
    """
    try:
        cfg = load_config(config)
        input_dir  = Path(input)
        output_dir = Path(output)
        output_dir.mkdir(parents=True, exist_ok=True)

        ocr_engine = OcrEngine(cfg.model_dump())
        extractor  = MetadataExtractor(cfg.model_dump())
        router     = DocumentRouter(cfg.model_dump())
        resolver   = FolderResolver(output_dir)
        manifest_writer = ManifestWriter(output_dir, cfg.model_dump())
        feedback_log = FeedbackLog(_feedback_log_path(output_dir, cfg.model_dump()))

        # ── LLM classifier setup (Step 5) ──────────────────────────────────
        llm_classifier = _maybe_build_llm_classifier(
            cfg.model_dump(), config, output_dir, cli_override=llm,
        )
        if llm_classifier.enabled:
            console.print(
                f"[dim]LLM: {llm_classifier.backend.label} "
                f"(few-shot k={llm_classifier.llm_cfg.fewshot_k}, "
                f"threshold={llm_classifier.llm_cfg.confidence_threshold})[/]"
            )

        if not skip_ocr and not ocr_engine.is_available():
            console.print(f"[yellow]⚠  PDF24 not found — proceeding text-only (--skip-ocr)[/]")
            skip_ocr = True

        pdf_files = list(input_dir.rglob('*.pdf'))
        if not pdf_files:
            console.print("[yellow]No PDFs found.[/]")
            return

        # ── Pre-filter: skip files the user previously marked as parked
        # (Step 5.5). A "parked" file is one OCR'd in place that should never
        # be re-proposed unless explicitly unparked.
        parked = feedback_log.parked_filenames()
        if parked:
            parked_present = [p for p in pdf_files if p.name in parked]
            if parked_present:
                pdf_files = [p for p in pdf_files if p.name not in parked]
                console.print(
                    f"[dim]Skipping {len(parked_present)} parked file(s) "
                    f"(use `ocr-router feedback parked list` to inspect).[/]"
                )

        if max_files:
            pdf_files = pdf_files[:max_files]

        if not pdf_files:
            console.print("[yellow]No PDFs to process (all parked or filtered).[/]")
            return

        # ── Phase 1: analyse every file ─────────────────────────────────────
        console.print(f"\n[bold cyan]Analysing {len(pdf_files)} PDF(s)…[/]")
        proposals: list[Proposal] = []
        skipped_ocr: list[Path] = []
        ocr_tmp_dir = input_dir / '_ocr_tmp'

        with Progress(transient=True) as prog:
            task = prog.add_task("Reading…", total=len(pdf_files))
            for i, pdf_file in enumerate(pdf_files, 1):
                prog.update(task, advance=1)
                issues: list[str] = []

                # First attempt: extract text directly (no OCR)
                text, confidence = PdfTextExtractor.extract_text_with_confidence(pdf_file)
                pdf_to_extract = pdf_file

                # Always run ocrmypdf (OCR for scans, optimize=3 compression for all).
                # skip_text=True ensures existing-text pages are not re-OCR'd.
                if not skip_ocr:
                    ocr_tmp_dir.mkdir(parents=True, exist_ok=True)
                    ocr_out = ocr_tmp_dir / f"{pdf_file.stem}_ocr.pdf"
                    ok = ocr_engine.ocr_pdf(pdf_file, ocr_out)
                    if ok and ocr_out.exists():
                        text, confidence = PdfTextExtractor.extract_text_with_confidence(ocr_out)
                        pdf_to_extract = ocr_out
                    else:
                        issues.append("OCR/compression failed")

                if confidence == 0.0:
                    skipped_ocr.append(pdf_file)
                    continue

                metadata = extractor.extract_from_text(text, pdf_file.name)
                keyword_category = router.classify_document(text)
                category = keyword_category

                # ── LLM second opinion (Step 5) ───────────────────────────
                llm_result, llm_info = None, None
                backend_label = "keyword"
                if llm_classifier.enabled:
                    llm_result, llm_info = llm_classifier.classify(
                        text=text, filename=pdf_file.name,
                        # Make sure the LLM can pick the keyword's category even
                        # if it's "Uncategorized" (not in config.categories).
                        extra_categories=[keyword_category] if keyword_category else None,
                    )
                    if llm_result is not None:
                        backend_label, category, llm_issuer_override = _apply_llm_decision(
                            keyword_category=keyword_category,
                            llm_result=llm_result,
                            threshold=llm_classifier.llm_cfg.confidence_threshold,
                            issues=issues,
                        )
                        # Adopt the LLM's issuer when keyword extraction got nothing
                        # or when we're taking the LLM's category outright.
                        if llm_issuer_override and not metadata.get("issuer"):
                            metadata["issuer"] = llm_issuer_override

                metadata['category'] = category

                route_path = router.build_route_path(category, metadata)
                dest_dir, status = resolver.resolve(route_path)

                if status == 'suggest':
                    issues.append(f"new folder: {dest_dir.relative_to(output_dir)}")

                new_filename = router.normalize_filename(pdf_file.name, metadata)
                dest_file = _resolve_collision(dest_dir, new_filename)

                proposals.append(Proposal(
                    index=i,
                    pdf_file=pdf_file,
                    pdf_to_extract=pdf_to_extract,
                    text=text,
                    confidence=confidence,
                    metadata=metadata,
                    category=category,
                    route_path=route_path,
                    dest_dir=dest_dir,
                    folder_status=status,
                    new_filename=new_filename,
                    dest_file=dest_file,
                    issues=issues,
                    keyword_category=keyword_category,
                    llm_category=llm_result.category if llm_result else None,
                    llm_issuer=llm_result.issuer if llm_result else None,
                    llm_confidence=llm_result.confidence if llm_result else None,
                    llm_reasons=list(llm_result.reasons) if llm_result else [],
                    backend_label=backend_label,
                    llm_duration_ms=llm_info.duration_ms if llm_info else None,
                    llm_fewshot_count=llm_info.fewshot_count if llm_info else None,
                    llm_error=llm_info.error if llm_info else None,
                ))

        # ── Phase 2: review table ────────────────────────────────────────────
        _print_proposals(proposals, output_dir)

        if skipped_ocr:
            console.print(f"\n[yellow]⚠  {len(skipped_ocr)} file(s) need OCR (skipped):[/]")
            for f in skipped_ocr:
                console.print(f"   {f.name}")

        if not proposals:
            console.print("[yellow]Nothing to move.[/]")
            return

        if dry_run:
            console.print("\n[dim]--dry-run: no files moved.[/]")
            return

        # ── Phase 3: interactive confirmation ────────────────────────────────
        approved_indices: set[int]
        action_mode = 'move'   # 'move' | 'rename'
        if interactive:
            action_mode = _ask_action_mode()
            if action_mode is None:
                console.print("[yellow]Aborted.[/]")
                return
            approved_indices = _interactive_confirm(
                proposals, config, output_dir, feedback_log
            )
            if approved_indices is None:   # user quit
                console.print("[yellow]Aborted.[/]")
                return
        else:
            approved_indices = {p.index for p in proposals}

        # ── Phase 4: execute approved moves ──────────────────────────────────
        archive_dir = output_dir / "_processed-originals" if archive else None
        if archive_dir and action_mode == 'move':
            archive_dir.mkdir(parents=True, exist_ok=True)

        moved = skipped = 0
        for p in proposals:
            if p.index not in approved_indices:
                skipped += 1
                # Capture skip for future learning (best-effort, never raises)
                try:
                    feedback_log.append(FeedbackRecord.from_proposal(
                        event="skipped",
                        original_filename=p.pdf_file.name,
                        text=p.text,
                        proposal_meta=p.metadata,
                        proposed_folder=_safe_relpath(p.dest_dir, output_dir),
                        proposed_filename=p.new_filename,
                        proposed_confidence=p.confidence,
                        backend=p.backend_label,
                        extra={
                            "keyword_category": p.keyword_category,
                            "llm_category": p.llm_category,
                            "llm_confidence": p.llm_confidence,
                        },
                    ))
                except Exception:                                    # pragma: no cover
                    pass
                continue
            try:
                # Use the OCR'd file if OCR was performed, otherwise the original
                source_file = p.pdf_to_extract if p.pdf_to_extract != p.pdf_file else p.pdf_file

                if action_mode == 'rename':
                    # Rename in place: new name in same folder as source original
                    final_dest = _resolve_collision(p.pdf_file.parent, p.new_filename)
                    source_file.rename(final_dest)
                    # Remove original if OCR produced a separate file
                    if source_file != p.pdf_file and p.pdf_file.exists():
                        p.pdf_file.unlink()
                    p.dest_file = final_dest   # update for history log
                else:
                    # Move to target folder — copy the OCR'd (searchable) version
                    p.dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, p.dest_file)
                    if archive_dir:
                        # Archive the original (pre-OCR) for safekeeping
                        shutil.copy2(p.pdf_file, archive_dir / p.pdf_file.name)
                    # Remove originals from input_dir now that they are safely copied
                    p.pdf_file.unlink(missing_ok=True)
                    if source_file != p.pdf_file:
                        source_file.unlink(missing_ok=True)

                entry = ManifestEntry(
                    filename=p.pdf_file.name,
                    category=p.category,
                    issuer=p.metadata.get('issuer'),
                    owner=p.metadata.get('owner'),
                    account=p.metadata.get('account'),
                    date=p.metadata.get('date'),
                    amount=p.metadata.get('amount'),
                    ocr_confidence=p.confidence,
                    routed_to=str(p.dest_file.relative_to(output_dir))
                              if action_mode == 'move' else str(p.dest_file),
                    status='success',
                )
                manifest_writer.append_entry(entry)
                # Capture confirmation for future learning (best-effort)
                try:
                    final_folder = (
                        _safe_relpath(p.dest_file.parent, output_dir)
                        if action_mode == "move"
                        else str(p.dest_file.parent)
                    )
                    feedback_log.append(FeedbackRecord.from_proposal(
                        event="confirmed",
                        original_filename=p.pdf_file.name,
                        text=p.text,
                        proposal_meta=p.metadata,
                        proposed_folder=_safe_relpath(p.dest_dir, output_dir),
                        proposed_filename=p.new_filename,
                        proposed_confidence=p.confidence,
                        final_category=p.category,
                        final_issuer=p.metadata.get("issuer"),
                        final_folder=final_folder,
                        final_filename=p.dest_file.name,
                        backend=p.backend_label,
                        extra={
                            "action_mode": action_mode,
                            "keyword_category": p.keyword_category,
                            "llm_category": p.llm_category,
                            "llm_confidence": p.llm_confidence,
                            "llm_duration_ms": p.llm_duration_ms,
                            "llm_fewshot_count": p.llm_fewshot_count,
                        },
                    ))
                except Exception:                                    # pragma: no cover
                    pass
                moved += 1
            except Exception as e:
                console.print(f"[red]Error moving {p.pdf_file.name}: {e}[/]")

        # ── Cleanup OCR temp dir ─────────────────────────────────────────────
        if ocr_tmp_dir.exists():
            shutil.rmtree(ocr_tmp_dir, ignore_errors=True)

        verb = "Renamed" if action_mode == 'rename' else "Moved"
        console.print(f"\n[bold green]✓ {verb} {moved} file(s)[/]"
                      + (f", [yellow]skipped {skipped}[/]" if skipped else ""))
        console.print(f"  Manifest: {manifest_writer.csv_path}")

        # ── Phase 5: append to history log ───────────────────────────────────
        if moved:
            history_path = _append_history(
                output_dir,
                proposals=[p for p in proposals if p.index in approved_indices],
                run_time=_now_str(),
                action_mode=action_mode,
                config=cfg.model_dump(),
            )
            console.print(f"  History:  {history_path}")

    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/]")
        logger.exception(e)
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _print_proposals(proposals: list[Proposal], output_dir: Path) -> None:
    """Render the full review table."""
    has_llm = any(p.llm_category is not None or p.llm_error is not None for p in proposals)

    t = Table(
        title=f"\n[bold]Proposed moves — {len(proposals)} file(s)[/]",
        box=box.ROUNDED,
        show_lines=True,
        highlight=True,
    )
    t.add_column("#",             style="dim",     width=3,  no_wrap=True)
    t.add_column("Original File", style="cyan",    width=28, no_wrap=True)
    t.add_column("Category",      style="green",   width=22, no_wrap=True)
    t.add_column("Issuer",        style="yellow",  width=18, no_wrap=True)
    t.add_column("New Name",      style="white",   width=36, no_wrap=True)
    t.add_column("Amount",        style="magenta", width=10, no_wrap=True)
    if has_llm:
        t.add_column("Backend",   style="white",   width=22, no_wrap=False)
    t.add_column("Destination",   style="blue",    width=30)

    for p in proposals:
        issuer   = p.metadata.get('issuer') or '—'
        amount   = p.metadata.get('amount')
        currency = p.metadata.get('currency', '$')
        amt_str  = f'{currency}{float(amount):.2f}' if amount else '—'
        try:
            dest = str(p.dest_dir.relative_to(output_dir))
        except ValueError:
            dest = str(p.dest_dir)
        notes    = ', '.join(p.issues) if p.issues else ''

        row = [
            str(p.index),
            p.pdf_file.name[:28],
            p.category,
            issuer[:18],
            p.new_filename[:36],
            amt_str,
        ]
        if has_llm:
            row.append(_format_backend_cell(p))
        row.append(dest + (f'  [dim]{notes}[/]' if notes else ''))

        t.add_row(*row)

    console.print(t)


def _format_backend_cell(p: Proposal) -> str:
    """Render the Backend column for one proposal."""
    if p.llm_error:
        return f"[red]llm err[/] [dim]{p.llm_error[:18]}[/]"
    if p.llm_category is None:
        return "[dim]keyword[/]"

    conf = f"{p.llm_confidence:.2f}" if p.llm_confidence is not None else "?"
    agree = p.keyword_category == p.llm_category
    if p.backend_label.startswith("hybrid-llm"):
        # We overrode keyword with LLM
        return (f"[magenta]LLM ✱[/] [bold]{conf}[/]\n"
                f"[dim]kw said: {p.keyword_category or '—'}[/]")
    if p.backend_label == "keyword-llm-low-conf":
        return (f"[yellow]kw (LLM low)[/] [dim]{conf}[/]\n"
                f"[dim]LLM said: {p.llm_category}[/]")
    if agree:
        return f"[green]agree ✓[/] [bold]{conf}[/]"
    return f"[blue]kw[/] [dim]LLM {conf}[/]"


def _apply_llm_decision(
    *,
    keyword_category: str,
    llm_result,
    threshold: float,
    issues: list[str],
) -> tuple[str, str, Optional[str]]:
    """Decide the final category from keyword + LLM verdicts.

    Returns (backend_label, final_category, llm_issuer_override).

    Rules:
      * LLM confidence < threshold        → keep keyword, surface as a note
      * LLM agrees with keyword           → keep both, no flag
      * LLM disagrees and is confident    → take the LLM's category, flag for HITL
      * Keyword said 'Uncategorized' and LLM has any answer above threshold → take LLM
    """
    llm_cat = llm_result.category
    llm_conf = float(llm_result.confidence)
    llm_iss = llm_result.issuer

    # Below threshold: trust keyword, surface LLM opinion as a hint
    if llm_conf < threshold:
        if llm_cat and llm_cat != keyword_category:
            issues.append(f"LLM suggests {llm_cat} ({llm_conf:.2f})")
        return "keyword-llm-low-conf", keyword_category, None

    # Agreement
    if llm_cat == keyword_category:
        # Even on agreement, adopt the LLM issuer if it spotted one
        return "agree", keyword_category, llm_iss

    # Disagreement, LLM confident
    if not keyword_category or keyword_category == "Uncategorized":
        # Keyword had no opinion → defer to LLM silently
        return "hybrid-llm", llm_cat, llm_iss

    # Genuine disagreement
    issues.append(
        f"LLM says {llm_cat} ({llm_conf:.2f}), keyword says {keyword_category}"
    )
    return "hybrid-llm-disagree", llm_cat, llm_iss


def _maybe_build_llm_classifier(
    config: dict,
    config_path: Optional[str],
    output_dir: Path,
    *,
    cli_override: Optional[bool] = None,
):
    """Build an LLMClassifier respecting CLI/config gating.

    Returns a classifier whose ``.enabled`` is False when LLM is off — no
    network calls happen in that case. When on, performs a one-time probe;
    if Ollama is down, returns a NullBackend-wrapped classifier so the
    pipeline degrades gracefully.
    """
    from ocr_router.feedback import (
        DEFAULT_EMBED_MODEL, EmbeddingStore, OllamaEmbedder,
    )
    from ocr_router.llm import LLMClassifier, NullBackend, OllamaBackend
    from ocr_router.llm.classifier import LLMConfig

    llm_cfg = LLMConfig.from_dict(config)
    enabled = llm_cfg.enabled if cli_override is None else cli_override
    if not enabled:
        return LLMClassifier(backend=NullBackend("LLM off"), config=config)

    backend = OllamaBackend(model=llm_cfg.local_model, host=llm_cfg.host)
    info = backend.info()
    if not info.available:
        console.print(
            f"[yellow]⚠ LLM requested but unavailable ({info.note}); "
            "falling back to keyword-only.[/]"
        )
        return LLMClassifier(
            backend=NullBackend(info.note or "Ollama unavailable"),
            config=config,
        )

    embedder = OllamaEmbedder(
        model=llm_cfg.embed_model or DEFAULT_EMBED_MODEL, host=llm_cfg.host,
    )
    db_path = _resolve_embed_db_path(str(output_dir), config_path)
    store = EmbeddingStore(db_path) if db_path.exists() else None
    if store is None:
        console.print(
            f"[dim]LLM: embedding store missing ({db_path}); few-shot disabled.[/]"
        )

    return LLMClassifier(
        backend=backend, embedder=embedder, store=store, config=config,
    )


def _ask_action_mode() -> Optional[str]:
    """Ask whether to MOVE files to target folders or RENAME IN PLACE.

    Returns 'move', 'rename', or None (abort).
    """
    console.print(Panel(
        "[bold]What would you like to do with the files?[/]\n\n"
        "  [green]m[/]  → [bold]Move[/] files to their target folders (shown in the table)\n"
        "  [yellow]r[/]  → [bold]Rename in place[/] — keep files in current folder, apply new names only\n"
        "  [red]q[/]  → Quit without doing anything",
        title="Move or Rename?",
        border_style="magenta",
    ))
    raw = console.input("[bold magenta]> [/]").strip().lower()
    if raw in ('m', 'move', ''):
        return 'move'
    if raw in ('r', 'rename'):
        return 'rename'
    if raw == 'q':
        return None
    # Default to move for any other input
    console.print("[dim]Unrecognised — defaulting to move.[/]")
    return 'move'


def _interactive_confirm(
    proposals: list[Proposal],
    config_path: str,
    output_dir: Path,
    feedback_log: "FeedbackLog",
) -> Optional[set[int]]:
    """Ask which files to move, collect rules for skipped ones.

    Supported commands at the prompt:
      Enter         — move ALL files
      1,3,5         — move ONLY those numbers
      skip 2,4      — move all EXCEPT those numbers
      park 7        — keep those files exactly where they are; never propose them
                      again on future runs (records as event='parked' in the log)
      q             — quit without moving anything

    Returns a set of approved indices, or None if the user quits.
    """
    console.print(Panel(
        "[bold]Review the table above.[/]\n\n"
        "  [green]Enter[/]          → move ALL files\n"
        "  [yellow]1,3,5[/]          → move ONLY those numbers\n"
        "  [yellow]skip 2,4[/]       → move all EXCEPT those numbers\n"
        "  [magenta]park 7[/]         → keep those files in place permanently (never re-propose)\n"
        "  [red]q[/]              → quit without moving anything",
        title="What would you like to do?",
        border_style="cyan",
    ))

    raw = console.input("[bold cyan]> [/]").strip()

    if raw.lower() == 'q':
        return None

    all_indices = {p.index for p in proposals}
    parked_indices: set[int] = set()

    if raw == '':
        approved = all_indices
    elif raw.lower().startswith('skip '):
        nums = _parse_nums(raw[5:])
        approved = all_indices - nums
    elif raw.lower().startswith('park '):
        parked_indices = _parse_nums(raw[5:]) & all_indices
        approved = all_indices - parked_indices
    else:
        approved = _parse_nums(raw)

    if parked_indices:
        _record_parked(
            [p for p in proposals if p.index in parked_indices],
            output_dir,
            feedback_log,
        )

    skipped = all_indices - approved - parked_indices
    if skipped:
        console.print(f"\n[yellow]Skipping #{', '.join(str(n) for n in sorted(skipped))}[/]")
        _collect_rules_for_skipped(
            [p for p in proposals if p.index in skipped],
            config_path,
            output_dir,
            feedback_log,
        )

    return approved


def _record_parked(
    parked: list[Proposal],
    output_dir: Path,
    feedback_log: "FeedbackLog",
) -> None:
    """Write a ``parked`` feedback record for each file kept in place.

    Best-effort: each append failure is swallowed so the pipeline keeps moving.
    """
    console.print(f"\n[magenta]Parking #{', '.join(str(p.index) for p in parked)} "
                  f"— these files will not be re-proposed on future runs.[/]")
    for p in parked:
        try:
            current_folder = _safe_relpath(p.pdf_file.parent, output_dir)
            feedback_log.append(FeedbackRecord.from_proposal(
                event="parked",
                original_filename=p.pdf_file.name,
                text=p.text,
                proposal_meta=p.metadata,
                proposed_folder=_safe_relpath(p.dest_dir, output_dir),
                proposed_filename=p.new_filename,
                proposed_confidence=p.confidence,
                final_category=p.category,
                final_issuer=p.metadata.get("issuer"),
                final_folder=current_folder,
                final_filename=p.pdf_file.name,
                backend=p.backend_label,
                extra={
                    "parked_at": current_folder,
                    "keyword_category": p.keyword_category,
                    "llm_category": p.llm_category,
                    "llm_confidence": p.llm_confidence,
                },
            ))
        except Exception:                                            # pragma: no cover
            pass


def _collect_rules_for_skipped(
    skipped: list[Proposal],
    config_path: str,
    output_dir: Path,
    feedback_log: "FeedbackLog",
) -> None:
    """For each skipped file, optionally collect a new rule and append it to config.

    Also writes a ``rule_added`` feedback record so future learning passes can
    cross-reference YAML edits against the documents that motivated them.
    """
    console.print("\n[bold]For each skipped file you can add a rule to improve future runs.[/]")
    console.print("[dim]Examples:  issuer=FPL  |  category=Bills  |  skip  |  (blank = nothing)[/]\n")

    new_issuers: dict[str, str] = {}
    new_keywords: dict[str, list[str]] = {}

    for p in skipped:
        prompt = (f"  [cyan]{p.pdf_file.name}[/] "
                  f"(was: [dim]{p.category}[/], issuer=[dim]{p.metadata.get('issuer') or '—'}[/])\n"
                  f"  Rule? > ")
        rule = console.input(prompt).strip()

        if not rule or rule.lower() == 'skip':
            continue

        if rule.lower().startswith('issuer='):
            val = rule.split('=', 1)[1].strip()
            stem = Path(p.pdf_file.name).stem.lower()
            new_issuers[stem] = val
            console.print(f"  [green]✓ Will add issuer rule: {stem!r} → {val!r}[/]")
            try:
                feedback_log.append(FeedbackRecord.from_proposal(
                    event="rule_added",
                    original_filename=p.pdf_file.name,
                    text=p.text,
                    proposal_meta=p.metadata,
                    proposed_folder=_safe_relpath(p.dest_dir, output_dir),
                    proposed_filename=p.new_filename,
                    proposed_confidence=p.confidence,
                    final_issuer=val,
                    backend="keyword",
                    extra={"rule_kind": "issuer", "stem": stem},
                ))
            except Exception:                                        # pragma: no cover
                pass

        elif rule.lower().startswith('category='):
            val = rule.split('=', 1)[1].strip()
            # Add the original filename stem as a keyword for that category
            kw = Path(p.pdf_file.name).stem.lower().replace('-', ' ').replace('_', ' ')
            new_keywords.setdefault(val, []).append(kw)
            console.print(f"  [green]✓ Will add keyword {kw!r} to category {val!r}[/]")
            try:
                feedback_log.append(FeedbackRecord.from_proposal(
                    event="rule_added",
                    original_filename=p.pdf_file.name,
                    text=p.text,
                    proposal_meta=p.metadata,
                    proposed_folder=_safe_relpath(p.dest_dir, output_dir),
                    proposed_filename=p.new_filename,
                    proposed_confidence=p.confidence,
                    final_category=val,
                    backend="keyword",
                    extra={"rule_kind": "category", "keyword": kw},
                ))
            except Exception:                                        # pragma: no cover
                pass

        else:
            console.print(f"  [dim]Unrecognised format — skipped[/]")

    if new_issuers or new_keywords:
        _append_rules_to_config(config_path, new_issuers, new_keywords)


def _append_rules_to_config(config_path: str, new_issuers: dict, new_keywords: dict) -> None:
    """Merge user-supplied rules into the config YAML in-place."""
    cfg_file = Path(config_path)
    with open(cfg_file, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    for key, val in new_issuers.items():
        data.setdefault('known_issuers', {})[key] = val

    for category, kws in new_keywords.items():
        cats = data.setdefault('categories', {})
        cats.setdefault(category, [])
        for kw in kws:
            if kw not in cats[category]:
                cats[category].append(kw)

    with open(cfg_file, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    console.print(f"\n[green]✓ Config updated: {cfg_file}[/]")
    console.print("[dim]Re-run to apply new rules.[/]")


def _parse_nums(s: str) -> set[int]:
    nums = set()
    for part in s.replace(' ', ',').split(','):
        part = part.strip()
        if part.isdigit():
            nums.add(int(part))
    return nums


def _resolve_collision(dest_dir: Path, filename: str) -> Path:
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = dest_dir / filename
    counter = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem} - {counter}{suffix}"
        counter += 1
    return candidate


def _safe_relpath(path: Path, base: Path) -> str:
    """Return ``path`` relative to ``base`` as a string, or absolute string on failure."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _feedback_log_path(output_dir: Path, config: dict) -> Path:
    """Resolve where to write the feedback JSONL log.

    Order of precedence:
      1. ``OCR_FEEDBACK_LOG`` environment variable
      2. ``feedback.path`` in routing config
      3. ``<output_dir>/_feedback/corrections.jsonl`` (default)
    """
    import os
    env = os.environ.get("OCR_FEEDBACK_LOG")
    if env:
        return Path(env)
    cfg_path = (config or {}).get("feedback", {}).get("path")
    if cfg_path:
        return Path(cfg_path)
    return output_dir / "_feedback" / "corrections.jsonl"


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M')


def _content_summary(p: Proposal) -> str:
    """One-line human description of the document content."""
    cat      = p.category
    issuer   = p.metadata.get('issuer') or ''
    date     = p.metadata.get('date') or ''
    amount   = p.metadata.get('amount')
    currency = p.metadata.get('currency', '$')

    amount_str = f'{currency}{float(amount):.2f}' if amount else ''
    date_str   = date[:10] if date else ''

    templates = {
        'Bills':                         f'{issuer} service bill — {date_str}',
        'Credit Card Statements':        f'{issuer} credit card statement — {date_str}'
                                         + (f', balance {amount_str}' if amount_str else ''),
        'Bank Account & Statements':     f'{issuer} bank statement — {date_str}',
        'Mortgage & Home Equity Accounts': f'{issuer} mortgage/HELOC statement — {date_str}',
        'HSA & FSA Transactions':        f'HSA/FSA transactions — {date_str}',
        'Health Statements & Results':   f'{issuer} explanation of benefits — service {date_str}'
                                         + (f', billed {amount_str}' if amount_str else ''),
        'Insurance':                     f'{issuer} insurance policy/document — {date_str}',
        'Tax Returns':                   f'Tax document — year {date_str[:4]}',
        'Paystubs':                      f'{issuer} paycheck — period {date_str}'
                                         + (f', net {amount_str}' if amount_str else ''),
        'Receipts':                      f'{issuer} purchase receipt — {date_str}'
                                         + (f', {amount_str}' if amount_str else ''),
        'Real Estate & HOA':             f'{issuer} HOA document — {date_str}',
        'Notices':                       f'Notice/letter — {date_str}',
    }
    return templates.get(cat, f'{cat} document — {date_str}').strip(' —')


def _append_history(output_dir: Path, proposals: list[Proposal], run_time: str,
                    action_mode: str = 'move', config: dict | None = None) -> Path:
    """Append processed files to a PROCESSED_PDFS.md log, grouped by date.

    Path selection order:
      1. ``history.path``    in config -> use that file verbatim (1 rolling file)
      2. ``history.monthly`` true      -> ``<history.dir or output_dir>/<YYYY.MM> - PROCESSED_PDFS.md``
      3. Default                       -> ``<output_dir>/PROCESSED_PDFS.md``

    Format:
        ## 2026-05-17

        ### 21:30 — 5 files moved

        | # | Original File | Category | Issuer | New Name | Amount | Destination |
        |...|...|
    """
    history_file = _resolve_history_path(output_dir, run_time, config)
    history_file.parent.mkdir(parents=True, exist_ok=True)

    # Parse date and time from run_time (e.g. "2026-05-17 21:30")
    date_str = run_time[:10]   # "2026-05-17"
    time_str = run_time[11:16] if len(run_time) > 10 else ''  # "21:30"

    existing = history_file.read_text(encoding='utf-8') if history_file.exists() else ''
    is_new   = not existing

    verb        = 'renamed in place' if action_mode == 'rename' else 'moved'
    dest_header = 'Final Path' if action_mode == 'rename' else 'Destination'
    n           = len(proposals)

    # ── Build Markdown table ────────────────────────────────────────────────────
    headers = ["#", "Original File", "Category", "Issuer", "New Name", "Amount", dest_header]
    table_lines: list[str] = []
    table_lines.append('| ' + ' | '.join(headers) + ' |\n')
    table_lines.append('| ' + ' | '.join('---' for _ in headers) + ' |\n')

    for p in proposals:
        issuer   = p.metadata.get('issuer') or '—'
        amount   = p.metadata.get('amount')
        currency = p.metadata.get('currency', '$')
        amt_str  = f'{currency}{float(amount):.2f}' if amount else '—'
        if action_mode == 'rename':
            dest = str(p.dest_file)
        else:
            try:
                dest = str(p.dest_file.relative_to(output_dir))
            except ValueError:
                dest = str(p.dest_file)

        row = [str(p.index), _md_escape(p.pdf_file.name), _md_escape(p.category),
               _md_escape(issuer), _md_escape(p.new_filename), amt_str, _md_escape(dest)]
        table_lines.append('| ' + ' | '.join(row) + ' |\n')

    table_lines.append('\n')

    # ── Build section ────────────────────────────────────────────────────────
    day_heading = f'## {date_str}'

    lines: list[str] = []

    if is_new:
        # For monthly files use a richer header so they self-document
        month_label = run_time[:7].replace('-', '.')  # "2026.05"
        title = (
            f'# {month_label} — Processed Downloads\n\n'
            if _is_monthly_history(config)
            else '# Processed PDFs\n\n'
        )
        lines += [
            title,
            'Auto-generated by ocr-router — one section per day, one run per sub-heading.\n\n',
        ]

    # If today's date section already exists, just append the run block under it
    # by writing it after the last line (the file is append-only by date).
    if day_heading in existing:
        # Date section exists — open and append run sub-block
        lines += [f'\n### {time_str} — {n} file(s) {verb}\n\n']
    else:
        lines += [
            f'\n---\n\n',
            f'{day_heading}\n\n',
            f'### {time_str} — {n} file(s) {verb}\n\n',
        ]

    lines += table_lines

    with open(history_file, 'a', encoding='utf-8') as f:
        f.writelines(lines)

    return history_file


def _is_monthly_history(config: dict | None) -> bool:
    return bool((config or {}).get('history', {}).get('monthly', False))


def _resolve_history_path(output_dir: Path, run_time: str, config: dict | None) -> Path:
    """Pick the history.md path for this run.

    See ``_append_history`` for the order of precedence.
    """
    history_cfg = (config or {}).get('history', {}) or {}

    explicit = history_cfg.get('path')
    if explicit:
        return Path(explicit)

    if history_cfg.get('monthly'):
        base = Path(history_cfg.get('dir')) if history_cfg.get('dir') else output_dir
        month_label = run_time[:7].replace('-', '.')  # "2026-05" -> "2026.05"
        return base / f"{month_label} - PROCESSED_PDFS.md"

    return output_dir / 'PROCESSED_PDFS.md'


def _md_escape(s: str) -> str:
    """Escape pipe characters so they don't break Markdown tables."""
    return s.replace('|', '\\|')


@cli.command()
@click.option('--manifest', type=click.Path(exists=True), required=True)
def review(manifest: str):
    """Review processed documents from manifest."""
    manifest_path = Path(manifest).parent
    writer = ManifestWriter(manifest_path, {})
    entries = writer.get_entries()

    console.print(f"[bold cyan]Manifest Review: {len(entries)} entries[/]\n")
    t = Table()
    t.add_column("Filename",  style="cyan")
    t.add_column("Category",  style="green")
    t.add_column("Issuer",    style="yellow")
    t.add_column("Date",      style="magenta")
    for entry in entries[:20]:
        t.add_row(
            entry.get('filename', '')[:30],
            entry.get('category', '')[:20],
            entry.get('issuer',   '')[:15],
            entry.get('date',     '')[:10],
        )
    console.print(t)


# ── Feedback commands (L1: append-only learning log) ─────────────────────────

@cli.group()
def feedback():
    """Inspect and manage the learning feedback log."""
    pass


def _resolve_feedback_path(output: Optional[str], config: Optional[str]) -> Path:
    """Mirror of process()'s log path resolution for feedback subcommands."""
    import os
    env = os.environ.get("OCR_FEEDBACK_LOG")
    if env:
        return Path(env)
    cfg_dict: dict = {}
    if config:
        try:
            cfg_dict = load_config(config).model_dump()
        except Exception:
            cfg_dict = {}
    cfg_path = (cfg_dict or {}).get("feedback", {}).get("path")
    if cfg_path:
        return Path(cfg_path)
    if output:
        return Path(output) / "_feedback" / "corrections.jsonl"
    raise click.UsageError(
        "Cannot resolve feedback log path. "
        "Set OCR_FEEDBACK_LOG, or pass --output, or set feedback.path in config."
    )


@feedback.command("stats")
@click.option("--output", type=click.Path(), default=None,
              help="Documents root used during process (used to locate _feedback/).")
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env,
              help="Config YAML path (read feedback.path if set).")
def feedback_stats(output: Optional[str], config: Optional[str]):
    """Show counts of confirmed / corrected / skipped / rule_added records."""
    path = _resolve_feedback_path(output, config)
    log = FeedbackLog(path)
    s = log.stats()

    if s["total"] == 0:
        console.print(f"[yellow]No feedback records yet at: {path}[/]")
        return

    console.print(f"\n[bold]Feedback log:[/] [cyan]{s['path']}[/]")
    console.print(f"[bold]Total records:[/] {s['total']}\n")

    t = Table(title="By event", box=box.ROUNDED)
    t.add_column("Event", style="cyan")
    t.add_column("Count", style="green", justify="right")
    for ev, n in sorted(s["by_event"].items(), key=lambda kv: -kv[1]):
        t.add_row(ev, str(n))
    console.print(t)

    t = Table(title="By category", box=box.ROUNDED)
    t.add_column("Category", style="cyan")
    t.add_column("Count", style="green", justify="right")
    for cat, n in sorted(s["by_category"].items(), key=lambda kv: -kv[1])[:15]:
        t.add_row(cat, str(n))
    console.print(t)

    t = Table(title="By backend", box=box.ROUNDED)
    t.add_column("Backend", style="cyan")
    t.add_column("Count", style="green", justify="right")
    for be, n in sorted(s["by_backend"].items(), key=lambda kv: -kv[1]):
        t.add_row(be, str(n))
    console.print(t)


@feedback.command("show")
@click.option("--output", type=click.Path(), default=None)
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
@click.option("--limit", type=int, default=20, help="How many of the most recent records to show.")
@click.option("--event", type=click.Choice(["confirmed", "corrected", "skipped", "rule_added"]),
              default=None, help="Filter by event type.")
def feedback_show(output: Optional[str], config: Optional[str],
                  limit: int, event: Optional[str]):
    """List the most recent feedback records."""
    path = _resolve_feedback_path(output, config)
    log = FeedbackLog(path)

    records = list(log.iter_records())
    if event:
        records = [r for r in records if r.get("event") == event]
    records = records[-limit:]

    if not records:
        console.print(f"[yellow]No matching records in: {path}[/]")
        return

    t = Table(title=f"Last {len(records)} feedback record(s)", box=box.ROUNDED, show_lines=False)
    t.add_column("When", style="dim", width=19)
    t.add_column("Event", style="cyan", width=11)
    t.add_column("File", style="white", width=28, no_wrap=True)
    t.add_column("Category", style="green", width=22)
    t.add_column("Issuer", style="yellow", width=18)
    t.add_column("Backend", style="magenta", width=14)

    for r in records:
        ts = (r.get("ts") or "")[:19]
        cat = r.get("final_category") or r.get("proposed_category") or "—"
        iss = r.get("final_issuer") or r.get("proposed_issuer") or "—"
        t.add_row(
            ts,
            r.get("event", "—"),
            (r.get("original_filename") or "—")[:28],
            cat[:22],
            iss[:18],
            r.get("backend", "—")[:14],
        )
    console.print(t)


@feedback.command("export")
@click.option("--output", type=click.Path(), default=None)
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
@click.option("--to", "to_path", type=click.Path(), required=True,
              help="Destination file (.jsonl or .json).")
def feedback_export(output: Optional[str], config: Optional[str], to_path: str):
    """Copy the feedback log to a chosen path (JSONL pass-through, or JSON array)."""
    src = _resolve_feedback_path(output, config)
    if not src.exists():
        console.print(f"[yellow]Nothing to export — no log at {src}[/]")
        return

    dest = Path(to_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.suffix.lower() == ".json":
        import json as _json
        records = list(FeedbackLog(src).iter_records())
        dest.write_text(_json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # Pass-through copy preserves JSONL format
        shutil.copy2(src, dest)

    console.print(f"[green]✓ Exported to {dest}[/]")


@feedback.command("bootstrap")
@click.option("--source", type=click.Path(exists=True, file_okay=False), required=True,
              help="Folder of PDFs to replay (e.g. __downloads__).")
@click.option("--history", "history_path", type=click.Path(exists=True), required=True,
              help="PROCESSED_PDFS.md file (or a folder containing *PROCESSED_PDFS*.md).")
@click.option("--output", type=click.Path(), default=None,
              help="Documents root (used to locate _feedback/ if no override).")
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env,
              help="Config YAML path (for ocr_settings and feedback.path).")
@click.option("--include-unprocessed", is_flag=True,
              help="Also log files with no history match (as 'pending').")
@click.option("--force", is_flag=True,
              help="Re-import even if the filename is already in the log.")
@click.option("--skip-ocr", is_flag=True,
              help="Never OCR; record only what pypdf can already read.")
def feedback_bootstrap(source: str, history_path: str, output: Optional[str],
                       config: Optional[str], include_unprocessed: bool,
                       force: bool, skip_ocr: bool):
    """Replay processed PDFs from a download folder into the feedback log.

    Reads each PDF in --source, looks up its row in --history, and writes a
    'confirmed' record to the feedback log. Runs full OCR only for files
    that have no text layer. --history can be a file OR a directory; if a
    directory, every '*PROCESSED_PDFS*.md' file inside is parsed.
    """
    from ocr_router.feedback import (
        bootstrap_from_downloads,
        parse_processed_history_paths,
        index_history,
    )

    cfg_dict: dict = {}
    if config:
        try:
            cfg_dict = load_config(config).model_dump()
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not load config ({exc}); using defaults.[/]")

    log_path = _resolve_feedback_path(output, config)
    log = FeedbackLog(log_path)

    ocr_engine = None if skip_ocr else OcrEngine(cfg_dict)
    if ocr_engine and not ocr_engine.is_available():
        console.print("[yellow]⚠ Tesseract not found — falling back to --skip-ocr behavior.[/]")
        ocr_engine = None

    source_dir = Path(source)
    history = Path(history_path)

    pdf_count = len(list(source_dir.rglob("*.pdf")))
    if pdf_count == 0:
        console.print(f"[yellow]No PDFs found in {source_dir}[/]")
        return

    # Preview the parsed history so the user knows what was loaded
    history_entries = parse_processed_history_paths(history)
    history_index = index_history(history_entries)

    console.print(f"[bold cyan]Bootstrapping from {pdf_count} PDF(s) in {source_dir}…[/]")
    console.print(f"  History: [dim]{history}[/] ({len(history_entries)} rows, "
                  f"{len(history_index)} unique filenames)")
    console.print(f"  Log:     [dim]{log_path}[/]")
    if include_unprocessed:
        console.print("  Mode:    [dim]include unprocessed (pending records)[/]")
    if force:
        console.print("  Mode:    [dim]force re-import[/]")
    if skip_ocr or ocr_engine is None:
        console.print("  OCR:     [dim]disabled (pypdf text layer only)[/]")
    else:
        console.print("  OCR:     [dim]on-demand for files with no text layer[/]")

    # Write the parsed history into a temp Markdown file the bootstrap function
    # can re-parse — keeps bootstrap_from_downloads's signature simple. (Or we
    # could refactor to accept the index directly; not worth it for this iteration.)
    import tempfile
    if history.is_dir():
        # Concatenate all matched .md files into one temp file
        from ocr_router.feedback.bootstrap import parse_processed_history_paths as _p
        merged = "\n\n".join(
            f.read_text(encoding="utf-8")
            for f in sorted(history.glob("*PROCESSED_PDFS*.md"))
        )
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
            tf.write(merged)
            history_for_func = Path(tf.name)
    else:
        history_for_func = history

    with Progress(transient=True) as prog:
        task = prog.add_task("Bootstrapping…", total=pdf_count)

        def _cb(i, total, path):
            prog.update(task, completed=i)

        stats = bootstrap_from_downloads(
            source_dir=source_dir,
            history_path=history_for_func,
            feedback_log=log,
            ocr_engine=ocr_engine,
            force=force,
            include_unprocessed=include_unprocessed,
            skip_ocr=skip_ocr or ocr_engine is None,
            progress_cb=_cb,
        )

    if history.is_dir():
        try:
            history_for_func.unlink()
        except Exception:
            pass

    _print_bootstrap_summary(stats)


@feedback.command("bootstrap-tree")
@click.option("--root", type=click.Path(exists=True, file_okay=False), required=True,
              help="Organized Documents root (e.g. C:\\Users\\<user>\\Documents).")
@click.option("--output", type=click.Path(), default=None,
              help="Documents root for _feedback/ location (defaults to --root).")
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env,
              help="Config YAML path (for ocr_settings and feedback.path).")
@click.option("--force", is_flag=True,
              help="Re-import filenames already present in the log.")
@click.option("--skip-ocr", is_flag=True,
              help="Never OCR; record only what pypdf can already read.")
@click.option("--max-files", type=int, default=None,
              help="Cap on number of PDFs to process (useful for a smoke test).")
@click.option("--only", "only_categories", multiple=True,
              help="Only scan these top-level category dirs (repeatable).")
@click.option("--exclude", "exclude_categories", multiple=True,
              help="Skip these top-level category dirs (repeatable).")
def feedback_bootstrap_tree(root: str, output: Optional[str], config: Optional[str],
                            force: bool, skip_ocr: bool, max_files: Optional[int],
                            only_categories: tuple[str, ...],
                            exclude_categories: tuple[str, ...]):
    """Bootstrap from the organized Documents tree (path → category/issuer/year).

    Walks every PDF under --root and records a 'confirmed' entry per file,
    using its folder layout as the label. Use this when PROCESSED_PDFS.md is
    missing or incomplete but the filed PDFs themselves are intact.
    """
    from ocr_router.feedback import bootstrap_from_tree

    cfg_dict: dict = {}
    if config:
        try:
            cfg_dict = load_config(config).model_dump()
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not load config ({exc}); using defaults.[/]")

    root_dir = Path(root)
    log_output = output or root
    log_path = _resolve_feedback_path(log_output, config)
    log = FeedbackLog(log_path)

    ocr_engine = None if skip_ocr else OcrEngine(cfg_dict)
    if ocr_engine and not ocr_engine.is_available():
        console.print("[yellow]⚠ Tesseract not found — falling back to --skip-ocr behavior.[/]")
        ocr_engine = None

    console.print(f"[bold cyan]Walking Documents tree at {root_dir}…[/]")
    console.print(f"  Log:     [dim]{log_path}[/]")
    if only_categories:
        console.print(f"  Only:    [dim]{', '.join(only_categories)}[/]")
    if exclude_categories:
        console.print(f"  Exclude: [dim]{', '.join(exclude_categories)}[/]")
    if max_files:
        console.print(f"  Cap:     [dim]{max_files} files[/]")
    if skip_ocr or ocr_engine is None:
        console.print("  OCR:     [dim]disabled (pypdf text layer only)[/]")
    else:
        console.print("  OCR:     [dim]on-demand for files with no text layer[/]")

    with Progress(transient=False) as prog:
        task = prog.add_task("Scanning…", total=None)

        def _cb(i, total, path):
            prog.update(task, completed=i, total=total,
                        description=f"[cyan]Scanning…[/] {i}/{total}")

        stats = bootstrap_from_tree(
            root=root_dir,
            feedback_log=log,
            ocr_engine=ocr_engine,
            force=force,
            skip_ocr=skip_ocr or ocr_engine is None,
            only_categories=list(only_categories) if only_categories else None,
            excluded_categories=list(exclude_categories) if exclude_categories else None,
            max_files=max_files,
            progress_cb=_cb,
        )

    _print_bootstrap_summary(stats)


def _print_bootstrap_summary(stats) -> None:
    """Render BootstrapStats as a Rich table."""
    t = Table(title="Bootstrap summary", box=box.ROUNDED)
    t.add_column("Metric", style="cyan")
    t.add_column("Count", style="green", justify="right")
    t.add_row("Scanned",                    str(stats.scanned))
    t.add_row("Matched label",              str(stats.matched))
    t.add_row("Records appended",           str(stats.appended))
    t.add_row("Skipped (already in log)",   str(stats.already_logged))
    t.add_row("Skipped (no label)",         str(stats.no_history_match))
    t.add_row("Had text layer",             str(stats.text_extracted))
    t.add_row("OCR run",                    str(stats.ocr_run))
    t.add_row("OCR failed",                 str(stats.ocr_failed))
    t.add_row("Errors",                     str(len(stats.errors)))
    console.print(t)

    if stats.errors:
        console.print("\n[yellow]First few errors:[/]")
        for e in stats.errors[:5]:
            console.print(f"  • {e}")


# ── Embedding store path resolution (L3) ─────────────────────────────────────

def _resolve_embed_db_path(output: Optional[str], config: Optional[str]) -> Path:
    """Resolve where to store the SQLite embedding DB.

    Order of precedence:
      1. ``OCR_EMBEDDINGS_DB`` env var
      2. ``feedback.embeddings_db`` in routing config
      3. Sibling of the feedback log: ``<feedback_dir>/examples.sqlite``
    """
    import os as _os
    env = _os.environ.get("OCR_EMBEDDINGS_DB")
    if env:
        return Path(env)
    cfg_dict: dict = {}
    if config:
        try:
            cfg_dict = load_config(config).model_dump()
        except Exception:
            cfg_dict = {}
    cfg_path = (cfg_dict or {}).get("feedback", {}).get("embeddings_db")
    if cfg_path:
        return Path(cfg_path)
    log_path = _resolve_feedback_path(output, config)
    return log_path.parent / "examples.sqlite"


@feedback.command("embed")
@click.option("--output", type=click.Path(), default=None,
              help="Documents root (used to locate _feedback/).")
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env,
              help="Config YAML path (read feedback.embeddings_db if set).")
@click.option("--model", default=None,
              help="Ollama embed model (default: nomic-embed-text).")
@click.option("--host", default=None, help="Ollama host (default: localhost).")
def feedback_embed(output: Optional[str], config: Optional[str],
                   model: Optional[str], host: Optional[str]):
    """Embed all new 'confirmed' records from the log into the SQLite store."""
    from ocr_router.feedback import (
        EmbeddingStore, OllamaEmbedder, OllamaUnavailable,
        index_log_into_store, DEFAULT_EMBED_MODEL,
    )

    log_path = _resolve_feedback_path(output, config)
    db_path = _resolve_embed_db_path(output, config)

    log = FeedbackLog(log_path)
    if log.stats()["total"] == 0:
        console.print(f"[yellow]No records to embed at: {log_path}[/]")
        return

    embedder = OllamaEmbedder(model=model or DEFAULT_EMBED_MODEL, host=host)
    # Probe Ollama once up-front so we don't spam errors per-record
    try:
        embedder.embed("ocr-router connectivity probe")
    except OllamaUnavailable as exc:
        console.print(f"[red]Ollama unavailable: {exc}[/]")
        console.print("[dim]Hint: is `ollama serve` running? Did you `ollama pull "
                      f"{embedder.model}`?[/]")
        return

    store = EmbeddingStore(db_path)

    console.print(f"[bold cyan]Embedding records into {db_path}…[/]")
    console.print(f"  Model: [dim]{embedder.model}[/]")
    console.print(f"  Log:   [dim]{log_path}[/]")

    records = list(log.iter_records())
    with Progress(transient=False) as prog:
        task = prog.add_task("Embedding…", total=len(records))

        def _cb(i, total):
            prog.update(task, completed=i,
                        description=f"[cyan]Embedding…[/] {i}/{total}")

        stats = index_log_into_store(records, store, embedder, progress_cb=_cb)

    t = Table(title="Embed summary", box=box.ROUNDED)
    t.add_column("Metric", style="cyan")
    t.add_column("Count", style="green", justify="right")
    t.add_row("Seen",                     str(stats.seen))
    t.add_row("Skipped (not confirmed)",  str(stats.skipped_event))
    t.add_row("Skipped (empty text)",     str(stats.skipped_empty))
    t.add_row("Skipped (already in DB)",  str(stats.skipped_existing))
    t.add_row("Embedded",                 str(stats.embedded))
    t.add_row("Errors",                   str(stats.errors))
    console.print(t)

    db_stats = store.stats()
    console.print(f"\n[bold]Store now has {db_stats.total} records "
                  f"({db_stats.dim}-dim, model={db_stats.embed_model!r}).[/]")
    store.close()


@feedback.command("embed-stats")
@click.option("--output", type=click.Path(), default=None)
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
def feedback_embed_stats(output: Optional[str], config: Optional[str]):
    """Show what's in the embedding store."""
    from ocr_router.feedback import EmbeddingStore

    db_path = _resolve_embed_db_path(output, config)
    if not db_path.exists():
        console.print(f"[yellow]No embedding store at {db_path}[/]")
        return

    store = EmbeddingStore(db_path)
    s = store.stats()
    console.print(f"[bold]Embedding store:[/] [cyan]{db_path}[/]")
    console.print(f"[bold]Total:[/] {s.total}  [bold]Dim:[/] {s.dim}  "
                  f"[bold]Model:[/] {s.embed_model}\n")

    t = Table(title="By category", box=box.ROUNDED)
    t.add_column("Category", style="cyan")
    t.add_column("Count", style="green", justify="right")
    for cat, n in list(s.by_category.items())[:20]:
        t.add_row(cat, str(n))
    console.print(t)
    store.close()


@feedback.command("search")
@click.argument("query")
@click.option("--output", type=click.Path(), default=None)
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
@click.option("--model", default=None, help="Ollama embed model (default: nomic-embed-text).")
@click.option("--host", default=None)
@click.option("--k", type=int, default=5, help="Number of neighbors to return.")
@click.option("--category", default=None,
              help="Restrict search to one category (e.g. 'Bills').")
def feedback_search(query: str, output: Optional[str], config: Optional[str],
                    model: Optional[str], host: Optional[str], k: int,
                    category: Optional[str]):
    """Find the k most-similar past decisions to a query string."""
    from ocr_router.feedback import (
        EmbeddingStore, OllamaEmbedder, OllamaUnavailable, DEFAULT_EMBED_MODEL,
    )

    db_path = _resolve_embed_db_path(output, config)
    if not db_path.exists():
        console.print(f"[yellow]No embedding store at {db_path}. "
                      "Run `feedback embed` first.[/]")
        return

    embedder = OllamaEmbedder(model=model or DEFAULT_EMBED_MODEL, host=host)
    try:
        qv = embedder.embed(query)
    except OllamaUnavailable as exc:
        console.print(f"[red]Ollama unavailable: {exc}[/]")
        return

    store = EmbeddingStore(db_path)
    neighbors = store.search(qv, k=k, category=category)
    if not neighbors:
        console.print("[yellow]No results.[/]")
        store.close()
        return

    t = Table(title=f"Top {len(neighbors)} matches for: {query!r}",
              box=box.ROUNDED, show_lines=False)
    t.add_column("Score",   style="magenta", width=6, justify="right")
    t.add_column("Category", style="green",   width=24)
    t.add_column("Issuer",   style="yellow",  width=24)
    t.add_column("File",     style="cyan",    width=40, no_wrap=True)
    t.add_column("Folder",   style="blue",    width=40)
    for n in neighbors:
        t.add_row(
            f"{n.score:.3f}",
            (n.category or "—")[:24],
            (n.issuer or "—")[:24],
            (n.final_filename or n.original_filename)[:40],
            (n.folder or "—")[:40],
        )
    console.print(t)
    store.close()


# ── Parked files (Step 5.5) ──────────────────────────────────────────────────

@feedback.group("parked")
def feedback_parked():
    """Inspect or release files you have marked as 'parked' (kept in place)."""
    pass


@feedback_parked.command("list")
@click.option("--output", type=click.Path(), default=None)
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
def feedback_parked_list(output: Optional[str], config: Optional[str]):
    """Show every file currently marked as parked."""
    path = _resolve_feedback_path(output, config)
    log = FeedbackLog(path)
    parked = log.parked_filenames()

    if not parked:
        console.print(f"[dim]No parked files in {path}[/]")
        return

    t = Table(title=f"{len(parked)} parked file(s)", box=box.ROUNDED)
    t.add_column("Filename",   style="cyan",    width=40, no_wrap=True)
    t.add_column("Parked at",  style="blue",    width=40)
    t.add_column("Since",      style="dim",     width=19)
    t.add_column("Category",   style="green",   width=18)

    for name in sorted(parked):
        rec = parked[name]
        t.add_row(
            name[:40],
            (rec.get("extra", {}).get("parked_at") or rec.get("final_folder") or "—")[:40],
            (rec.get("ts") or "—")[:19],
            (rec.get("final_category") or "—")[:18],
        )
    console.print(t)


@feedback_parked.command("unpark")
@click.argument("filename")
@click.option("--output", type=click.Path(), default=None)
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
def feedback_parked_unpark(filename: str, output: Optional[str], config: Optional[str]):
    """Release one file so it is proposed again on the next process run.

    Writes an ``unparked`` record to the log; the next run will treat the file
    as new. The file itself is not touched.
    """
    path = _resolve_feedback_path(output, config)
    log = FeedbackLog(path)
    parked = log.parked_filenames()

    if filename not in parked:
        console.print(f"[yellow]Not parked: {filename}[/]")
        if parked:
            console.print(f"[dim]Currently parked: "
                          f"{', '.join(sorted(parked)[:5])}"
                          f"{'…' if len(parked) > 5 else ''}[/]")
        return

    rec = parked[filename]
    ok = log.append(FeedbackRecord(
        event="unparked",
        ts=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds"),
        original_filename=filename,
        text_excerpt="",
        proposed_category=rec.get("final_category"),
        proposed_issuer=rec.get("final_issuer"),
        proposed_folder=rec.get("final_folder"),
        proposed_filename=rec.get("final_filename"),
        backend="manual-unpark",
        extra={"previous_parked_at": rec.get("extra", {}).get("parked_at")},
    ))
    if ok:
        console.print(f"[green]✓ Unparked {filename}[/]")
    else:
        console.print(f"[red]Failed to write unpark record[/]")


# ── LLM commands (Step 4: L4 backbone) ───────────────────────────────────────

@cli.group()
def llm():
    """Local-first LLM classifier (Ollama). Used by `process` when enabled."""
    pass


def _build_llm_classifier(config_path: Optional[str], output: Optional[str]):
    """Helper used by `llm doctor` and `llm classify`."""
    from ocr_router.feedback import (
        DEFAULT_EMBED_MODEL, EmbeddingStore, OllamaEmbedder,
    )
    from ocr_router.llm import LLMClassifier, NullBackend, OllamaBackend
    from ocr_router.llm.classifier import LLMConfig

    cfg_dict: dict = {}
    if config_path:
        try:
            cfg_dict = load_config(config_path).model_dump()
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not load config ({exc}); using defaults.[/]")

    llm_cfg = LLMConfig.from_dict(cfg_dict)

    if not llm_cfg.enabled:
        return LLMClassifier(
            backend=NullBackend("LLM disabled in config (set llm.enabled: true)"),
            config=cfg_dict,
        )

    backend = OllamaBackend(model=llm_cfg.local_model, host=llm_cfg.host)
    embedder = OllamaEmbedder(model=llm_cfg.embed_model or DEFAULT_EMBED_MODEL, host=llm_cfg.host)

    db_path = _resolve_embed_db_path(output, config_path)
    store: Optional[EmbeddingStore] = None
    if db_path.exists():
        store = EmbeddingStore(db_path)

    return LLMClassifier(
        backend=backend, embedder=embedder, store=store, config=cfg_dict,
    )


@llm.command("doctor")
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env,
              help="Config YAML path.")
@click.option("--output", type=click.Path(), default=None,
              help="Documents root (to locate the embedding DB).")
def llm_doctor(config: Optional[str], output: Optional[str]):
    """Diagnose Ollama + embedder + embedding store availability."""
    from ocr_router.feedback import (
        DEFAULT_EMBED_MODEL, EmbeddingStore, OllamaEmbedder, OllamaUnavailable,
    )
    from ocr_router.llm import OllamaBackend
    from ocr_router.llm.classifier import LLMConfig

    cfg_dict: dict = {}
    if config:
        try:
            cfg_dict = load_config(config).model_dump()
        except Exception:
            pass

    llm_cfg = LLMConfig.from_dict(cfg_dict)

    t = Table(title="LLM doctor", box=box.ROUNDED, show_lines=False)
    t.add_column("Component", style="cyan")
    t.add_column("Status",    style="white", width=10)
    t.add_column("Detail",    style="dim")

    # 1. Config flag
    t.add_row(
        "config llm.enabled",
        "[green]on[/]" if llm_cfg.enabled else "[yellow]off[/]",
        "edit routing-config.yaml → llm.enabled: true",
    )

    # 2. Chat backend
    chat = OllamaBackend(model=llm_cfg.local_model, host=llm_cfg.host)
    info = chat.info()
    t.add_row(
        "chat backend",
        "[green]ok[/]" if info.available else "[red]down[/]",
        f"{info.label}" + (f" — {info.note}" if info.note else ""),
    )

    # 3. Embedder
    embedder = OllamaEmbedder(
        model=llm_cfg.embed_model or DEFAULT_EMBED_MODEL, host=llm_cfg.host,
    )
    try:
        _ = embedder.embed("doctor")
        t.add_row("embedder", "[green]ok[/]",
                  f"local:{embedder.model}")
    except OllamaUnavailable as exc:
        t.add_row("embedder", "[red]down[/]", str(exc))

    # 4. Embedding store
    db_path = _resolve_embed_db_path(output, config)
    if db_path.exists():
        store = EmbeddingStore(db_path)
        s = store.stats()
        t.add_row(
            "embedding store",
            "[green]ok[/]" if s.total > 0 else "[yellow]empty[/]",
            f"{db_path}  —  {s.total} records, dim={s.dim}",
        )
        store.close()
    else:
        t.add_row("embedding store", "[yellow]missing[/]", str(db_path))

    console.print(t)


@llm.command("classify")
@click.option("--file", "file_path", type=click.Path(exists=True), required=True,
              help="PDF to classify.")
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
@click.option("--output", type=click.Path(), default=None,
              help="Documents root (for the embedding DB).")
def llm_classify_file(file_path: str, config: Optional[str], output: Optional[str]):
    """Run the LLM classifier on one PDF and print the verdict (no moves)."""
    from ocr_router.llm import NullBackend
    pdf = Path(file_path)

    classifier = _build_llm_classifier(config, output)
    if isinstance(classifier.backend, NullBackend):
        console.print(f"[yellow]LLM is disabled. Reason: {classifier.backend.reason}[/]")
        return

    cfg_dict: dict = {}
    if config:
        try:
            cfg_dict = load_config(config).model_dump()
        except Exception:
            pass

    # OCR if needed
    ocr_engine = OcrEngine(cfg_dict)
    text, confidence = PdfTextExtractor.extract_text_with_confidence(pdf)
    if confidence == 0.0 and ocr_engine.is_available():
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            out = Path(td) / f"{pdf.stem}_ocr.pdf"
            if ocr_engine.ocr_pdf(pdf, out):
                text, confidence = PdfTextExtractor.extract_text_with_confidence(out)

    if not text or confidence == 0.0:
        console.print("[yellow]No text could be extracted from this PDF.[/]")
        return

    console.print(f"[bold cyan]Classifying:[/] {pdf.name}")
    console.print(f"[dim]Text confidence: {confidence:.2f}, length: {len(text)} chars[/]")

    result, info = classifier.classify(text=text, filename=pdf.name)

    if result is None:
        console.print(f"[red]Classifier returned no result. Reason: {info.error}[/]")
        return

    console.print()
    console.print(f"  [bold]Category:  [/] {result.category}")
    console.print(f"  [bold]Issuer:    [/] {result.issuer or '—'}")
    console.print(f"  [bold]Confidence:[/] {result.confidence:.2f}")
    if result.reasons:
        console.print("  [bold]Reasons:[/]")
        for r in result.reasons:
            console.print(f"    • {r}")
    console.print()
    console.print(f"[dim]Backend: {info.backend}  •  {info.duration_ms} ms  •  "
                  f"few-shot: {info.fewshot_count}  •  prompt {info.prompt_chars} chars, "
                  f"completion {info.completion_chars} chars[/]")


# ── Eval command (Step 6) ────────────────────────────────────────────────────

@cli.command("eval")
@click.option("--root", type=click.Path(exists=True, file_okay=False), required=True,
              help="Organized Documents root (ground truth source).")
@click.option("--config", type=click.Path(exists=True), default=get_config_from_env)
@click.option("--output", type=click.Path(), default=None,
              help="Documents root for embedding DB (defaults to --root).")
@click.option("--sample", type=int, default=200,
              help="Number of PDFs to evaluate (stratified by category). "
                   "Use 0 for everything.")
@click.option("--only", "only_categories", multiple=True,
              help="Restrict to these top-level category dirs (repeatable).")
@click.option("--exclude", "exclude_categories", multiple=True,
              help="Skip these top-level category dirs (repeatable).")
@click.option("--llm/--no-llm", default=None,
              help="Override config llm.enabled for this eval.")
@click.option("--skip-ocr", is_flag=True,
              help="Do not OCR; skip files with no text layer.")
@click.option("--seed", type=int, default=42, help="Sampling seed (deterministic).")
@click.option("--audit", type=click.Path(), default=None,
              help="Where to write per-file JSONL audit log "
                   "(default: <output>/_feedback/eval-<timestamp>.jsonl).")
def cmd_eval(root: str, config: Optional[str], output: Optional[str],
             sample: int, only_categories: tuple[str, ...],
             exclude_categories: tuple[str, ...],
             llm: Optional[bool], skip_ocr: bool, seed: int,
             audit: Optional[str]):
    """Measure classifier accuracy against the organized Documents tree.

    For each sampled PDF the eval runs both the keyword router and (optionally)
    the LLM classifier, then compares verdicts to the ground truth inferred
    from the PDF's folder path. A per-file JSONL audit log is written so you
    can grep individual misses.

    Nothing is moved or renamed — the eval is read-only.
    """
    from ocr_router.eval import EvalRunner, sample_files

    cfg = load_config(config)
    cfg_dict = cfg.model_dump()
    root_dir = Path(root)
    output_dir = Path(output) if output else root_dir

    # Pick samples first so we can show the count up-front
    samples = sample_files(
        root_dir,
        n=None if sample == 0 else sample,
        only_categories=list(only_categories) or None,
        excluded_categories=list(exclude_categories) or None,
        seed=seed,
    )
    if not samples:
        console.print("[yellow]No PDFs matched the eval filters.[/]")
        return

    # Build the same classifiers process uses
    router = DocumentRouter(cfg_dict)
    ocr_engine = None if skip_ocr else OcrEngine(cfg_dict)
    if ocr_engine and not ocr_engine.is_available():
        console.print("[yellow]⚠ Tesseract not found; --skip-ocr behavior.[/]")
        ocr_engine = None
    llm_classifier = _maybe_build_llm_classifier(
        cfg_dict, config, output_dir, cli_override=llm,
    )

    # Default audit path: sibling of feedback log
    if audit:
        audit_path = Path(audit)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        audit_path = _resolve_feedback_path(str(output_dir), config).parent / f"eval-{ts}.jsonl"

    from ocr_router.llm.classifier import LLMConfig
    threshold = LLMConfig.from_dict(cfg_dict).confidence_threshold

    runner = EvalRunner(
        config=cfg_dict, router=router, ocr_engine=ocr_engine,
        llm_classifier=llm_classifier if llm_classifier.enabled else None,
        skip_ocr=skip_ocr or ocr_engine is None,
        confidence_threshold=threshold,
        audit_log_path=audit_path,
    )

    console.print(f"\n[bold cyan]Evaluating {len(samples)} sampled file(s) "
                  f"from {root_dir}[/]")
    console.print(f"  LLM:     [dim]"
                  f"{llm_classifier.backend.label if llm_classifier.enabled else 'disabled'}"
                  f"[/]")
    console.print(f"  Audit:   [dim]{audit_path}[/]\n")

    with Progress(transient=False) as prog:
        task = prog.add_task("Evaluating…", total=len(samples))

        def _cb(i, total, path):
            prog.update(task, completed=i,
                        description=f"[cyan]Evaluating…[/] {i}/{total} "
                                    f"[dim]{path.name[:40]}[/]")

        report = runner.evaluate(samples, progress_cb=_cb)

    _print_eval_report(report, llm_enabled=llm_classifier.enabled)


def _print_eval_report(report, *, llm_enabled: bool) -> None:
    """Render the eval report as a series of Rich tables."""
    # Headline accuracy
    t = Table(title="Overall accuracy", box=box.ROUNDED)
    t.add_column("Backend",   style="cyan")
    t.add_column("Correct",   style="green", justify="right")
    t.add_column("Total",     style="dim",   justify="right")
    t.add_column("Accuracy",  style="bold",  justify="right")
    t.add_row(
        "Keyword",
        str(report.keyword_correct), str(report.n_evaluated),
        f"{report.keyword_accuracy * 100:.1f}%",
    )
    if llm_enabled:
        t.add_row(
            "LLM (when attempted)",
            str(report.llm_correct), str(report.llm_attempted),
            f"{report.llm_accuracy * 100:.1f}%",
        )
        t.add_row(
            "Hybrid (Step 5 rule)",
            str(report.hybrid_correct), str(report.n_evaluated),
            f"{report.hybrid_accuracy * 100:.1f}%",
        )
    console.print(t)

    if report.n_skipped:
        reasons = ", ".join(f"{k}: {v}" for k, v in report.skipped_reasons.items())
        console.print(f"[dim]Skipped {report.n_skipped} file(s) — {reasons}[/]")

    if llm_enabled and report.llm_attempted:
        console.print(f"[dim]Avg LLM latency: {report.avg_llm_ms:.0f} ms "
                      f"({report.total_llm_ms / 1000:.1f} s total)[/]\n")

    # Per-category breakdown
    if report.by_category:
        t = Table(title="Accuracy by category", box=box.ROUNDED, show_lines=False)
        t.add_column("Category", style="cyan")
        t.add_column("N",       style="dim",   justify="right", width=5)
        t.add_column("kw",      style="green", justify="right", width=12)
        if llm_enabled:
            t.add_column("LLM",    style="green", justify="right", width=12)
            t.add_column("Hybrid", style="bold green", justify="right", width=12)
        for cat in sorted(report.by_category, key=lambda c: -report.by_category[c].truth_count):
            s = report.by_category[cat]
            row = [
                cat[:36],
                str(s.truth_count),
                f"{s.keyword_correct}/{s.truth_count} "
                f"({(s.keyword_correct / s.truth_count * 100 if s.truth_count else 0):.0f}%)",
            ]
            if llm_enabled:
                row.append(
                    f"{s.llm_correct}/{s.truth_count} "
                    f"({(s.llm_correct / s.truth_count * 100 if s.truth_count else 0):.0f}%)"
                )
                row.append(
                    f"{s.hybrid_correct}/{s.truth_count} "
                    f"({(s.hybrid_correct / s.truth_count * 100 if s.truth_count else 0):.0f}%)"
                )
            t.add_row(*row)
        console.print(t)

    # Where LLM helped / hurt
    if llm_enabled and report.llm_helped:
        t = Table(title=f"LLM helped ({len(report.llm_helped)} files)",
                  box=box.ROUNDED, show_lines=False)
        t.add_column("Truth",      style="green", width=22)
        t.add_column("kw said",    style="yellow", width=22)
        t.add_column("LLM said",   style="bold green", width=22)
        t.add_column("File",       style="cyan", width=32, no_wrap=True)
        for r in report.llm_helped[:15]:
            t.add_row(
                r.truth_category[:22], (r.keyword_category or "—")[:22],
                f"{r.llm_category} ({r.llm_confidence:.2f})"[:22],
                r.path.name[:32],
            )
        console.print(t)
        if len(report.llm_helped) > 15:
            console.print(f"[dim]… and {len(report.llm_helped) - 15} more.[/]")

    if llm_enabled and report.llm_hurt:
        t = Table(title=f"LLM hurt ({len(report.llm_hurt)} files)",
                  box=box.ROUNDED, show_lines=False)
        t.add_column("Truth",     style="green", width=22)
        t.add_column("kw said",   style="bold green", width=22)
        t.add_column("LLM said",  style="red", width=22)
        t.add_column("File",      style="cyan", width=32, no_wrap=True)
        for r in report.llm_hurt[:15]:
            t.add_row(
                r.truth_category[:22], (r.keyword_category or "—")[:22],
                f"{r.llm_category} ({r.llm_confidence:.2f})"[:22],
                r.path.name[:32],
            )
        console.print(t)
        if len(report.llm_hurt) > 15:
            console.print(f"[dim]… and {len(report.llm_hurt) - 15} more.[/]")


if __name__ == '__main__':
    cli()
