---
mode: agent
description: Scan __downloads__, OCR/compress PDFs and convert JPEGs to PDF, then recommend rename + destination folder per the established taxonomy. Append results to PROCESSED_PDFS.md.
---

# Process new files in `__downloads__`

You are processing PDFs and JPEGs that have landed in the staging folder:

`C:\Users\ozamo\OneDrive\Documents\__downloads__\`

## Workflow

### Step 0 — List candidates (ask before acting)

Scan `__downloads__` for new files (skip anything already listed in [PROCESSED_PDFS.md](../../../OneDrive/Documents/__downloads__/PROCESSED_PDFS.md)):

- **PDFs** — include all `.pdf` files not in history. Skip `Archive/`, `Tracklists/`, `desktop.ini`, `PROCESSED_PDFS.md` itself, and `_processed-originals/`.
- **JPEGs** — include all `.jpg` / `.jpeg` files not in history. Skip the same exclusion list above.
- Ignore everything else (png, mp4, csv, pbix, xlsx) unless the user asks.

**Present two separate lists** before doing anything else:

1. `📄 PDFs to process (N)` — list filenames
2. `🖼 JPEGs found (N)` — list filenames and ask: *"¿Cuáles proceso? (all / numbers / none)"*

Wait for the user's JPEG answer before proceeding. For each JPEG the user **declines**, log it immediately in PROCESSED_PDFS.md under a `## skipped` entry so it is never surfaced again.

---

### Step 1 — Process PDFs

For every confirmed PDF:

