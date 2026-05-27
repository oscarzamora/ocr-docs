"""Bootstrap the feedback log from existing processed documents.

Replays past decisions into the L1 feedback JSONL so that downstream layers
(L3 vector store, L4 LLM few-shot) have data to learn from on day one.

Two modes are supported:

1. **History-based** (``bootstrap_from_downloads``):
   Parse ``PROCESSED_PDFS.md`` to learn what each filename was previously
   classified as, then walk ``--source`` and emit ``confirmed`` records.
   Best when the per-day pipe-table history is intact.

2. **Tree-based** (``bootstrap_from_tree``):
   Walk an organized Documents tree and infer ``(category, issuer, year)``
   from the folder layout (``<root>/<category>/<issuer>/<year>/<file>``).
   Best when history is lost but the filed documents themselves survive.

In both modes:
- Full OCR is invoked only when the PDF has no text layer.
- The log is idempotent on filename unless ``--force`` is passed.
- Per-file failures never abort the run.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ocr_router.extractor import PdfTextExtractor
from ocr_router.feedback.log import FeedbackLog, FeedbackRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# History parsing (mode 1)
# ---------------------------------------------------------------------------

@dataclass
class HistoryEntry:
    """One row from ``PROCESSED_PDFS.md``."""
    original_filename: str
    category: str
    issuer: Optional[str]
    new_filename: str
    destination: str            # relative for 'moved', absolute for 'renamed'
    amount: Optional[str]
    day: str                    # YYYY-MM-DD from the section heading
    action_mode: str            # 'move' | 'rename'


def _md_unescape(s: str) -> str:
    """Reverse the ``\\|`` escaping done by cli._md_escape."""
    return s.replace("\\|", "|").strip()


# Day headings we accept (in descending specificity):
#   ## 2026-05-15
#   ## 2026-05-15 — 25 files processed
#   ## 2026-05-25 (session 1) — 16 files processed
#   ## Session 3 — 2026-05-25
_DAY_PATTERNS = [
    re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"^##\s+.*?(\d{4}-\d{2}-\d{2})"),
]

# Run sub-heading (legacy format that explicitly states action mode).
# Modern logs frequently omit this — we default to action_mode='move' when absent.
_RUN_RE = re.compile(
    r"^###\s+\d{1,2}:\d{2}\s+[—-]\s+\d+\s+file\(s\)\s+(moved|renamed in place)\s*$"
)

# Skip these section types — they are auxiliary tables (different schema)
_SKIP_SECTION_RE = re.compile(
    r"^###\s+(Duplicates deleted|Renamed in destination|Skipped|Pending iteration)",
    re.IGNORECASE,
)

# Markdown table separator row, e.g. `| --- | --- | ... |`
_SEP_RE = re.compile(r"^\|\s*-+\s*(\|\s*-+\s*)+\|\s*$")

# Cells that mean "no value" in the log.
_EMPTY_CELLS = {"—", "-", "–", "", "same", "—"}


def parse_processed_history(path: Path) -> list[HistoryEntry]:
    """Parse one ``PROCESSED_PDFS.md`` file into a flat list of entries.

    Accepts the evolving real-world format:
      - Day headings with optional trailing text
      - Optional ``### HH:MM — N file(s) (moved|renamed in place)`` sub-heading
      - Skips ``### Duplicates deleted`` / ``### Renamed in destination`` /
        ``### Skipped`` blocks (different schemas; not bootstrap-able)
      - Only parses 7-column rows where the first cell is an integer
      - Treats common "empty" markers (``—``, ``-``, ``same``) as None
      - Skips rows whose destination contains ``DELETED``
    """
    if not path.exists():
        return []

    entries: list[HistoryEntry] = []
    current_day = ""
    current_action = "move"
    in_skip_block = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()

        # Day heading
        for pat in _DAY_PATTERNS:
            m = pat.match(line)
            if m:
                current_day = m.group(1)
                current_action = "move"
                in_skip_block = False
                break
        else:
            # Run sub-heading: action mode
            if line.startswith("###"):
                if _SKIP_SECTION_RE.match(line):
                    in_skip_block = True
                    continue
                in_skip_block = False
                m = _RUN_RE.match(line)
                if m:
                    current_action = "rename" if m.group(1) == "renamed in place" else "move"
                else:
                    # New run within same day — reset to move by default
                    current_action = "move"
                continue

        if in_skip_block:
            continue
        if not line.startswith("|"):
            continue
        if _SEP_RE.match(line):
            continue

        # Split on '|' respecting the ``\|`` escape.
        tmp = line.replace("\\|", "\u0001")
        cells = [c.strip().replace("\u0001", "|") for c in tmp.strip("|").split("|")]

        if len(cells) < 7 or not cells[0].lstrip("-").isdigit():
            continue

        try:
            _idx, original, category, issuer, new_name, amount, destination = cells[:7]
            destination_clean = _md_unescape(destination)
            # Skip deletions and obvious non-files
            if any(tok in destination_clean.upper() for tok in ("DELETED", "DUPLICATE")):
                continue
            new_name_clean = _md_unescape(new_name)
            original_clean = _md_unescape(original)
            # Strip annotations like " (OCR)" from filenames
            original_clean = re.sub(r"\s*\([^)]+\)\s*$", "", original_clean).strip()
            # "same" means new_name == original
            if new_name_clean.lower() in _EMPTY_CELLS:
                new_name_clean = original_clean

            entries.append(HistoryEntry(
                original_filename=original_clean,
                category=_md_unescape(category),
                issuer=None if issuer.strip() in _EMPTY_CELLS else _md_unescape(issuer),
                new_filename=new_name_clean,
                destination=destination_clean,
                amount=None if amount.strip() in _EMPTY_CELLS else _md_unescape(amount),
                day=current_day,
                action_mode=current_action,
            ))
        except Exception as exc:                                  # pragma: no cover
            logger.warning("Skipping malformed history row: %s (%s)", line, exc)

    return entries


def parse_processed_history_paths(paths_or_dir: Path | list[Path]) -> list[HistoryEntry]:
    """Parse one file or every ``*PROCESSED_PDFS*.md`` under a directory.

    Useful because the live log file is named e.g. ``2026.05 - PROCESSED_PDFS.md``
    and you may want to ingest several monthly files in one pass.
    """
    if isinstance(paths_or_dir, Path) and paths_or_dir.is_dir():
        files = sorted(paths_or_dir.glob("*PROCESSED_PDFS*.md"))
    elif isinstance(paths_or_dir, Path):
        files = [paths_or_dir]
    else:
        files = list(paths_or_dir)

    all_entries: list[HistoryEntry] = []
    for f in files:
        all_entries.extend(parse_processed_history(f))
    return all_entries


def index_history(entries: Iterable[HistoryEntry]) -> dict[str, HistoryEntry]:
    """Build a {original_filename: most_recent_entry} index.

    If the same filename appears twice (rare — re-processed), keep the *latest*
    entry by day, since that reflects the most recent human decision.
    """
    out: dict[str, HistoryEntry] = {}
    for e in entries:
        prev = out.get(e.original_filename)
        if prev is None or e.day >= prev.day:
            out[e.original_filename] = e
    return out


# ---------------------------------------------------------------------------
# Bootstrap orchestrator
# ---------------------------------------------------------------------------

@dataclass
class BootstrapStats:
    scanned: int = 0
    matched: int = 0          # PDFs whose filename was found in history
    appended: int = 0         # records actually written
    already_logged: int = 0   # skipped due to existing entry in feedback log
    no_history_match: int = 0
    text_extracted: int = 0   # had a usable text layer
    ocr_run: int = 0
    ocr_failed: int = 0
    errors: list[str] = field(default_factory=list)


def _existing_filenames(log: FeedbackLog) -> set[str]:
    """Return the set of original filenames already present in the log."""
    return {r.get("original_filename", "") for r in log.iter_records()}


def bootstrap_from_downloads(
    source_dir: Path,
    history_path: Path,
    feedback_log: FeedbackLog,
    *,
    ocr_engine=None,
    text_excerpt_chars: int = 2000,
    force: bool = False,
    include_unprocessed: bool = False,
    skip_ocr: bool = False,
    progress_cb=None,
) -> BootstrapStats:
    """Replay processed files in ``source_dir`` into the feedback log.

    Args:
        source_dir: folder of PDFs to scan (e.g. ``__downloads__``).
        history_path: ``PROCESSED_PDFS.md`` to parse for ground truth.
        feedback_log: target log (entries appended here).
        ocr_engine: optional ``OcrEngine`` for files with no text layer.
                    If None, those files are skipped.
        force: if False, skip filenames already present in the log.
        include_unprocessed: if True, also record files with no history match
                             as ``pending`` entries (no final_*).
        skip_ocr: if True, never invoke OCR even when text layer is missing.
        progress_cb: optional callable(file_index, total, current_path) for UI.

    Returns ``BootstrapStats`` summary.
    """
    stats = BootstrapStats()

    entries = parse_processed_history(history_path)
    index = index_history(entries)
    already = set() if force else _existing_filenames(feedback_log)

    pdf_files = sorted(source_dir.rglob("*.pdf"))
    total = len(pdf_files)

    with tempfile.TemporaryDirectory(prefix="ocr_bootstrap_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for i, pdf in enumerate(pdf_files, 1):
            stats.scanned += 1
            if progress_cb:
                try:
                    progress_cb(i, total, pdf)
                except Exception:                                  # pragma: no cover
                    pass

            try:
                if pdf.name in already:
                    stats.already_logged += 1
                    continue

                hist = index.get(pdf.name)
                if hist is None and not include_unprocessed:
                    stats.no_history_match += 1
                    continue
                if hist is not None:
                    stats.matched += 1

                # --- extract text ----------------------------------------
                text, confidence = PdfTextExtractor.extract_text_with_confidence(pdf)

                if confidence == 0.0 and not skip_ocr and ocr_engine is not None:
                    ocr_out = tmp_dir / f"{pdf.stem}_ocr.pdf"
                    if ocr_engine.ocr_pdf(pdf, ocr_out):
                        text, confidence = PdfTextExtractor.extract_text_with_confidence(ocr_out)
                        stats.ocr_run += 1
                        try:
                            ocr_out.unlink(missing_ok=True)
                        except Exception:                          # pragma: no cover
                            pass
                    else:
                        stats.ocr_failed += 1
                elif confidence > 0.0:
                    stats.text_extracted += 1

                # --- build record ----------------------------------------
                if hist is not None:
                    rec = FeedbackRecord.from_proposal(
                        event="confirmed",
                        original_filename=pdf.name,
                        text=text or "",
                        proposal_meta={
                            "category": hist.category,
                            "issuer": hist.issuer,
                        },
                        proposed_folder=str(Path(hist.destination).parent)
                                        if hist.action_mode == "move"
                                        else None,
                        proposed_filename=hist.new_filename,
                        proposed_confidence=confidence,
                        final_category=hist.category,
                        final_issuer=hist.issuer,
                        final_folder=str(Path(hist.destination).parent)
                                     if hist.action_mode == "move"
                                     else None,
                        final_filename=hist.new_filename,
                        backend="bootstrap",
                        extra={
                            "source": "bootstrap",
                            "history_day": hist.day,
                            "action_mode": hist.action_mode,
                            "amount": hist.amount,
                        },
                        text_excerpt_chars=text_excerpt_chars,
                    )
                else:
                    # include_unprocessed path — record what we know without a final
                    rec = FeedbackRecord.from_proposal(
                        event="pending",
                        original_filename=pdf.name,
                        text=text or "",
                        proposal_meta={},
                        proposed_folder=None,
                        proposed_filename=None,
                        proposed_confidence=confidence,
                        backend="bootstrap",
                        extra={"source": "bootstrap", "reason": "no_history_match"},
                        text_excerpt_chars=text_excerpt_chars,
                    )

                if feedback_log.append(rec):
                    stats.appended += 1

            except Exception as exc:
                msg = f"{pdf.name}: {exc}"
                stats.errors.append(msg)
                logger.warning("bootstrap error %s", msg)

    return stats


__all__ = [
    "HistoryEntry",
    "BootstrapStats",
    "parse_processed_history",
    "parse_processed_history_paths",
    "index_history",
    "bootstrap_from_downloads",
    "bootstrap_from_tree",
    "infer_label_from_path",
]


# ---------------------------------------------------------------------------
# Tree-based bootstrap (mode 2)
# ---------------------------------------------------------------------------

# Folders we never descend into when scanning an organized Documents tree.
DEFAULT_TREE_EXCLUDES = {
    "__downloads__",
    "_processed-originals",
    "_feedback",
    ".git",
    ".vscode",
    "OneNote Notebooks",
    "Custom Office Templates",
    "node_modules",
    "venv",
    "env",
    ".venv",
}

# Path components that should be skipped when inferring an issuer name
# (administrative containers, not real issuer dirs).
_NON_ISSUER_COMPONENTS = {
    "_ Closed Accounts _",
    "Closed Accounts",
    "Forms",
}

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


@dataclass
class InferredLabel:
    """Label inferred from a file's path under a Documents root."""
    category: Optional[str]
    issuer: Optional[str]
    year: Optional[str]
    final_folder: str       # path of the parent dir, relative to root
    final_filename: str     # the file name as currently on disk


