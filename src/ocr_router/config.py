"""Configuration management for OCR Router."""

import os
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel, ConfigDict


class RoutingTemplates(BaseModel):
    """Routing path templates."""
    model_config = ConfigDict(extra='allow')


class CategoryPatterns(BaseModel):
    """Category classification patterns."""
    model_config = ConfigDict(extra='allow')


class ExtractionPatterns(BaseModel):
    """Metadata extraction patterns."""
    date_formats: list[str] = []
    amount_regex: str = ""
    account_regex: str = ""


class OcrSettings(BaseModel):
    """OCR engine settings."""
    pdf24_path: str = r"C:\Program Files\PDF24\pdf24-Ocr.exe"
    keepOriginal: bool = True
    archiveFolder: str = "_processed-originals"


class ManifestSettings(BaseModel):
    """Manifest generation settings."""
    csv_path: str = "manifest.csv"
    jsonl_path: str = "manifest.jsonl"
    include_fields: list[str] = []


class RoutingConfig(BaseModel):
    """Complete routing configuration."""
    model_config = ConfigDict(extra='allow')
    
    owners: list[str] = []
    route_templates: Dict[str, str] = {}
    categories: Dict[str, list[str]] = {}
    extraction_patterns: ExtractionPatterns = ExtractionPatterns()
    ocr_settings: OcrSettings = OcrSettings()
    manifest: ManifestSettings = ManifestSettings()


def load_config(config_path: str | Path) -> RoutingConfig:
    """Load configuration from YAML file."""
    config_path = Path(config_path)
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    
    return RoutingConfig(**data)


def get_config_from_env() -> str:
    """Get config path from environment or use default."""
    default = Path(__file__).parent.parent.parent / "config" / "routing-config.yaml"
    return os.getenv("OCR_CONFIG_PATH", str(default))
