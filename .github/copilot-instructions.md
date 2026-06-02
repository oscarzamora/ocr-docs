# Copilot Instructions

## Build & Commands

```bash
# Install (activate venv first)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run CLI
python -m ocr_router.cli process --input "data/input" --output "data/output" --config config/routing-config.yaml

# Run tests
pytest tests/

# Format and lint
black src/
flake8 src/
```

Black line length is 100 (`pyproject.toml`).

## Architecture

Pipeline components wired in `cli.py`:

```
OcrEngine -> PdfTextExtractor -> MetadataExtractor -> DocumentRouter -> FolderResolver -> ManifestWriter
```

Optional local-first classifier path:

```
LLMClassifier (Ollama) -> EmbeddingStore (SQLite)
```

## Core conventions

- Pass config as a plain dict (`cfg.model_dump()`) to pipeline components.
- Keep routing and filename behavior in YAML config, not hardcoded rules.
- Strip unknown path segments in routes instead of creating `Unknown` folders.
- Keep history logs append-only.
- Reuse OCR cache and skip re-OCR when text layer already exists.

## LLM conventions

- LLM is opt-in and local-only.
- `--no-llm` must always disable LLM classification.
- If local backend is unavailable, degrade gracefully to keyword-only behavior.
- Do not add cloud LLM paths silently.

## Data and privacy

- Keep feedback and embeddings under project-local `data/_feedback/` by default.
- Do not commit sensitive artifacts (`.env`, manifests, OCR cache, local feedback logs).
- Do not include personal names, account identifiers, addresses, or employer names in docs/examples.
- Keep all OCR/LLM document processing local.

## Testing

- Prefer deterministic unit tests with fake backends where possible.
- Keep evaluation harness read-only and reproducible.
