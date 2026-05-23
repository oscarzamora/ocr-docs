---
mode: agent
description: Scan __downloads__, OCR if needed, then recommend rename + destination folder per the established taxonomy. Append results to PROCESSED_PDFS.md.
---

# Process new files in `__downloads__`

You are processing PDFs (and occasionally other docs) that have landed in the staging folder:

`C:\Users\ozamo\OneDrive\Documents\__downloads__\`

## Workflow

For each **new** PDF in `__downloads__` (skip files already listed in [PROCESSED_PDFS.md](../../../OneDrive/Documents/__downloads__/PROCESSED_PDFS.md)):

1. **Scan** — list candidates. Ignore non-PDF assets (jpg, png, mp4, csv, pbix, xlsx) unless the user asks otherwise. Skip `Archive/`, `Tracklists/`, `desktop.ini`, and `PROCESSED_PDFS.md` itself.
2. **OCR if needed (cache eagerly)** — for every PDF, check whether it already has a text layer (try extracting text with `pypdf`; if the result is empty / near-empty, treat it as a scan). Files named `Report_*.pdf` are almost always scans. For any PDF without a text layer:
   - Run OCR via [ocr_engine.py](../../src/ocr_router/ocr_engine.py) (`pdf24-Ocr.exe`) **up front during the analyse phase**, before presenting the proposal table — do not wait for my confirmation to OCR.
   - **Cache the OCR'd version immediately**: overwrite the source PDF in `__downloads__` with the searchable copy so the text layer survives across sessions / restarts while I review.
   - Move the pre-OCR original to `__downloads__\_processed-originals\` (create if missing) as a safety net — do not leave both copies in `__downloads__`.
   - If a file already lives in `_processed-originals\`, do not re-OCR — treat the `__downloads__\` copy as the cached version.
   - Mark the file as `(OCR)` in the history table.
3. **Extract metadata** — date, amount (USD `$` or PEN `S/`), issuer, category, owner. Use the rules from [routing-config.yaml](../../config/routing-config.yaml) (`known_issuers`, `categories`, `monthly_categories`, `doc_types`, `no_amount_categories`).
4. **Propose new filename** using the conventions in the Naming section below.
5. **Propose destination folder** under `C:\Users\ozamo\OneDrive\Documents\` using the taxonomy in the Folders section below.
6. **Do NOT move anything automatically.** Present the recommendations as a markdown pipe table for the user to confirm. Only after explicit confirmation, move the files and append the run to [PROCESSED_PDFS.md](../../../OneDrive/Documents/__downloads__/PROCESSED_PDFS.md).

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

- Use **pipe tables only** (no bullet lists, no box-drawing). This is the confirmed standard.
- Use `same` in the New Name column when the file was already correctly named.
- Use `—` for empty cells.

## What to ask me before acting

1. Confirm the list of files you intend to process (skip the ones already in history).
2. **`Bills` and `Credit Card Statements` always stay in `__downloads__\`** — do not move them. Rename in place to the proposed filename and set the Destination column in the history table to `__downloads__ (pending payment)`. I will move them manually once payment is scheduled/cleared.
3. For ambiguous categories (e.g. an EOB that could be Insurance vs Health), ask before moving.
4. For any file that triggers a metadata edge case (DOB picked as date, missing issuer, score below `min_classification_score`), flag it under **Pending iteration** instead of silently guessing.

Begin by listing the new candidates in `__downloads__` and the proposed table. Wait for my confirmation before moving files or writing to history.