1. **OCR + compress (always)** — every PDF goes through `ocrmypdf --optimize 3 --skip-text`:
   - Run via [ocr_engine.py](../../src/ocr_router/ocr_engine.py) **up front during the analyse phase**, before presenting the proposal table.
   - `--optimize 3` applies **lossy jbig2enc + pngquant compression** — typically **~40% smaller** on B&W financial statements, even when a text layer already exists.
   - `--skip-text` ensures existing text pages are not re-OCR'd; Tesseract only runs on pure-scan pages.
   - **Overwrite the source PDF** in `__downloads__` with the optimized copy immediately.
   - Move the original to `__downloads__\_processed-originals\` (create if missing) as a safety net.
   - If the original already lives in `_processed-originals\`, skip reprocessing — the `__downloads__\` copy is already the cached version.
   - Mark the file as `(OCR)` if Tesseract ran, `(compressed)` if only optimization was applied.
   - **✅ Searchability gate**: after processing, verify `conf > 0` via `PdfTextExtractor.extract_text_with_confidence()`. If conf=0, the file is **not acceptable as a final version** — flag it under **Pending iteration** and do not move it to its destination until the text layer is confirmed.

2. **Extract metadata** — date, amount (USD `$` or PEN `S/`), issuer, category, owner using [routing-config.yaml](../../config/routing-config.yaml).
3. **Propose new filename** (see Naming section).
4. **Propose destination folder** (see Folders section).

---

### Step 2 — Process JPEGs (user-confirmed only)

For each JPEG the user approved:

1. **Convert to PDF + OCR** using `ocrmypdf` with **lossless compression**:
   - `ocrmypdf --optimize 1 --image-dpi 300 <input.jpg> <output.pdf>` — optimize=1 is lossless (no pngquant/jbig2 lossy pass) to preserve photo/color quality.
   - Output filename: same stem as the JPEG, extension `.pdf`, written to `__downloads__\`.
   - Move the original JPEG to `__downloads__\_processed-originals\`.
2. **Check for duplicates** — before proceeding, compare newly converted JPEGs against each other and against existing PDFs. WhatsApp images in particular are often duplicates of order confirmations or receipts already present. Delete duplicates and note in history.
3. **Extract text** — run `PdfTextExtractor.extract_text_with_confidence()`. If conf=0 (Tesseract found no text):
   - The image is likely a **phone photo of a screen** with background noise (keyboard, room, glare). Tesseract fails on these even with `--force-ocr`.
   - **Preprocess with Pillow**: crop out the background/keyboard (keep only the document/dialog area), upscale 2× with LANCZOS, boost contrast (1.5×) and sharpness (2×), save as PNG at 300dpi.
   - Convert the preprocessed PNG to PDF with OCR: `ocrmypdf --optimize 1 --image-dpi 300 <cropped.png> <output.pdf>`. This usually yields conf > 0 and a searchable PDF.
   - If still conf=0 after preprocessing, **use pymupdf** (`fitz`) to render a page preview at 150 dpi and read the content visually — never leave a file as "Unknown" without trying visual inspection. Extract metadata manually from what you see.
   - **✅ Searchability gate**: every JPEG→PDF final file must have conf > 0. Do not finalize or log a file as processed if it is not searchable.
   - Note in history if the PDF was rebuilt from a cropped version (original JPEG preserved in `_processed-originals`).
4. **Propose filename + destination** — same pipeline as PDFs above.
5. Mark the file as `(JPEG→PDF+OCR)` in the history table.

For each JPEG the user **declined**, log it in PROCESSED_PDFS.md as `skipped` (see Output format below) so it is never surfaced again.

---

### Step 3 — Review & confirm

**ALWAYS present the proposal table and wait for explicit user confirmation before moving, renaming, or deleting any file.** The **Destination** column is always a *recommendation* — the user confirms or overrides each row. Do not assume the edge-case defaults (e.g. `__downloads__` for vehicle orders) without asking.


## Naming convention

| Category type | Format | Example |
|---|---|---|
| Monthly (Bills, CC, Bank, Mortgage, HSA, Rental) | `YYYY.MM - {Issuer} {DocType} - ${amount}.pdf` | `2026.04 - T-Mobile Monthly - $304.29.pdf` |
| Dated (Receipts, EOB, Notices, Personal, Paystubs) | `YYYY.MM.DD - {Issuer} {DocType} - ${amount}.pdf` | `2026.01.29 - Delta Dental EOB - $139.00.pdf` |
| Reference (HOA, Tax Returns, Real Estate) | `YYYY.MM.DD - {Issuer} {DocType} - {descriptor}.pdf` | `2006.06.01 - Weston Hills HOA - Architectural Design.pdf` |
| Peruvian AMEX (Interbank) | `YYYY.MM - Interbank Amex Gold (Last4 XXXX) S/{amount}.pdf` | `2026.04 - Interbank Amex Gold (Last4 0234) S/314.09.pdf` |
| T-Mobile (has Detailed + Summary) | append `- Detailed.pdf` or `- Summary.pdf` | `2026.04 - T-Mobile Monthly - $304.29 - Detailed.pdf` |

Rules:
- Omit `${amount}` if the category is in `no_amount_categories` or no amount applies.
- Omit `Last4` if not present on the document.
- Drop weekday/timezone noise. Date is the **statement/issue date**, not download date.
- Do **not** use the patient/holder Date Of Birth as the document date (known pitfall: `Report_000977`).

## Folder taxonomy (under `C:\Users\ozamo\OneDrive\Documents\`)

| Category | Destination pattern |
|---|---|
| `Bills` | `Bills\{Issuer}\{Year}` |
| `Credit Card Statements` (US) | `Credit Card Statements\{Issuer}\{Year}` |
| `Credit Card Statements` (Interbank Peru) | `Credit Card Statements\AMEX Gold\{Year}` |
| `Bank Account & Statements` | `Bank Account & Statements\{Issuer}\{Year}` |
| `Mortgage & Home Equity Accounts` | `Mortgage & Home Equity Accounts\{Lender + Account}\{Year}` |
| `HSA & FSA Transactions` | `HSA & FSA Transactions\{Year}` |
| `Paystubs` | `Paystubs\{Year}` |
| `Tax Returns` | `Tax Returns\{Year} Tax Return Related Documents` or `Tax Returns\Forms` |
| `Health Statements & Results` | `HSA & FSA Transactions\{Year}` (**default for all health docs**) |
| `Insurance` | `Insurance\{Year}` |
| `Receipts` | `Receipts, Payment, Warranty` |
| `Notices` (employer/HR) | `Careers\{Employer}` (e.g. `Careers\DTCC`) |
| `Personal` | `Personal` |
| `Real Estate & HOA` | `Weston Hills` (or other property folder) |
| `Rental Expenses` | `Rental\__Expenses__\` (flat, no year subfolder) |
| Auto docs | `Auto Documentation\{Year} {Make} {Model}\{Sub-topic}` |

Person-scoped routing (override above when the doc clearly belongs to one person):
- **FamilyMemberA** → `FamilyMemberA\__Expenses__` (her cards, tickets, etc.)
- **FamilyMemberB** → `FamilyMemberB\` (his health, school, contact lens)
- **OwnerB** → `OwnerB\`
- Credit cards in her name embed `(B)` in the issuer suffix.

Closed/inactive issuers go under `_ Closed Accounts _\` inside each category folder.

## Edge cases (from prior runs)

- **FPL Electric** — autopay account; never include a payment prompt or "amount due" note. Route to `Bills\FPL\{Year}`.
- **All health documents** (EOBs, clinic statements, dental, vision, prescriptions) → default destination is `HSA & FSA Transactions\{Year}`, not `Health Statements & Results`. Always flag whether the patient balance is **still owed** and ask the user if the file should stay in `__downloads__` until paid.
- **Rental Expenses** — always include the amount in the filename: `YYYY.MM.DD - {Vendor} - ${amount}.pdf`. Never omit amount even if the receipt looks blank; check carefully.
- **Temp / junk** (vendor support pages, unrelated political files) → propose `DELETED` in the destination column.
- **Vehicle order/spec sheets** (e.g. `911 Carrera T.pdf`, `2027 QX60 Order.jpeg`) → keep in `__downloads__` with a date prefix; they are work-in-progress until the purchase closes.
- **Proof of Payment / receipts that look like CC docs** → route to `Receipts, Payment, Warranty`, not CC Statements.
- **HOA architectural/structural docs** → `Weston Hills` directly (not the generic HOA template).
- **Microsoft paystubs** → `Paystubs\{Year}` regardless of pay period date.

## Output format (history table)

Append a new section to [PROCESSED_PDFS.md](../../../OneDrive/Documents/__downloads__/PROCESSED_PDFS.md):

```markdown
## YYYY-MM-DD — N files processed

