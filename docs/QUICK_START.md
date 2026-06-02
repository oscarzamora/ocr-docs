# OCR Router - Quick Start Guide

## Installation

### 1. Setup Virtual Environment

```powershell
cd C:\path\to\ocr-docs
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Verify PDF24 Installation

```powershell
Test-Path "C:\Program Files\PDF24\pdf24-Ocr.exe"
```

If not found, download from https://www.pdf24.org/

## Usage

### Auto Mode - Process PDFs

```powershell
python -m ocr_router process `
  --input "C:\path\to\input\pdfs" `
  --output "C:\path\to\output" `
  --config config/routing-config.yaml
```

### Options

- `--skip-ocr`: Skip OCR step (text extraction only)
- `--max-files N`: Process only first N files (for testing)
- `--no-archive`: Don't keep original PDFs

### Review Results

```powershell
python -m ocr_router review --manifest data/output/manifest.jsonl
```

## Configuration

Edit `config/routing-config.yaml`:

1. **owners**: List of document owners
2. **categories**: Document types and keywords
3. **route_templates**: Output folder structure
4. **extraction_patterns**: Metadata extraction rules

## Output Structure

```
output/
├── Bills/
│   └── {account}/{year}/
├── Credit Card Statements/
│   └── {account}/{year}/
├── HSA & FSA Transactions/
│   └── {year}/
├── Notices/
│   └── {year}/
├── _processed-originals/
├── manifest.csv
└── manifest.jsonl
```

## Examples

### Process with options

```powershell
# Skip OCR for testing
python -m ocr_router process `
  --input "C:\test" `
  --output "C:\output" `
  --skip-ocr `
  --max-files 5

# Process and keep originals archived
python -m ocr_router process `
  --input "C:\documents" `
  --output "C:\routed" `
  --config config/routing-config.yaml
```

## Troubleshooting

### PDF24 not found
- Install PDF24 from https://www.pdf24.org/
- Or use `--skip-ocr` flag to extract text only

### Can't activate venv
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\venv\Scripts\Activate.ps1
```

### Module not found
```powershell
pip install -r requirements.txt --upgrade
```

## Next Steps

1. Customize `config/routing-config.yaml` with your categories
2. Test with `--skip-ocr --max-files 3`
3. Review `manifest.jsonl` output
4. Run full auto mode on all PDFs
