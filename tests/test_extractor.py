"""Tests for extractor module."""

import pytest

from ocr_router.extractor import MetadataExtractor, PdfTextExtractor


def test_extract_date():
    """Test date extraction from text."""
    extractor = MetadataExtractor({})
    
    text = "Statement Date: 01/15/2024"
    result = extractor._extract_date(text)
    
    assert result is not None
    assert "01" in result and "15" in result and "2024" in result


def test_extract_amount():
    """Test amount extraction from text."""
    extractor = MetadataExtractor({})
    
    text = "Total Amount Due: $1,234.56"
    result = extractor._extract_amount(text)
    
    assert result is not None
    assert "1234" in result or "1,234" in result


def test_classify_document():
    """Test document classification."""
    from ocr_router.router import DocumentRouter
    
    config = {
        'categories': {
            'Bills': ['amount due', 'billing period'],
            'Credit Card': ['new balance', 'statement balance'],
        },
        'route_templates': {},
        'owners': [],
    }
    
    router = DocumentRouter(config)
    
    text = "Your statement balance is $500. New balance: $250."
    category = router.classify_document(text)
    
    assert category == 'Credit Card'
