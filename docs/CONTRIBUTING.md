# Contributing

## Development Setup

### 1. Clone and Install

```bash
cd ocr-docs
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pytest pytest-cov black flake8
```

### 2. Code Style

Use Black for formatting:

```bash
black src/
```

Lint with flake8:

```bash
flake8 src/
```

### 3. Testing

```bash
pytest tests/
pytest --cov=src tests/  # with coverage
```

## Project Structure Rules

- `src/ocr_router/`: Main package code
- `config/`: Configuration templates
- `data/`: Input/output data
- `tests/`: Unit and integration tests
- `docs/`: Documentation

## Adding Features

1. Create feature branch: `git checkout -b feature/my-feature`
2. Implement with tests
3. Run linting and tests
4. Create pull request

## Key Files to Modify

- **Add category**: Edit `config/routing-config.yaml` → `categories`
- **Change routing**: Edit `config/routing-config.yaml` → `route_templates`
- **Add CLI command**: Edit `src/ocr_router/cli.py` → add `@cli.command()`
- **Improve extraction**: Edit `src/ocr_router/extractor.py` → `MetadataExtractor`
