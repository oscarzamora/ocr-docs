# OCR Router

OCR Router is a Python CLI that processes PDF documents in batches, extracts metadata, classifies document types, and routes files into structured folders.

It is designed to be configurable so each user can define their own categories, issuers, and folder templates.

## Features

- **Interactive Review:** Preview all proposed actions before any file move/rename
- **Intelligent Routing:** Classify documents and route by configurable category/metadata templates
- **Text & Metadata Extraction:** Date, amount, issuer, account hints, and OCR confidence heuristic
- **Smart Naming:** Consistent filename normalization based on document type and metadata
- **Folder Density Awareness:** Detects existing structure and chooses practical routing behavior
- **Manifest Generation:** Tracks all processed documents in CSV and JSONL
- **Markdown History Log:** Optional run history output for traceability
- **Rule Capture:** Supports adding routing rules from review feedback

---

## OCR Requirements (Windows)

The tool uses **PDF24** for OCR. PDF24 is free desktop software that produces searchable (text-layer) PDFs from scanned images.

### Why OCR is needed
Many PDFs (scans, photos, fax-like exports) contain no machine-readable text. Without OCR, extraction and classification quality is limited.

### Obtaining PDF24
| Option | Where |
|--------|-------|
| **Desktop app (recommended)** | [https://www.pdf24.org/en/creator.html](https://www.pdf24.org/en/creator.html) — free, no account needed |
| **Direct installer** | [https://download.pdf24.org/pdf24-creator.exe](https://download.pdf24.org/pdf24-creator.exe) |
| **Winget** | `winget install SoftwareAG.PDF24Creator` |
| **Chocolatey** | `choco install pdf24creator` |

Default OCR executable path:
```
C:\Program Files\PDF24\pdf24-Ocr.exe
```

### What happens without PDF24
The CLI can run in text-only mode (`--skip-ocr`). Text-based PDFs are still processable, but image-only PDFs may be skipped or have incomplete extraction.

### Configuring a custom PDF24 path
If PDF24 is installed elsewhere, set `pdf24_path` in your config file:
```yaml
ocr_settings:
  pdf24_path: "D:\\Tools\\PDF24\\pdf24-Ocr.exe"
```
Or set it via environment variable in `.env`:
```
PDF24_PATH=D:\Tools\PDF24\pdf24-Ocr.exe
```

---

## Quick Start

### 1. Setup

```powershell
cd ocr-docs

python -m venv venv
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. Configure

Use the public example config as your starting point:

```powershell
Copy-Item config/routing-config.example.yaml config/routing-config.local.yaml
```

Then edit `config/routing-config.local.yaml` with your personal categories, issuers, and route templates.

### 3. Run (interactive, default)

```powershell
python -m ocr_router.cli process `
  --input  "C:\path\to\input" `
  --output "C:\path\to\output" `
  --config config/routing-config.local.yaml
```

**Interactive flow:**
1. Analyses all PDFs silently
2. Prints a full proposal table (original → new name, category, issuer, folder status)
3. Asks **Move** (target folder) or **Rename in place** (stay in source folder, new name only)
4. Asks which files to act on (`Enter` = all, `1,3,5` = only those, `skip 2,4` = all except, `q` = quit)
5. Executes and appends a section to `PROCESSING_HISTORY.md`

### Batch Mode (no prompts)

```powershell
python -m ocr_router.cli process `
  --input  "C:\path\to\pdfs" `
  --output "C:\path\to\output" `
  --no-interactive
```

### Dry Run (preview only, no changes)

```powershell
python -m ocr_router.cli process --input ... --output ... --dry-run
```

---

## Naming Convention

| Document type | Format |
|---------------|--------|
| Monthly statement | `YYYY.MM - Issuer DocType.pdf` |
| Monthly with account | `YYYY.MM - Issuer DocType - (Last4 XXXX) - $Amount.pdf` |
| Dated transaction / receipt | `YYYY.MM.DD - Issuer DocType - $Amount.pdf` |
| Paystub | `YYYY.MM.DD - Issuer Paycheck - $NetPay.pdf` |
| Reference / policy form | `YYYY - Issuer DocType.pdf` (no amount) |
| Non-USD currency | `S/Amount` (Soles), `€Amount` (EUR), `£Amount` (GBP) |

Missing metadata fields are omitted from the filename instead of inserting placeholder tokens.

---

## Project Structure

```
ocr-docs/
├── src/ocr_router/
│   ├── cli.py              # Interactive CLI — phases: analyse → review → confirm → execute → log
│   ├── ocr_engine.py       # PDF24 OCR integration (auto-skips if unavailable)
│   ├── extractor.py        # Text & metadata extraction (date, amount, issuer, currency)
│   ├── router.py           # Document classification and filename normalisation
│   ├── folder_resolver.py  # Density-aware folder routing (exact/new/flat/suggest)
│   ├── manifest.py         # CSV + JSONL manifest writer
│   └── config.py           # Pydantic config loader
├── config/
│   └── routing-config.yaml # All routing rules, issuers, keywords, templates
├── scripts/
│   └── dry_run.py          # Standalone non-destructive test runner
└── tests/
```

---

## Environment Variables

Create a `.env` file (never commit):

```
PDF24_PATH=C:\Program Files\PDF24\pdf24-Ocr.exe
OCR_CONFIG_PATH=config/routing-config.local.yaml
LOG_LEVEL=INFO
DEBUG=false
```

---

## Public vs Private Data

Use this pattern to publish safely while keeping personal data local:

1. Keep only generic templates in git:
  - `config/routing-config.example.yaml`
  - `.env.example`
2. Keep personal files local and ignored:
  - `config/routing-config.local.yaml`
  - `.env`
  - manifests, logs, and processed document folders
3. Iterative learning rules can be public when written in generic terms:
  - Keep category and keyword improvements
  - Replace real personal names from PDFs (for example first/last names) with neutral placeholders
4. Before pushing, run:

```powershell
git status
```

If any personal file appears, add or update `.gitignore` first.

Important: if sensitive data was committed in past history, removing it from the current commit is not enough. Rotate secrets and rewrite git history before making the repository fully public.
