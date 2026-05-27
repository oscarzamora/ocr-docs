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

The pipeline processes PDFs in a fixed sequence across 6 independent components wired together in `cli.py`:

```
OcrEngine → PdfTextExtractor → MetadataExtractor → DocumentRouter → FolderResolver → ManifestWriter
                                                          │
                                                          ▼ (optional, when --llm)
                                                  LLMClassifier (Ollama llama3.2:3b)
                                                          │
                                                          ▼
                                                  EmbeddingStore (few-shot from past confirmed decisions)
```

1. **`ocr_engine.py`** — Shells out to `pdf24-Ocr.exe` (Windows). OCR only runs on files with `confidence == 0.0` (no text layer). Falls back to text-only mode if PDF24 is unavailable.
2. **`extractor.py`** — Two classes: `PdfTextExtractor` (static methods, uses `pypdf`) and `MetadataExtractor` (instance, takes `config dict`). Extracts date, amount, account, owner, issuer via regex. Issuer matching searches the first 1500 chars and last 1000 chars of text; longer keys in `known_issuers` are tried first (specificity wins).
3. **`router.py`** — `DocumentRouter` classifies by scoring keyword matches per category, then formats the output path using `route_templates` from config (string `{placeholder}` substitution). `Unknown` path segments are stripped automatically.
4. **`folder_resolver.py`** — `FolderResolver` applies density-aware routing: if the target path ends in a year and the parent exists, it checks whether the parent already has year subfolders. If yes, creates the year folder (`created`); if no, routes to the parent (`flat`). Returns `(dest_dir, status)` where status is `exact | created | flat | suggest`.
5. **`manifest.py`** — Writes `ManifestEntry` records to both CSV and JSONL in parallel.
6. **`config.py`** — Loads `routing-config.yaml` into a Pydantic `RoutingConfig` model. All other components receive `config.model_dump()` (a plain dict).

### Optional learning layers (L1–L6, all opt-in, all local)

7. **`feedback/log.py`** — Append-only JSONL log under `data/_feedback/corrections.jsonl` by default (project-local, gitignored). Every `process` run writes one `confirmed` / `skipped` / `parked` / `rule_added` record per file. `FeedbackLog.parked_filenames()` returns the active parked set (latest-record-wins across the lifecycle).
8. **`feedback/bootstrap.py`** — Two pre-population modes: `bootstrap_from_downloads` parses `PROCESSED_PDFS.md` history, `bootstrap_from_tree` infers `(category, issuer, year)` from the organized Documents folder layout. Both run full OCR only when no text layer is present.
9. **`feedback/store.py`** — SQLite-backed embedding store. `OllamaEmbedder(nomic-embed-text, 768-dim)` + `EmbeddingStore.search()` does cosine similarity via numpy matmul (no vector DB dependency). `index_log_into_store()` trims excerpts to `max_chars=6000` before embedding to stay under the embedder's 2048-token context.
10. **`llm/classifier.py`** — `LLMClassifier` composes `OllamaBackend` (chat with `format='json'`) + `OllamaEmbedder` + `EmbeddingStore`. `fetch_neighbors()` trims the query text the same way. Defensively rejects categories the model invents.
11. **`llm/backends.py`** — `LLMBackend` ABC + `NullBackend` (used when LLM disabled so callers always have a safe `.classify()` to call) + `OllamaBackend`. `info()` probe distinguishes "daemon down" from "model not pulled".
12. **`eval/runner.py`** — Read-only accuracy harness. `sample_files()` is stratified-deterministic; `EvalRunner` reuses the production classifiers and the Step 5 decision rule (via lazy import of `cli._apply_llm_decision`) so the eval tests the exact code path `process` uses.

### Interactive CLI phases

The `process` command runs in distinct phases:
1. **Pre-filter** — drops files already marked as `parked` in the feedback log (silent skip).
2. **Analyse** — OCR + extract + keyword classify + (optional) LLM classify all files silently; builds a `Proposal` dataclass per file with `keyword_category`, `llm_category`, `llm_confidence`, `backend_label`.
3. **Review** — prints a Rich table of all proposals; shows a `Backend` column when `--llm` is on (`agree ✓ 0.99` | `LLM ✱ 0.90 / kw said:…` | `kw (LLM low) 0.40` | `llm err`). `--dry-run` exits here.
4. **Confirm** — prompts Move vs Rename-in-place, then which files to act on (`Enter`=all, `1,3,5`=selected, `skip 2,4`=all except, `park 7`=keep in place forever, `q`=quit); skipped with `--no-interactive`.
5. **Execute** — moves/renames files, optionally archives originals to `_processed-originals/`.
6. **Log** — appends a section to `PROCESSED_PDFS.md`, writes manifest entries, **writes one feedback record per file** carrying `backend_label` and the LLM diagnostics.

### LLM decision rule (in `cli._apply_llm_decision`, fully unit-tested)

| Situation | Final category | `backend_label` |
|---|---|---|
| LLM disabled or returned None | keyword | `keyword` |
| LLM confidence < threshold | keyword (LLM shown as hint if disagreeing) | `keyword-llm-low-conf` |
| LLM agrees with keyword | keyword (adopt LLM issuer if keyword had none) | `agree` |
| Keyword = `Uncategorized` AND LLM confident | LLM (silent override) | `hybrid-llm` |
| Genuine disagreement above threshold | LLM (flagged for HITL) | `hybrid-llm-disagree` |
| LLM hallucinated non-config category | rejected, treated as failure | (logged, returns None) |

