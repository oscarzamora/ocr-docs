"""Fixtures and test utilities."""

import json
import tempfile
from pathlib import Path

import pytest
from pypdf import PdfWriter


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_pdf(temp_dir):
    """Create a sample PDF for testing."""
    pdf = PdfWriter()
    pdf.add_blank_page(width=200, height=200)
    
    pdf_path = temp_dir / "sample.pdf"
    with open(pdf_path, 'wb') as f:
        pdf.write(f)
    
    return pdf_path


@pytest.fixture
def sample_config():
    """Sample configuration for testing."""
    return {
        'owners': ['Test Owner'],
        'categories': {
            'Bills': ['amount due', 'billing period'],
            'Credit Card': ['new balance', 'credit card'],
        },
        'route_templates': {
            'default': '{category}/{issuer}/{owner}/{year}',
            'Bills': 'Bills/{account}/{year}',
        },
        'extraction_patterns': {
            'date_formats': ['%m/%d/%Y'],
            'amount_regex': r'\$([0-9.]+)',
            'account_regex': r'Account: ([0-9]{4})',
        },
    }
