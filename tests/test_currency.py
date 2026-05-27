"""Tests for the currency detection fix (Bug 3).

The pre-fix logic returned the FIRST symbol found in priority order
(S/ > € > £ > $), which meant a single stray '€' in a clearly-USD document
flipped the whole filename's currency. The fix uses majority vote with a
hard bias toward '$'.
"""

from __future__ import annotations

import pytest

from ocr_router.extractor import MetadataExtractor


@pytest.fixture
def extractor():
    return MetadataExtractor({})


def test_pure_usd_returns_dollar(extractor):
    text = "Total $1,052.01. Available credit $5,000.00. Min payment due $25.00."
    assert extractor._extract_currency(text) == "$"


def test_pure_soles_returns_S_slash(extractor):
    text = "Saldo S/ 3.05  Pago mínimo S/ 25.00  Crédito disponible S/ 5,000.00"
    assert extractor._extract_currency(text) == "S/"


def test_pure_euro_returns_euro(extractor):
    text = "Total €1,234.56  Disponible €5,000.00"
    assert extractor._extract_currency(text) == "€"


def test_stray_euro_in_usd_doc_does_NOT_flip_currency(extractor):
    """Regression for Bug 3: one stray '€' (perhaps OCR noise) in a doc
    with many '$' should not flip the currency. This was the actual bug
    that produced '€1052.01' filenames from Fidelity Visa statements."""
    text = (
        "Statement Balance $1,052.01  Previous Balance $980.00  "
        "Payments $-300.00  Purchases $372.01  "
        "Min Payment Due $25.00  Credit Line $10,000.00  "
        "(FX note: 1 EUR ≈ $1.08 €1)"  # the stray €
    )
    assert extractor._extract_currency(text) == "$"


def test_majority_soles_wins_over_one_dollar(extractor):
    """Interbank statement in Soles with a tiny '$' equivalent footnote
    should still be S/."""
    text = (
        "Cuenta Interbank  Saldo S/ 314.09  Pago mínimo S/ 31.40  "
        "Disponible S/ 5,000.00  "
        "(equivalente $1 US)"
    )
    assert extractor._extract_currency(text) == "S/"


def test_no_currency_defaults_to_dollar(extractor):
    text = "No money symbols anywhere in this text at all."
    assert extractor._extract_currency(text) == "$"


def test_tie_breaks_to_dollar(extractor):
    """If $ count equals another currency's count, $ wins. Conservative
    default for US-context documents."""
    text = "$10 €10"
    assert extractor._extract_currency(text) == "$"
