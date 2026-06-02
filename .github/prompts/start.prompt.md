---
mode: agent
description: Start an OCR Router session from SESSION_START.md with a review-first run and explicit go/no-go control.
---

# Start OCR Router session (`/start`)

Use [SESSION_START.md](../../SESSION_START.md) as the canonical session template and execute the routine end-to-end.

## Behavior

1. Summarize understanding in 4 to 8 lines.
2. Propose a short plan.
3. Confirm only the minimum missing run inputs:
   - source folder path
   - run mode: `preview-only` or `review-then-go`
4. Execute directly once inputs are provided:
   - OCR only non-OCR-ready files
   - propose filename + destination
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