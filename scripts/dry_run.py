"""
Dry-run: process PDFs from __downloads__ and show predicted rename + route
without touching any actual files.

Automatically OCRs scanned PDFs (no text layer) silently via ocrmypdf+Tesseract
before analysis. OCR output files are written to __downloads__ as {stem}_ocr.pdf
and cleaned up after the run.

Usage:
    python scripts/dry_run.py [--verbose]
"""

import re
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from ocr_router.config import load_config
from ocr_router.extractor import MetadataExtractor, PdfTextExtractor
from ocr_router.folder_resolver import FolderResolver
from ocr_router.ocr_engine import OcrEngine
from ocr_router.router import DocumentRouter

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = WORKSPACE_ROOT / 'config' / 'routing-config.local.yaml'
if not DEFAULT_CONFIG.exists():
    DEFAULT_CONFIG = WORKSPACE_ROOT / 'config' / 'routing-config.yaml'

CONFIG_PATH = Path(os.getenv('OCR_CONFIG_PATH', str(DEFAULT_CONFIG)))
OUTPUT_ROOT = Path(os.getenv('OCR_DOCS_OUTPUT_ROOT', str(Path.home() / 'Documents')))
DOWNLOADS_DIR = Path(os.getenv('OCR_DOCS_DOWNLOADS_DIR', str(OUTPUT_ROOT / '__downloads__')))

# Already-renamed files (ground truth) — skip processing, only count
ALREADY_RENAMED = {f.name for f in DOWNLOADS_DIR.glob('*.pdf')
                   if re.match(r'^\d{4}\.\d{2}', f.name)}
RAW_FILES = [f for f in sorted(DOWNLOADS_DIR.glob('*.pdf'))
             if f.name not in ALREADY_RENAMED
             and not f.name.endswith('_ocr.pdf')]   # skip leftover OCR artefacts

VERBOSE = '--verbose' in sys.argv

STATUS_ICONS = {
    'exact':   '📁',
    'created': '📂✨',
    'flat':    '📂~',
    'suggest': '⚠ ',
}


def fmt(label: str, value: str, ok: bool = True) -> None:
    icon = '✓' if ok else '✗'
    print(f"  {icon} {label:<20} {value}")


def process_file(
    pdf: Path,
    extractor: MetadataExtractor,
    router: DocumentRouter,
    resolver: FolderResolver,
    ocr_engine: OcrEngine,
    ocr_tmp_dir: Path,
) -> dict:
    print(f"\n{'='*72}")
    print(f"  FILE: {pdf.name}")
    print(f"{'='*72}")

    text, confidence = PdfTextExtractor.extract_text_with_confidence(pdf)

    # Auto-OCR when no text layer detected
    if confidence == 0.0:
        if ocr_engine.is_available():
            print(f"  🔍 No text layer — running silent OCR...")
            ocr_out = ocr_tmp_dir / f"{pdf.stem}_ocr.pdf"
            ok = ocr_engine.ocr_pdf(pdf, ocr_out)
            if ok and ocr_out.exists():
                text, confidence = PdfTextExtractor.extract_text_with_confidence(ocr_out)
                print(f"  ✓ OCR complete (confidence={confidence:.2f})")
            else:
                print(f"  ✗ OCR failed — skipping")
                return {'status': 'ocr_failed'}
        else:
            print(f"  ⚠  No text extracted — Tesseract unavailable")
            return {'status': 'needs_ocr'}

    if VERBOSE:
        print(f"  TEXT: {text[:500].replace(chr(10), ' ')!r}")

    if not text.strip():
        print(f"  ⚠  Empty text after OCR — skipping")
        return {'status': 'empty'}

    metadata = extractor.extract_from_text(text, pdf.name)
    category = router.classify_document(text)
    metadata['category'] = category

    route = router.build_route_path(category, metadata)
    new_name = router.normalize_filename(pdf.name, metadata)

    dest_dir, folder_status = resolver.resolve(route)
    full_dest = dest_dir / new_name

    # Display
    fmt('Category', category, category != 'Uncategorized')
    fmt('Date', metadata.get('date') or '—', bool(metadata.get('date')))
    fmt('Issuer', metadata.get('issuer') or '—', bool(metadata.get('issuer')))

    acct = metadata.get('account') or ''
    if acct:
        if metadata.get('account_masked'):
            acct_display = f"(Last{metadata.get('account_digits', 4)} {acct})"
        else:
            acct_display = f"({acct})"
    else:
        acct_display = '—'
    fmt('Account', acct_display)
    currency = metadata.get('currency', '$')
    amt = metadata.get('amount')
    fmt('Amount', f"{currency}{amt}" if amt else '—', bool(amt))
    fmt('Confidence', f"{confidence:.2f}", confidence > 0.10)

    icon = STATUS_ICONS.get(folder_status, '?')
    flat_note = ' (flat folder — no year subfolder)' if folder_status == 'flat' else ''
    print()
    print(f"  OLD NAME:  {pdf.name}")
    print(f"  NEW NAME:  {new_name}")
    print(f"  FOLDER:    {icon} [{folder_status.upper()}]{flat_note}")
    print(f"  FULL PATH: {full_dest}")

    if folder_status == 'suggest':
        print(f"  {resolver.suggest_message(route)}")

    return {'status': folder_status, 'category': category}


def main():
    cfg = load_config(CONFIG_PATH)
    extractor = MetadataExtractor(cfg.model_dump())
    router = DocumentRouter(cfg.model_dump())
    resolver = FolderResolver(OUTPUT_ROOT)
    ocr_engine = OcrEngine(cfg.model_dump())

    # Temp dir for OCR output files (cleaned up after run)
    ocr_tmp_dir = DOWNLOADS_DIR / '_ocr_tmp'
    ocr_tmp_dir.mkdir(exist_ok=True)

    print(f"\n{'#'*72}")
    print(f"  DRY RUN — {len(RAW_FILES)} raw PDFs  |  {len(ALREADY_RENAMED)} already named")
    if ocr_engine.is_available():
        print(f"  OCR: silent (ocrmypdf + Tesseract)")
    else:
        print(f"  OCR: unavailable")
    print(f"{'#'*72}")

    stats: dict[str, int] = {}
    for pdf in RAW_FILES:
        r = process_file(pdf, extractor, router, resolver, ocr_engine, ocr_tmp_dir)
        key = r.get('status', 'unknown')
        stats[key] = stats.get(key, 0) + 1

    # Clean up OCR temp files
    import shutil
    shutil.rmtree(ocr_tmp_dir, ignore_errors=True)

    print(f"\n{'#'*72}")
    print("  SUMMARY (no files were modified)")
    for k, v in sorted(stats.items()):
        print(f"    {STATUS_ICONS.get(k, '?')} {k:<12} {v}")
    print(f"{'#'*72}\n")


if __name__ == '__main__':
    main()

