# Session Start Template for OCR Router

Use this at the beginning of every new chat session.

## Quick Routine (30 seconds)

1. Update the fields in the block below.
2. Send the full block as your first message.
3. Ask the agent to execute end-to-end and verify with tests.

## Start-of-Session Prompt (copy, fill, send)

Project: OCR Router (local-first document OCR, routing, and filing)

Repository topology:
- Public git (sanitized only): https://github.com/<public-user-or-org>/ocr-docs/
- Internal-only git (personal/sensitive operational data): https://github.com/<private-user-or-org>/ocr-docs-int

Working mode:
- Language: English
- Be action-oriented: implement changes, run checks, and report results
- Keep changes minimal and focused
- Public sharing rule: only sanitized, non-personal artifacts may go to public git
- Private sharing rule: personal or identifying operational artifacts go only to internal-only git
- LLM default rule: use local LLM by default on this machine; if backend is down, start it and retry health before any fallback

Current objective (single sentence):
- Execute one end-to-end review-first routing cycle: scan a user-selected source folder (PDF + supported images), OCR non-OCR-ready PDFs and all supported images, propose name and destination, learn from corrections, and on explicit go move files, append processed history, and flush session cache.

Project mission (fixed):
- Help the user find and organize previously OCR'd PDFs and JPEGs.
- Route/move files into predetermined destinations.
- Keep user-in-the-loop review before finalizing actions.
- Learn from each iteration using feedback.
- Persist movement history for traceability and replay.

What is already done (3 to 7 bullets):
- Pipeline components are wired: OcrEngine -> PdfTextExtractor -> MetadataExtractor -> DocumentRouter -> FolderResolver -> ManifestWriter
- LLM path is optional and local-only (Ollama). No cloud backend.
- OCR cache is reused, and PDF OCR should run only when no text layer is present.
- Routing behavior should remain config-driven (YAML), not hardcoded.
- Feedback and embeddings are project-local under data/_feedback.

Open tasks for this session:
- Confirm run mode and source folder to scan.
- At session start, delete `C:\Users\ozamo\OneDrive\Documents\__downloads__\_ocr_tmp` if it exists.
- Before OCR/classification, check monthly ledger files named YYYY.MM - PROCESSED_PDFS.md and exclude only entries with well-formed, date-prefixed descriptive names.
- Treat generic/repeating names (for example: statement.pdf, invoice.pdf) as ambiguous and always send them through OCR/classification.
- Include supported image files (JPEG/PNG/WebP/TIFF/BMP) in the scan and send them through OCR unless excluded by the processed-ledger rule.
- Never drop low-confidence files from proposals; include them with default name/folder and mark them as awaiting feedback.
- Run review-first classification: OCR only non-OCR-ready files, then propose filename and destination.
- Capture user corrections, re-suggest improved routing, and await explicit go or no-go.
- On go: move files, append processed Markdown history, update statements CSV for credit card/bill items, then flush session cache and temp files.

Session task flow (fixed, execute in order):
1. Expect the user to specify a run via agent mode or a prompt to scan a source folder.
2. Remove `C:\Users\ozamo\OneDrive\Documents\__downloads__\_ocr_tmp` if it exists from prior sessions.
3. Run local LLM health check; if unavailable, force-start the local LLM service and re-check before continuing.
4. Check monthly ledger files named YYYY.MM - PROCESSED_PDFS.md and filter out only files with well-formed, date-prefixed descriptive names already listed there.
5. Send generic/repeating names (for example: statement.pdf, invoice.pdf) through OCR/classification even if the same basename appears in ledgers.
6. OCR PDFs only if they are not OCR-ready; OCR supported image files in all cases.
7. Retain OCR artifacts in cache during the session.
8. Propose naming convention and target folder, then wait for explicit go or no-go.
9. If user provides corrections, learn from feedback and suggest improved name and destination.
10. If go is approved, move files, append to processed Markdown history, then flush session cache.

Hard constraints (must follow):
- Local LLM should always be used by default (this machine already has local LLM installed and used in prior runs).
- --no-llm must always disable LLM classification when explicitly requested.
- If local LLM backend is unavailable, force-start it and retry health once before degrading to keyword-only behavior.
- If pngquant is unavailable, keep OCR running with optimize fallback (do not fail the file).
- Treat YYYY.MM - PROCESSED_PDFS.md ledgers as authoritative only for well-formed, date-prefixed descriptive filenames.
- Do not treat generic/repeating basenames as processed by name alone; they must be re-evaluated through OCR/classification.
- Include supported image files in processing and OCR them unless excluded by the processed-ledger rule.
- For generic names (e.g., statement.pdf), apply OCR-text and YAML-driven routing with best-effort naming, then request feedback if confidence is low.
- Keep history logs append-only.
- Strip unknown route path segments instead of creating Unknown folders.
- Do not commit sensitive artifacts (.env, manifests, OCR cache, local feedback logs).
- Keep OCR and LLM processing local.
- OCR internal data may contain personal/sensitive content.
- OCR external/public artifacts must be fully sanitized and must never contain personal/sensitive content.
- Public repo policy: commit only sanitized, non-personal code/docs/examples.
- Personal or identifying operational data stays private.
- External/public push gate: run sanitization check and only push if it passes.
- Never expose personal or identifying terms in public artifacts (examples: personal names, family names, employer names, card-brand names).
- If any sensitive or identifying content is detected, keep it in internal git only.

Definition of done:
- User confirms outputs are acceptable.
- Files are moved with proper naming into the proper target folder.
- Session cache is flushed and temporary files are removed.
- Processed Markdown history is updated.
- Any Credit Card Statement or Bill maintains a CSV record with: due date | what | full amount due.

Verification to run:
- pytest tests/
- Optional targeted run: python -m ocr_router.cli process --input "data/input" --output "data/output" --config config/routing-config.yaml

Files to inspect first:
- src/ocr_router/cli.py
- src/ocr_router/router.py
- src/ocr_router/folder_resolver.py
- config/routing-config.yaml
- tests/

Execution instructions for the agent:
1. Summarize understanding in 4 to 8 lines.
2. Propose a short plan.
3. Implement directly.
4. Run verification.
5. Report:
   - changed files
   - key behavior changes
   - test results
   - follow-up options

## End-of-Session Handoff (paste at the end of each session)

Completed:
- Source folder scanned and candidate files analyzed.
- OCR executed only for non-OCR-ready files.
- Proposed filenames and target folders prepared for review.
- User-approved moves executed (if go was provided).
- Processed Markdown history updated.
- Statements CSV updated for Credit Card Statement/Bill items (if any).

Pending:
- Any files still awaiting explicit go or no-go.
- Any user corrections not yet applied in a new recommendation pass.
- Any remaining files that were intentionally deferred to next session.

Risks or assumptions:
- Local LLM availability/health for the next run.
- Potential ambiguous classifications requiring user confirmation.
- Path or naming conflicts that may require manual override.

First step for next session:
- Confirm source folder and run mode, then start a review-first scan.

Git closeout rule (end of session):
- If code or YAML files changed, stage, commit, and push before ending the session.
- Push sanitized-only changes to public remote.
- Push personal/sensitive operational changes to internal remote.
- If a branch contains mixed content, split commits/branches so public receives only sanitized content.
- Before any public push, run sanitization checks (for example: python scripts/sanitize_check.py) and block public push on any finding.

## Optional One-Liner Helpers (PowerShell)

Run one-command internal session launcher:
.\scripts\start-session.ps1

Print this template in terminal:
Get-Content .\SESSION_START.md

Copy this template to clipboard:
Get-Content .\SESSION_START.md -Raw | Set-Clipboard
