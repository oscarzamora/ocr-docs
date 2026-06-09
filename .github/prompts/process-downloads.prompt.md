---
mode: agent
description: Scan __downloads__, OCR/compress PDFs and convert JPEGs to PDF, then recommend rename + destination folder per the established taxonomy. Append results to PROCESSED_PDFS.md.
---

# Process new files in `__downloads__`

> **All routing rules, naming conventions, and filing edge cases live in
> `config/routing-config.local.yaml` → read it before making any proposals.**
> The YAML sections that drive behaviour: `route_templates`, `doc_types`,
> `monthly_categories`, `description_from_filename_categories`, `known_issuers`,
> `no_amount_categories`, and `filing_notes`.

---

## Step 0 — List candidates (ask before acting)

Scan the staging folder for files not already in the current month's
`YYYY.MM - PROCESSED_PDFS.md` ledger:

- **PDFs** — all `.pdf` files not in ledger. Skip `_processed-originals/`,
  `YYYY.MM - PROCESSED_PDFS.md`, `PROCESSED_PDFS.md`, `desktop.ini`.
- **JPEGs** — all `.jpg` / `.jpeg` files not in ledger (same exclusions).
- Ignore other extensions unless the user explicitly asks.

Present two separate lists before doing anything else:

1. `📄 PDFs to process (N)` — list filenames
2. `🖼 JPEGs found (N)` — list filenames, then ask:
   *"¿Cuáles proceso? (all / numbers / none)"*

Wait for the user's JPEG answer. For each **declined** JPEG, log it immediately
in `YYYY.MM - PROCESSED_PDFS.md` as `skipped` so it is never surfaced again.

---

## Step 1 — Process PDFs

For every confirmed PDF:

1. **Check OCR readiness** — call `PdfTextExtractor.extract_text_with_confidence()`:
   - `conf > 0` → already searchable; skip OCR entirely. Mark `(text-ready)`.
   - `conf == 0` → run `ocrmypdf --optimize 3 --skip-text` via `ocr_engine.py`.
     Overwrite the source PDF in staging; move the original to `_processed-originals/`.
   - **Searchability gate**: after OCR, re-check `conf > 0`. If still 0, flag as
     **Pending iteration** — do not move to destination until searchable.

2. **Extract metadata** — date, amount, issuer, category, owner using YAML config.

3. **Propose filename** — follow `doc_types`, `monthly_categories`,
   `description_from_filename_categories`, and `filing_notes` from YAML.

4. **Propose destination** — follow `route_templates` and `filing_notes` from YAML.

---

## Step 2 — Process JPEGs (user-confirmed only)

For each **approved** JPEG:

1. Convert to PDF + OCR:
   `ocrmypdf --optimize 1 --image-dpi 300 <input.jpg> <output.pdf>`
   Move original JPEG to `_processed-originals/`.
2. **Dedup check** — compare against other JPEGs and existing PDFs; delete confirmed
   duplicates and note in history.
3. **Extract text** — if `conf == 0` after OCR, apply preprocessing (see `filing_notes`
   in YAML for the crop → upscale → contrast/sharpen → re-OCR → pymupdf fallback chain).
   Never finalize a JPEG→PDF without `conf > 0`.
4. Propose filename + destination (same pipeline as PDFs).
5. Mark `(JPEG→PDF+OCR)` in the history table.

For each **declined** JPEG, log as `skipped` immediately.

---

## Step 3 — Review & confirm

**Always present the proposal table and wait for explicit user confirmation before
moving, renaming, or deleting any file.**

---

## Step 4 — Duplicate check at destination

Before placing each file, scan the destination for an existing file with the same
period (same date prefix + issuer). If found, stop and ask:

> `⚠ '<proposed-name>' — existing file at destination: '<existing-name>'.`
> `Replace / Keep both (-v2) / Skip?`

Never silently overwrite. Never silently rename.

---

## Step 5 — Execute (on explicit `go`)

1. Move files to proposed destinations.
2. Append to `__downloads__/YYYY.MM - PROCESSED_PDFS.md` (create if missing).
   **Write log entries using Python — not PowerShell — to preserve `$` signs in amounts.**
3. For Credit Card Statement / Bill items, update statements CSV:
   `due date | what | full amount due`.
4. Flush OCR temp files (`_ocr_tmp/`) and OCR cache.

---

## Output format

Append a new section to the current month's ledger:

```markdown
## YYYY-MM-DD — N files processed

**Notes:**
- (free-form bullets for deletions, special cases, pending iterations)

**Pending iteration:**
- (files not yet moved or flagged)

| #  | Original File | Category | Issuer | New Name | Amount | Destination |
|----|---------------|----------|--------|----------|--------|-------------|
```

Status markers: ✅ moved · ⚠️ needs attention · ❌ error · ⏳ pending