def infer_label_from_path(pdf_path: Path, root: Path) -> InferredLabel:
    """Infer (category, issuer, year) from a PDF's location under ``root``.

    Rules (best-effort, permissive — works across the real folder taxonomy):
      - Category   = first path component under ``root``.
      - Year       = the deepest 4-digit component matching 19xx/20xx (if any).
      - Issuer     = the *deepest* non-year, non-admin, non-category component
                     between category and the year (or, if no year, between
                     category and the file). Returns None if the file sits
                     directly under category.

    Example::

        root  = C:\\Users\\me\\Documents
        path  = root / "Bills" / "FPL" / "2026" / "2026.04 - FPL.pdf"
        →   category="Bills", issuer="FPL", year="2026"

        path  = root / "Paystubs" / "2026" / "...pdf"
        →   category="Paystubs", issuer=None, year="2026"

        path  = root / "Bank Account & Statements" / "_ Closed Accounts _" /
                "Old Bank" / "2018" / "...pdf"
        →   category="Bank Account & Statements", issuer="Old Bank", year="2018"
    """
    try:
        rel = pdf_path.relative_to(root)
    except ValueError:
        return InferredLabel(None, None, None, str(pdf_path.parent), pdf_path.name)

    parts = list(rel.parts)
    if not parts:
        return InferredLabel(None, None, None, "", pdf_path.name)

    # Drop the filename itself
    dir_parts = parts[:-1]
    fname = parts[-1]

    if not dir_parts:
        return InferredLabel(None, None, None, "", fname)

    category = dir_parts[0]
    middle = dir_parts[1:]    # everything between category and the file

    # Year = last component matching YYYY
    year = None
    for p in reversed(middle):
        if _YEAR_RE.match(p):
            year = p
            break

    # Issuer = deepest non-year, non-admin component in `middle`
    issuer = None
    for p in reversed(middle):
        if _YEAR_RE.match(p):
            continue
        if p in _NON_ISSUER_COMPONENTS:
            continue
        issuer = p
        break

    final_folder = str(Path(*dir_parts))
    return InferredLabel(
        category=category,
        issuer=issuer,
        year=year,
        final_folder=final_folder,
        final_filename=fname,
    )


