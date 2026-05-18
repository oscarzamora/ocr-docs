# Architecture

## Components

### 1. OCR Engine (`ocr_engine.py`)
- Handles PDF24 OCR integration
- Converts PDFs to OCR'd versions
- Error handling for missing PDF24

### 2. Text Extractor (`extractor.py`)
- **PdfTextExtractor**: Extracts raw text from PDFs using pypdf
- **MetadataExtractor**: Identifies dates, amounts, accounts, owners, issuers

### 3. Router (`router.py`)
- **DocumentRouter**: 
  - Classifies documents by analyzing text against categories
  - Builds destination paths using templates
  - Normalizes filenames with metadata

### 4. Manifest (`manifest.py`)
- **ManifestEntry**: Single document record
- **ManifestWriter**: Writes to CSV and JSONL for tracking

### 5. Configuration (`config.py`)
- Loads YAML configuration
- Pydantic validation for type safety
- Environment variable overrides

### 6. CLI (`cli.py`)
- **process**: Main command for batch processing
- **review**: View manifest entries
- Rich console output with progress bars

## Workflow (Auto Mode)

```
Input PDF
   ↓
[1] OCR using PDF24 → OCR'd PDF
   ↓
[2] Extract Text → Raw text
   ↓
[3] Extract Metadata → date, amount, owner, account, issuer
   ↓
[4] Classify Category → Match keywords against categories
   ↓
[5] Build Route Path → {category}/{issuer}/{owner}/{year}
   ↓
[6] Normalize Filename → date_amount_issuer_basename.pdf
   ↓
[7] Copy/Move File → destination/{route_path}/filename
   ↓
[8] Archive Original → _processed-originals/
   ↓
[9] Log Entry → manifest.csv + manifest.jsonl
   ↓
Output: Organized folder structure + Manifest
```

## Configuration Structure

```yaml
owners: [...list of document owners...]

route_templates:
  default: "{category}/{issuer}/{owner}/{year}"
  Bills: "Bills/{account}/{year}"
  # ... category-specific templates

categories:
  Credit Card Statements: [keywords...]
  Bills: [keywords...]
  # ... more categories

extraction_patterns:
  date_formats: [date regex patterns...]
  amount_regex: "regex to find amounts"
  account_regex: "regex to find accounts"

ocr_settings:
  pdf24_path: "path to pdf24-Ocr.exe"
  keepOriginal: true
  archiveFolder: "_processed-originals"

manifest:
  csv_path: "manifest.csv"
  jsonl_path: "manifest.jsonl"
  include_fields: [fields to include...]
```

## Extension Points

- **Custom Categories**: Add keywords to `categories` in YAML
- **Route Templates**: Modify path structure in `route_templates`
- **Metadata Extraction**: Add patterns to `extraction_patterns`
- **CLI Commands**: Add new commands to `cli.py`

## Error Handling

- PDF24 unavailable → Falls back to text extraction
- OCR failure → Uses original PDF
- Missing metadata → Uses "Unknown" or omits from path
- Invalid characters in filename → Sanitized with underscores
