---
mode: agent
description: Start an OCR Router session with a self-contained review-first routine and explicit go/no-go control.
---

# Start OCR Router session (`/start`)

Run this routine end-to-end without requiring external templates.

## Behavior

1. Summarize understanding in 4 to 8 lines.
2. Propose a short plan.
3. Confirm only the minimum missing run inputs:
   - source folder path
   - run mode: `preview-only` or `review-then-go`
4. Execute directly once inputs are provided:
   - delete `C:\Users\ozamo\OneDrive\Documents\__downloads__\_ocr_tmp` if it exists
   - run local LLM health check; if unavailable, start local backend and retry once before fallback
   - check monthly ledgers named `YYYY.MM - PROCESSED_PDFS.md`
   - exclude only well-formed, date-prefixed descriptive filenames already recorded in ledger
   - treat generic names (for example `statement.pdf`, `invoice.pdf`) as ambiguous and always send through OCR/classification
   - OCR only non-OCR-ready files
   - OCR supported images (JPEG/PNG/WebP/TIFF/BMP)
   - propose filename + destination
   - include low-confidence files and mark them as awaiting feedback
   - wait for explicit `go` before moving files
   - learn from user corrections and re-suggest
5. On explicit `go`:
   - move files
   - append processed Markdown history
   - update statements CSV for Credit Card Statement/Bill items (`due date | what | full amount due`)
   - flush session cache/temp files
6. Run verification:
   - `pytest tests/`
   - optional targeted CLI run if requested

## Hard constraints

- Keep routing config-driven from YAML; no hardcoded routing rules.
- Local LLM is default; if unavailable, degrade gracefully to keyword-only.
- `--no-llm` must always disable LLM classification.
- Keep history append-only.
- Strip unknown route segments instead of creating Unknown folders.
- Keep OCR/LLM processing local.
- Never include personal or identifying terms in public/sanitized outputs.

## Reporting format

Report at the end with:

- Changed files
- Key behavior changes
- Test results
- Follow-up options

Keep changes minimal and focused.