def bootstrap_from_tree(
    root: Path,
    feedback_log: FeedbackLog,
    *,
    ocr_engine=None,
    text_excerpt_chars: int = 2000,
    force: bool = False,
    skip_ocr: bool = False,
    excluded_dirs: Optional[Iterable[str]] = None,
    excluded_categories: Optional[Iterable[str]] = None,
    only_categories: Optional[Iterable[str]] = None,
    max_files: Optional[int] = None,
    progress_cb=None,
) -> BootstrapStats:
    """Walk an organized Documents tree and append a ``confirmed`` record per PDF.

    Each record's category/issuer/year are inferred from the folder layout
    (see :func:`infer_label_from_path`). The current filename is treated as
    the final (human-approved) filename.

    Args:
        root: Documents root (e.g. ``C:\\Users\\me\\OneDrive\\Documents``).
        feedback_log: log to append to.
        ocr_engine: optional OcrEngine for PDFs with no text layer.
        force: re-import filenames already present in the log.
        skip_ocr: never invoke OCR (only record what pypdf can read).
        excluded_dirs: top-level dir names to skip entirely
                       (defaults to DEFAULT_TREE_EXCLUDES).
        excluded_categories: category names (top-level dirs) to skip.
        only_categories: if set, *only* these top-level dirs are scanned.
        max_files: cap on the number of files to process (None = no cap).
        progress_cb: callable(i, total, current_path) for UI updates.
    """
    excludes = set(excluded_dirs) if excluded_dirs is not None else set(DEFAULT_TREE_EXCLUDES)
    exclude_cats = set(excluded_categories or ())
    only_cats = set(only_categories) if only_categories else None

    stats = BootstrapStats()
    already = set() if force else _existing_filenames(feedback_log)

    # Pre-walk to compute total for the progress bar, honouring excludes.
    def _iter_pdfs():
        # Iterate only top-level dirs that pass filters
        for top in sorted(p for p in root.iterdir() if p.is_dir()):
            if top.name in excludes:
                continue
            if top.name in exclude_cats:
                continue
            if only_cats is not None and top.name not in only_cats:
                continue
            for pdf in top.rglob("*.pdf"):
                # Skip if any path component is excluded
                if any(part in excludes for part in pdf.parts):
                    continue
                yield pdf

    pdfs = list(_iter_pdfs())
    if max_files is not None:
        pdfs = pdfs[:max_files]
    total = len(pdfs)

    with tempfile.TemporaryDirectory(prefix="ocr_bootstrap_tree_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for i, pdf in enumerate(pdfs, 1):
            stats.scanned += 1
            if progress_cb:
                try:
                    progress_cb(i, total, pdf)
                except Exception:                                  # pragma: no cover
                    pass

            try:
                if pdf.name in already:
                    stats.already_logged += 1
                    continue

                label = infer_label_from_path(pdf, root)
                if not label.category:
                    stats.no_history_match += 1
                    continue
                stats.matched += 1

                # --- extract text ----------------------------------------
                text, confidence = PdfTextExtractor.extract_text_with_confidence(pdf)

                if confidence == 0.0 and not skip_ocr and ocr_engine is not None:
                    ocr_out = tmp_dir / f"{pdf.stem}_ocr.pdf"
                    if ocr_engine.ocr_pdf(pdf, ocr_out):
                        text, confidence = PdfTextExtractor.extract_text_with_confidence(ocr_out)
                        stats.ocr_run += 1
                        try:
                            ocr_out.unlink(missing_ok=True)
                        except Exception:                          # pragma: no cover
                            pass
                    else:
                        stats.ocr_failed += 1
                elif confidence > 0.0:
                    stats.text_extracted += 1

                rec = FeedbackRecord.from_proposal(
                    event="confirmed",
                    original_filename=pdf.name,
                    text=text or "",
                    proposal_meta={
                        "category": label.category,
                        "issuer": label.issuer,
                    },
                    proposed_folder=label.final_folder,
                    proposed_filename=label.final_filename,
                    proposed_confidence=confidence,
                    final_category=label.category,
                    final_issuer=label.issuer,
                    final_folder=label.final_folder,
                    final_filename=label.final_filename,
                    backend="bootstrap-tree",
                    extra={
                        "source": "bootstrap-tree",
                        "root": str(root),
                        "year": label.year,
                    },
                    text_excerpt_chars=text_excerpt_chars,
                )

                if feedback_log.append(rec):
                    stats.appended += 1

            except Exception as exc:
                msg = f"{pdf.name}: {exc}"
                stats.errors.append(msg)
                logger.warning("bootstrap_from_tree error %s", msg)

    return stats