## Key Conventions

**Config is passed as a plain dict**, not the Pydantic model. Every component takes `config: dict` in `__init__`. Use `cfg.model_dump()` at the CLI layer when constructing components.

**All routing logic lives in YAML**, not code. To add a category, add keywords under `categories:`. To change folder structure, edit `route_templates:`. Category-specific templates override the `default` template.

**Route path cleanup**: `DocumentRouter.build_route_path()` strips `Unknown/` segments from paths — so if a metadata field is missing, that folder level is omitted rather than creating an `Unknown/` directory.

**Filename normalization format**: `{date} - {issuer} {doc_type} - {account} - {amount}{ext}` — components joined with ` - `, skipped entirely if the metadata field is absent. `doc_types` in config maps category → suffix (e.g. `Bills` → `Monthly`, `Paystubs` → `Paycheck`). Date is `YYYY.MM` for monthly categories, `YYYY.MM.DD` for others.

**Config keys that control filename behavior**: `monthly_categories` (list), `account_in_filename_categories` (list), `no_amount_categories` (list), `doc_types` (map), `min_classification_score` (int, default 2). These live in the YAML and are passed through as plain dict values.

**Issuer matching**: `_normalize_for_match` strips `&` and `-` entirely (`AT&T`→`att`, `T-Mobile`→`tmobile`). Keys in `known_issuers` are tried longest-first so specific names win over short aliases. Matching is applied to the first 1500 chars, last 1000 chars, and filename.

**Contract detection**: When a CC or Bank statement has no `amount`, the router treats it as a contract — routes to `{category}/{issuer}` (no year folder), and the filename gets `Contract` as doc type regardless of `doc_types` config.

**`ocr_confidence`** is a naive heuristic (`min(len(text) / 5000.0, 1.0)`), not a real OCR confidence score.

**LLM is always opt-in.** Default config has `llm.enabled: false`; CLI flag `--no-llm` always wins. When Ollama is unreachable, `_maybe_build_llm_classifier` returns a `NullBackend`-wrapped classifier so the pipeline degrades to keyword-only with a single yellow warning. **Never** add a cloud LLM path silently — any cloud backend must require both an explicit env var AND an explicit `llm.cloud.enabled: true` opt-in (currently no cloud backend is implemented by design; this repo is local-only).

**Data lives in the project folder, not next to documents.** Feedback log, embedding store, and eval audit logs all default to `<cwd>/data/_feedback/` (gitignored). The `--output` flag points at the user's Documents tree (where filed PDFs live) but is NOT used for bookkeeping defaults. Override via env vars (`OCR_FEEDBACK_DIR`, `OCR_FEEDBACK_LOG`, `OCR_EMBEDDINGS_DB`) or config (`feedback.path`, `feedback.embeddings_db`). Resolution helpers: `_default_feedback_dir`, `_feedback_log_path`, `_resolve_feedback_path`, `_resolve_embed_db_path`.

**Recovery point.** Tag `pre-l4-baseline` on `master` marks the last keyword-only commit before the L1–L6 work landed. Roll back via `git checkout pre-l4-baseline` if needed.

**Test fixtures** in `conftest.py` provide `temp_dir`, `sample_pdf` (blank pypdf PDF), and `sample_config` (dict). Use these rather than creating new fixture patterns. **LLM tests use a `_FakeBackend`** and **embedding tests use a `FakeEmbedder`** — no real Ollama is needed to run the suite (one opt-in integration test is gated on `OLLAMA_TEST=1`).

## Environment Variables

```
PDF24_PATH    # Not currently wired; pdf24_path comes from config YAML or OcrSettings default
OCR_CONFIG_PATH  # Override default config file path
LOG_LEVEL     # INFO (default)
DEBUG         # false (default)
```

Store in `.env` (never commit). Use `.env.example` for templates.

## Target Folder Structure (Documents)

The output of this pipeline is `C:\Users\<user>\Documents`. The real folder taxonomy is:

### Financial — by issuer then year
| Category | Path pattern | Notes |
|---|---|---|
| `Bank Account & Statements` | `Bank Account & Statements/{issuer}/{year}` | Issuer examples: `Citibank`, `Chase Checking`, `Fidelity`, `PNC`, `Capital One` |
| `Credit Card Statements` | `Credit Card Statements/{issuer}/{year}` | Issuer may include owner suffixes such as `AMEX (A)`, `Bank of America (B)` |
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
Owner markers can be embedded in the **issuer name** as a suffix, e.g. `AMEX (A)`, `Bank of America (B)`. They are **not** a separate folder level in practice.

### Closed accounts convention
Inactive/closed issuers are moved to `_ Closed Accounts _` subdirectory within each category.

### `__downloads__` folder
`__downloads__` at the root is a staging area — files here are **not** processed by the OCR pipeline. New PDFs downloaded from bank/card portals land here before being processed.

## Config vs. Reality Gaps

The current `routing-config.yaml` has mismatches with the real folder structure:
- `Bills` template uses `{account}` but real path uses **issuer name**, not account number
- `Credit Card Statements` template uses `{account}` but real path uses **issuer name with owner suffix**
- `owners` values should stay generic in tracked config; owner labels can be embedded in issuer strings when needed
- `Bank Account & Statements` has no route template entry — it falls through to `default`

When adding features or fixing routing, align output paths to the conventions above.

## Security

- Never commit `.env`, `manifest.csv`, or `manifest.jsonl` — manifests contain document metadata
- Keep the repo private