**Notes:**
- (free-form bullets for deletions, special cases, pending iterations)

**Pending iteration:**
- (file → bug/issue to address in code)

| #  | Original File | Category | Issuer | New Name | Amount | Destination |
| -- | ------------- | -------- | ------ | -------- | ------ | ----------- |
| 1  | ...           | ...      | ...    | ...      | ...    | ...         |
```

For **declined JPEGs**, append a separate skipped block (can be in the same run section):

```markdown
**Skipped (do not reprocess):**
| File | Reason |
| ---- | ------ |
| photo.jpg | user declined |
```

- Use **pipe tables only** (no bullet lists, no box-drawing). This is the confirmed standard.
- Use `same` in the New Name column when the file was already correctly named.
- Use `—` for empty cells.
- JPEG→PDF entries: use the converted `.pdf` name in New Name, mark Original File as `photo.jpg (→PDF+OCR)`.
- Treat [PROCESSED_PDFS.md](../../../OneDrive/Documents/__downloads__/PROCESSED_PDFS.md) as **append-only**: never regenerate, truncate, or overwrite earlier sections.
- If a correction is needed, append a follow-up section rather than rewriting prior history.

## What to ask me before acting

1. **Always present the JPEG list and wait for a decision** before processing any JPEG (Step 0 above).
2. **`Bills` and `Credit Card Statements` always stay in `__downloads__\`** — do not move them. Rename in place to the proposed filename and set the Destination column in the history table to `__downloads__ (pending payment)`. I will move them manually once payment is scheduled/cleared.
3. For ambiguous categories (e.g. an EOB that could be Insurance vs Health), ask before moving.
4. For any file that triggers a metadata edge case (DOB picked as date, missing issuer, score below `min_classification_score`), flag it under **Pending iteration** instead of silently guessing.

Begin by listing the new PDF and JPEG candidates in `__downloads__` and waiting for my JPEG selection before proceeding.

## Cleanup (always run after completing the full session)

Once all files have been moved/renamed and the history log is written:

1. **Delete `_processed-originals\`** — remove the entire folder and all its contents. Originals are only a safety net during the session; do not leave them behind.
2. **Delete any temp files** created during processing — e.g. `_cropped.png`, `_tess_*.txt`, `_tmp_*.pdf`, `_preprocessed_*.png`, or any other intermediate files in `__downloads__` or the project root.
3. Confirm cleanup in your final message to the user.
