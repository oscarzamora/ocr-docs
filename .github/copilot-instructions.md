# Copilot Instructions

## Build & Commands

```bash
# Install (activate venv first)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run the CLI
python -m ocr_router.cli process --input "data/input" --output "data/output" --config config/routing-config.yaml

# Run all tests
pytest tests/

# Run a single test file or test
pytest tests/test_extractor.py
pytest tests/test_extractor.py::test_function_name

# Tests with coverage
pytest --cov=src tests/

# Format
black src/

# Lint
flake8 src/
```

Black line length is 100 (`pyproject.toml`).

## Architecture

The pipeline processes PDFs in a fixed sequence across 5 independent components wired together in `cli.py`:

```
OcrEngine → PdfTextExtractor → MetadataExtractor → DocumentRouter → ManifestWriter
```

1. **`ocr_engine.py`** — Shells out to `pdf24-Ocr.exe` (Windows). If PDF24 is unavailable, the CLI automatically falls back to raw text extraction (`--skip-ocr`).
2. **`extractor.py`** — Two classes: `PdfTextExtractor` (static methods, uses `pypdf`) and `MetadataExtractor` (instance, takes `config dict`). Extracts date, amount, account, owner, issuer via regex.
3. **`router.py`** — `DocumentRouter` classifies by scoring keyword matches per category, then formats the output path using `route_templates` from config (string `{placeholder}` substitution). `Unknown` path segments are stripped automatically.
4. **`manifest.py`** — Writes `ManifestEntry` records to both CSV and JSONL in parallel.
5. **`config.py`** — Loads `routing-config.yaml` into a Pydantic `RoutingConfig` model. All other components receive `config.model_dump()` (a plain dict).

## Key Conventions

**Config is passed as a plain dict**, not the Pydantic model. Every component takes `config: dict` in `__init__`. Use `cfg.model_dump()` at the CLI layer when constructing components.

**All routing logic lives in YAML**, not code. To add a category, add keywords under `categories:`. To change folder structure, edit `route_templates:`. Category-specific templates override the `default` template.

**Route path cleanup**: `DocumentRouter.build_route_path()` strips `Unknown/` segments from paths — so if a metadata field is missing, that folder level is omitted rather than creating an `Unknown/` directory.

**Filename normalization format**: `{date}_{$amount}_{issuer}_{original_stem}.pdf` — components are skipped if the metadata field is absent.

**`ocr_confidence`** is a naive heuristic (`min(len(text) / 5000.0, 1.0)`), not a real OCR confidence score.

**Test fixtures** in `conftest.py` provide `temp_dir`, `sample_pdf` (blank pypdf PDF), and `sample_config` (dict). Use these rather than creating new fixture patterns.

## Environment Variables

```
PDF24_PATH    # Not currently wired; pdf24_path comes from config YAML or OcrSettings default
OCR_CONFIG_PATH  # Override default config file path
LOG_LEVEL     # INFO (default)
DEBUG         # false (default)
```

Store in `.env` (never commit). Use `.env.example` for templates.

## Target Folder Structure (OneDrive\Documents)

The output of this pipeline is `C:\Users\ozamo\OneDrive\Documents`. The real folder taxonomy is:

### Financial — by issuer then year
| Category | Path pattern | Notes |
|---|---|---|
| `Bank Account & Statements` | `Bank Account & Statements/{issuer}/{year}` | Issuer examples: `Citibank`, `Chase Checking`, `Fidelity`, `PNC`, `Capital One` |
| `Credit Card Statements` | `Credit Card Statements/{issuer}/{year}` | Issuer includes owner suffix: `AMEX (A)`, `Bank of America (B)`, `Citibank - AAdvantage (A)` |
| `Bills` | `Bills/{issuer}/{year}` | Issuer examples: `AT&T`, `FPL`, `T-Mobile`, `Pure Water Pool Service` |
| `Mortgage & Home Equity Accounts` | `Mortgage & Home Equity Accounts/{lender + account}/{year}` | Lender examples: `PennyMac - Weston Account - Loan # 8193759048`, `Rocket Mortgage - Boca Raton Account # 0710628363` |
| `HSA & FSA Transactions` | `HSA & FSA Transactions/{year}` | No issuer level |
| `Paystubs` | `Paystubs/{year}` | No issuer level |
| `Tax Returns` | `Tax Returns/{year} Tax Return Related Documents` | Special suffix in folder name |

### Health & Insurance — by year only
- `Health Statements & Results/{year}`
- `Insurance/{year}`

### Auto Documentation — by vehicle
- `Auto Documentation/{year} {Make} {Model}/{sub-topic}`
- Sub-topics: `Contract Related`, `Lease Agreement Docs`, `Manuals`, `Title Documentation`, `Sale`

### Other categories (not routed by OCR pipeline)
- `Careers`, `Contracts`, `Real Estate`, `Rental`, `Tax Returns`, `Remodeling`, `Travel` — manually filed

### Owner encoding convention
Owners **OZ** (Oscar/OwnerA) and **GV** (OwnerB) are embedded in the **issuer name** as a suffix, e.g. `AMEX (A)`, `Bank of America (B)`. They are **not** a separate folder level in practice.

### Closed accounts convention
Inactive/closed issuers are moved to `_ Closed Accounts _` subdirectory within each category.

### `__downloads__` folder
`__downloads__` at the root is a staging area — files here are **not** processed by the OCR pipeline. New PDFs downloaded from bank/card portals land here before being processed.

## Config vs. Reality Gaps

The current `routing-config.yaml` has mismatches with the real folder structure:
- `Bills` template uses `{account}` but real path uses **issuer name**, not account number
- `Credit Card Statements` template uses `{account}` but real path uses **issuer name with owner suffix**
- `owners: [OwnerA]` is defined but owner is embedded in the issuer string, not injected as a standalone path segment
- `Bank Account & Statements` has no route template entry — it falls through to `default`

When adding features or fixing routing, align output paths to the conventions above.

## Security

- Never commit `.env`, `manifest.csv`, or `manifest.jsonl` — manifests contain document metadata
- Keep the repo private
