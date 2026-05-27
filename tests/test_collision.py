"""Regression tests for the 2026-05-26 Interbank silent-overwrite incident.

Bug: 6 Interbank PDFs whose date extractor returned the same year-only
fallback (2025-01-01) normalised to 3 distinct filenames; shutil.copy2
silently overwrote 3 of them on disk.

Two complementary fixes are tested:
  1. _extract_date_from_filename now pulls YYYYMMDD from the filename stem
     (so the 6 files get 6 distinct dates and 6 distinct normalised names).
  2. _resolve_collision now accepts a per-batch ``reserved`` set so even if
     the date extractor fails AGAIN for some future format, the second move
     gets a ``- 2`` suffix instead of clobbering the first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ocr_router.cli import _resolve_collision
from ocr_router.extractor import MetadataExtractor


# Fix 1: filename-stem date extraction
# ------------------------------------------------------------------

@pytest.fixture
def extractor():
    return MetadataExtractor({})


def test_filename_yyyymmdd_at_start(extractor):
    assert extractor._extract_date_from_filename(
        "20250221_37775302340019750160.pdf"
    ) == "2025-02-21"


def test_filename_yyyy_dash_mm_dash_dd(extractor):
    assert extractor._extract_date_from_filename(
        "2026-04-14 - Fidelity Statement.pdf"
    ) == "2026-04-14"


def test_filename_yyyy_dot_mm_dot_dd(extractor):
    assert extractor._extract_date_from_filename(
        "2025.12.29 - 911 Carrera T.pdf"
    ) == "2025-12-29"


def test_filename_yyyy_dot_mm_only(extractor):
    assert extractor._extract_date_from_filename(
        "2026.05 - Fidelity Rewards Visa.pdf"
    ) == "2026-05-01"


def test_filename_no_date_returns_none(extractor):
    assert extractor._extract_date_from_filename(
        "random_no_date_here.pdf"
    ) is None


def test_filename_bare_year_does_not_match(extractor):
    assert extractor._extract_date_from_filename(
        "someprefix2025somesuffix.pdf"
    ) is None


def test_extract_from_text_prefers_filename_date_over_year_only_fallback(extractor):
    meta = extractor.extract_from_text(
        text="Some statement text mentioning 2025 but no specific date.",
        filename="20250221_37775302340019750160.pdf",
    )
    assert meta["date"] == "2025-02-21"
    assert meta["date_year_only"] is False


def test_six_interbank_filenames_yield_six_distinct_dates(extractor):
    filenames = [
        "20250221_37775302340019750160.pdf",
        "20250321_37775302340019750160.pdf",
        "20250421_37775302340019750160.pdf",
        "20250521_37775302340019750160.pdf",
        "20250620_37775302340019750160.pdf",
        "20250721_37775302340019750160.pdf",
    ]
    dates = {extractor._extract_date_from_filename(fn) for fn in filenames}
    assert len(dates) == 6, f"Expected 6 unique dates, got {dates}"


# DD/MM/YYYY fallback for LATAM / European text dates
def test_text_ddmm_yyyy_when_dd_gt_12(extractor):
    """21/02/2025 cannot be MM/DD (21 > 12) so should be parsed as DD/MM."""
    assert extractor._extract_date("Fecha de corte: 21/02/2025") == "2025-02-21"


def test_text_mmdd_yyyy_us_format_still_works(extractor):
    assert extractor._extract_date("Statement Date: 02/21/2025") == "2025-02-21"


# Fix 2: per-batch collision detection
# ------------------------------------------------------------------

def test_collision_disk_only_legacy_behaviour(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    out = _resolve_collision(tmp_path, "a.pdf")
    assert out.name == "a - 2.pdf"


def test_collision_reserved_set_prevents_same_batch_overwrite(tmp_path: Path):
    reserved: set[str] = set()
    first  = _resolve_collision(tmp_path, "statement.pdf", reserved=reserved)
    second = _resolve_collision(tmp_path, "statement.pdf", reserved=reserved)
    third  = _resolve_collision(tmp_path, "statement.pdf", reserved=reserved)
    assert first.name  == "statement.pdf"
    assert second.name == "statement - 2.pdf"
    assert third.name  == "statement - 3.pdf"
    assert {str(first), str(second), str(third)} == reserved


def test_collision_reserved_set_respects_disk_too(tmp_path: Path):
    (tmp_path / "x.pdf").write_bytes(b"y")
    reserved: set[str] = set()
    first  = _resolve_collision(tmp_path, "x.pdf", reserved=reserved)
    second = _resolve_collision(tmp_path, "x.pdf", reserved=reserved)
    assert first.name  == "x - 2.pdf"
    assert second.name == "x - 3.pdf"


# Fix 3: S/. (formal Peruvian Soles notation) currency detection
# ------------------------------------------------------------------

def test_currency_s_slash_period_dot(extractor):
    """S/. with a period is the formal Peruvian notation; should count as S/."""
    text = "Saldo S/. 314.09  Pago minimo S/. 31.40  Disponible S/. 5,000.00"
    assert extractor._extract_currency(text) == "S/"


def test_currency_mixed_s_slash_variants(extractor):
    text = "Total S/ 100.00 mas S/. 50.00 mas S/200.00"
    assert extractor._extract_currency(text) == "S/"
