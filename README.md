# OCR Router

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![CLI](https://img.shields.io/badge/Interface-CLI-111111)
![Sanitize%20Gate](https://img.shields.io/badge/Sanitize%20Gate-Enabled-2EA043)
![Status](https://img.shields.io/badge/Status-Production%20Ready-22863A)

OCR Router is a Python CLI that processes PDF documents in batches, extracts metadata, classifies document types, and routes files into structured folders.

It is configuration-driven: categories, issuers, naming, and folder routing are defined in YAML so each user can tailor behavior without changing source code.

## About (Verbose)

OCR Router is designed for high-volume document organization where PDFs arrive mixed (bank statements, credit cards, bills, tax forms, notices, receipts, paystubs). The pipeline performs OCR when needed, extracts key fields (date, amount, account hints, issuer), classifies by keyword scoring, proposes standardized filenames, and routes files into deterministic folder structures.

The tool emphasizes safety and reviewability:

- Interactive review before moves/renames
- Dry-run mode for zero-risk previews
- CSV/JSONL manifests for traceability
- Sanitization checks to prevent accidental personal-data publication

## Features

- Interactive review before file operations
- OCR support for scanned/image-only PDFs via PDF24
- JPEG/JPG ingestion with conversion to searchable PDF (OCR applied)
- Metadata extraction (date, amount, issuer, account)
- Rule-based classification and routing templates
- Deterministic filename normalization
- Density-aware folder resolver
- Manifest output in CSV and JSONL
- Privacy guardrails for public repositories

---

## 1. Installation

### Prerequisites

- Python 3.10+
- Windows PowerShell
- Optional OCR engine: PDF24 Creator (recommended)

Install PDF24 (optional but recommended):

| Option | Command / Link |
|---|---|
| Winget | `winget install SoftwareAG.PDF24Creator` |
| Chocolatey | `choco install pdf24creator` |
| Download | https://tools.pdf24.org/en/creator |

### Clone and install dependencies

```powershell
git clone https://github.com/oscarzamora/ocr-docs.git
cd ocr-docs

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Create your local private config

```powershell
Copy-Item config/routing-config.example.yaml config/routing-config.local.yaml
```

Edit `config/routing-config.local.yaml` with your own categories, issuers, and route templates.

---

## 2. How To Use

Main command (interactive mode):

```powershell
python -m ocr_router.cli process `
  --input  "C:\path\to\input" `
  --output "C:\path\to\output" `
  --config config/routing-config.local.yaml
```

Interactive flow (PDF + JPEG inputs):

1. Analyze all input files
2. Convert JPEG/JPG files to searchable PDFs through OCR preprocessing
3. Show proposal table (category, issuer, filename, route)
4. Ask move vs rename-in-place
5. Ask file selection (all, include-list, skip-list)
6. Execute and write history/manifest

---

## 3. Can This Work With Llama3?

Yes, as an optional integration.

Important: Llama3 is not required and is not hard-coded in the core pipeline. OCR Router works out-of-the-box with rule-based classification.

### Recommended integration approach

Use Llama3 as a fallback classifier only when rule confidence is low.

1. Run OCR Router extraction first (text + metadata).
2. If score is below threshold, call Llama3 (for example via Ollama).
3. Return one of your known categories.
4. Keep deterministic naming/routing in OCR Router.

Example local Ollama call (Llama3):

```powershell
curl http://localhost:11434/api/generate `
  -d '{"model":"llama3","prompt":"Classify this document into one of: Bills, Credit Card Statements, Tax Returns, Receipts. Text: ...","stream":false}'
```

Good practice:

- Keep category output constrained to allowed labels.
- Log model decisions in manifest notes.
- Do not send private document text to external hosted APIs unless you explicitly want cloud inference.

---

## 4. Git: About, Tags, and Release Flow

### Verbose About text (for GitHub repo "About")

Use this in GitHub repository description/about field:

`OCR Router is a privacy-aware Python CLI for OCR, metadata extraction, deterministic document classification, smart naming, and folder routing with review-first workflows and manifest traceability.`

Suggested Topics (GitHub tags):

- `python`
- `ocr`
- `pdf`
- `document-management`
- `cli`
- `automation`
- `privacy`
- `yaml-config`

### Version tags (annotated)

```powershell
git tag -a v0.2.0 -m "v0.2.0: README overhaul, sanitize gate, release documentation"
git push origin v0.2.0
```

List tags:

```powershell
git tag --list
```

Show tag details:

```powershell
git show v0.2.0
```

---

## 5. Run Examples

### Example A: Interactive processing

```powershell
python -m ocr_router.cli process `
  --input  "C:\Docs\Incoming" `
  --output "C:\Docs\Sorted" `
  --config config/routing-config.local.yaml
```

### Example B: Dry-run (no file changes)

```powershell
python -m ocr_router.cli process `
  --input "C:\Docs\Incoming" `
  --output "C:\Docs\Sorted" `
  --config config/routing-config.local.yaml `
  --dry-run
```

### Example C: Batch mode (non-interactive)

```powershell
python -m ocr_router.cli process `
  --input "C:\Docs\Incoming" `
  --output "C:\Docs\Sorted" `
  --config config/routing-config.local.yaml `
  --no-interactive
```

### Example D: Review existing manifest

```powershell
python -m ocr_router.cli review --manifest "C:\Docs\Sorted\manifest.jsonl"
```

### Example E: Validate privacy before push

```powershell
python scripts/sanitize_check.py
```

---

## Naming Convention

| Document type | Format |
|---|---|
| Monthly statement | `YYYY.MM - Issuer DocType.pdf` |
| Monthly with account | `YYYY.MM - Issuer DocType - (Last4 XXXX) - $Amount.pdf` |
| Dated transaction / receipt | `YYYY.MM.DD - Issuer DocType - $Amount.pdf` |
| Paystub | `YYYY.MM.DD - Issuer Paycheck - $NetPay.pdf` |
| Reference / policy form | `YYYY - Issuer DocType.pdf` |

Missing metadata fields are omitted instead of using placeholder tokens.

---

## Project Structure

```text
ocr-docs/
  src/ocr_router/
    cli.py
    ocr_engine.py
    extractor.py
    router.py
    folder_resolver.py
    manifest.py
    config.py
  config/
    routing-config.example.yaml
    routing-config.yaml
    routing-config.local.yaml  # local-only, gitignored
  scripts/
    dry_run.py
    sanitize_check.py
  tests/
```

---

## Environment Variables

Create `.env` locally (never commit):

```dotenv
PDF24_PATH=C:\Program Files\PDF24\pdf24-Ocr.exe
OCR_CONFIG_PATH=config/routing-config.local.yaml
LOG_LEVEL=INFO
DEBUG=false
```

---

## Public vs Private Data

1. Keep generic templates in git:
   - `config/routing-config.example.yaml`
   - `.env.example`
2. Keep personal files local and ignored:
   - `config/routing-config.local.yaml`
   - `.env`
   - manifests and local logs
3. Run the sanitization gate before push:

```powershell
python scripts/sanitize_check.py
```

The same check runs in GitHub Actions on pull requests and pushes.
