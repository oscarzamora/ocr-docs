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
def process(input: str, output: str, config: str, max_files: int,
            skip_ocr: bool, archive: bool, dry_run: bool, interactive: bool):
    """Process PDFs: OCR → extract → classify → rename → route.

    By default shows a full review table before moving any file.
    Use --no-interactive to skip confirmation (batch / cron mode).
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

        if not skip_ocr and not ocr_engine.is_available():
            console.print(f"[yellow]⚠  PDF24 not found — proceeding text-only (--skip-ocr)[/]")
            skip_ocr = True

        pdf_files = list(input_dir.rglob('*.pdf'))
        if not pdf_files:
            console.print("[yellow]No PDFs found.[/]")
            return
        if max_files:
            pdf_files = pdf_files[:max_files]

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

                # OCR only when there's truly no text layer
                if confidence == 0.0 and not skip_ocr:
                    ocr_tmp_dir.mkdir(parents=True, exist_ok=True)
                    ocr_out = ocr_tmp_dir / f"{pdf_file.stem}_ocr.pdf"
                    ok = ocr_engine.ocr_pdf(pdf_file, ocr_out)
                    if ok and ocr_out.exists():
                        text, confidence = PdfTextExtractor.extract_text_with_confidence(ocr_out)
                        pdf_to_extract = ocr_out
                    else:
                        issues.append("OCR failed")

                if confidence == 0.0:
                    skipped_ocr.append(pdf_file)
                    continue

                metadata = extractor.extract_from_text(text, pdf_file.name)
                category = router.classify_document(text)
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
            approved_indices = _interactive_confirm(proposals, config)
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
    t = Table(
        title=f"\n[bold]Proposed moves — {len(proposals)} file(s)[/]",
        box=box.ROUNDED,
        show_lines=True,
        highlight=True,
    )
    t.add_column("#",             style="dim",     width=3,  no_wrap=True)
    t.add_column("Original File", style="cyan",    width=30, no_wrap=True)
    t.add_column("Category",      style="green",   width=24, no_wrap=True)
    t.add_column("Issuer",        style="yellow",  width=18, no_wrap=True)
    t.add_column("New Name",      style="white",   width=40, no_wrap=True)
    t.add_column("Amount",        style="magenta", width=10, no_wrap=True)
    t.add_column("Destination",   style="blue",    width=35)

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

        t.add_row(
            str(p.index),
            p.pdf_file.name[:30],
            p.category,
            issuer[:18],
            p.new_filename[:40],
            amt_str,
            dest + (f'  [dim]{notes}[/]' if notes else ''),
        )

    console.print(t)


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


def _interactive_confirm(proposals: list[Proposal], config_path: str) -> Optional[set[int]]:
    """Ask which files to move, collect rules for skipped ones.

    Returns a set of approved indices, or None if the user quits.
    """
    console.print(Panel(
        "[bold]Review the table above.[/]\n\n"
        "  [green]Enter[/]          → move ALL files\n"
        "  [yellow]1,3,5[/]          → move ONLY those numbers\n"
        "  [yellow]skip 2,4[/]       → move all EXCEPT those numbers\n"
        "  [red]q[/]              → quit without moving anything",
        title="What would you like to do?",
        border_style="cyan",
    ))

    raw = console.input("[bold cyan]> [/]").strip()

    if raw.lower() == 'q':
        return None

    all_indices = {p.index for p in proposals}

    if raw == '':
        approved = all_indices
    elif raw.lower().startswith('skip '):
        nums = _parse_nums(raw[5:])
        approved = all_indices - nums
    else:
        approved = _parse_nums(raw)

    skipped = all_indices - approved
    if skipped:
        console.print(f"\n[yellow]Skipping #{', '.join(str(n) for n in sorted(skipped))}[/]")
        _collect_rules_for_skipped(
            [p for p in proposals if p.index in skipped],
            config_path,
        )

    return approved


def _collect_rules_for_skipped(skipped: list[Proposal], config_path: str) -> None:
    """For each skipped file, optionally collect a new rule and append it to config."""
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

        elif rule.lower().startswith('category='):
            val = rule.split('=', 1)[1].strip()
            # Add the original filename stem as a keyword for that category
            kw = Path(p.pdf_file.name).stem.lower().replace('-', ' ').replace('_', ' ')
            new_keywords.setdefault(val, []).append(kw)
            console.print(f"  [green]✓ Will add keyword {kw!r} to category {val!r}[/]")

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
    """Append processed files to PROCESSED_PDFS.md, grouped by date.

    Format:
        ## 2026-05-17

        ### 21:30 — 5 files moved

        | # | Original File | Category | Issuer | New Name | Amount | Destination |
        |...|...|
    """
    cfg_path = (config or {}).get('history', {}).get('path')
    history_file = Path(cfg_path) if cfg_path else output_dir / 'PROCESSED_PDFS.md'
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
        lines += [
            '# Processed PDFs\n\n',
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


if __name__ == '__main__':
    cli()
