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


# ----------------------------------------------------------------------
# Fix 4 (2026-05-26 follow-up): zero-balance detection + safer labels
# ----------------------------------------------------------------------
"""Even after the date + collision fixes, the recovered amounts for the
6 Interbank statements were ALSO wrong (S/980, S/280, S/420 instead of
S/ 0.00). Root cause: the default amount_labels list included a lone
'total', which matched a column header like 'Redondeo Total' in the
Interbank breakdown table and grabbed the bare '980' on the next line.

Fixes verified by these tests:
  1. 'total' is no longer in _DEFAULT_AMOUNT_LABELS (too greedy).
  2. Spanish labels 'pago del mes' / 'deuda total' ARE in defaults.
  3. router.normalize_filename drops amount when it parses to 0.00 (defense
     in depth — even if some future extractor returns a labelled zero, no
     amount suffix appears in the filename).
"""

from ocr_router.extractor import _DEFAULT_AMOUNT_LABELS
from ocr_router.router import DocumentRouter


def test_bare_total_not_in_default_labels():
    """Regression: bare 'total' (last in the old list) caused 'Redondeo Total\n980' to be picked up as the statement amount."""
    assert 'total' not in _DEFAULT_AMOUNT_LABELS, (
        "Bare 'total' is too greedy — matches column headers in financial "
        "PDFs and grabs the next stray digit. Use more specific labels like "
        "'total amount due', 'pago del mes', etc."
    )


def test_spanish_labels_in_defaults():
    """Peruvian statement labels should be in the default list so the """
    """extractor finds them without requiring a custom YAML."""
    for label in ('pago del mes', 'deuda total'):
        assert label in _DEFAULT_AMOUNT_LABELS, f"missing default label: {label!r}"


def test_specific_total_labels_still_in_defaults():
    """Backward-compat: the specific 'total amount due' / 'total due' / """
    """'invoice total' labels are still present."""
    for label in ('total amount due', 'total due', 'invoice total', 'inv total'):
        assert label in _DEFAULT_AMOUNT_LABELS


def _router(extra_config: dict | None = None) -> DocumentRouter:
    cfg = {
        'categories': {'Credit Card Statements': ['statement balance']},
        'route_templates': {'default': '{category}/{issuer}/{year}'},
        'owners': [],
        'monthly_categories': ['Credit Card Statements'],
        'account_in_filename_categories': [],
        'no_amount_categories': [],
        'doc_types': {'Credit Card Statements': 'Statement'},
        'known_issuers': {},
        'extraction_patterns': {},
    }
    if extra_config:
        cfg.update(extra_config)
    return DocumentRouter(cfg)


def test_normalize_filename_zero_amount_dropped():
    """Regression: zero-balance Interbank statement should NOT carry """
    """'S/0.00' (or '$0.00') in the filename."""
    router = _router()
    name = router.normalize_filename(
        '20250221_xxx.pdf',
        {
            'date': '2025-02-21',
            'amount': '0',
            'currency': 'S/',
            'issuer': 'Interbank',
            'category': 'Credit Card Statements',
        },
    )
    assert 'S/0.00' not in name
    assert '$0.00' not in name
    assert name == '2025.02 - Interbank Statement.pdf'


def test_normalize_filename_zero_amount_float_dropped():
    """Same as above but raw_amount is a string '0.00' (typical extractor output)."""
    router = _router()
    name = router.normalize_filename(
        '20250221_xxx.pdf',
        {
            'date': '2025-02-21',
            'amount': '0.00',
            'currency': 'S/',
            'issuer': 'Interbank',
            'category': 'Credit Card Statements',
        },
    )
    assert '0.00' not in name
    assert name == '2025.02 - Interbank Statement.pdf'


def test_normalize_filename_nonzero_amount_kept():
    """Sanity: a real amount like 980.00 SHOULD appear in the filename."""
    router = _router()
    name = router.normalize_filename(
        '20250221_xxx.pdf',
        {
            'date': '2025-02-21',
            'amount': '980.00',
            'currency': 'S/',
            'issuer': 'Interbank',
            'category': 'Credit Card Statements',
        },
    )
    assert 'S_980.00' in name  # '/' sanitised to '_'


def test_normalize_filename_tiny_nonzero_amount_kept():
    """Sanity: 0.01 is still > 0 and should be preserved."""
    router = _router()
    name = router.normalize_filename(
        '20250221_xxx.pdf',
        {
            'date': '2025-02-21',
            'amount': '0.01',
            'currency': 'S/',
            'issuer': 'Interbank',
            'category': 'Credit Card Statements',
        },
    )
    assert 'S_0.01' in name  # '/' sanitised to '_'


# ----------------------------------------------------------------------
# Fix 5 (zero-balance detection): labelled-zero short-circuit
# ----------------------------------------------------------------------
"""Even with 'total' removed from defaults, the bare-currency fallback
would still grab the first ``S/ 2,800.00`` (credit line value) it sees
on an Interbank zero-balance statement. The two-pass labelled extractor
+ ``amount_zero`` flag stops the fallback when a real labelled 0.00 is
found, and the router uses the flag to avoid treating the statement as
a contract.
"""


def test_extract_amount_labelled_zero_returns_none_and_flag():
    """Direct unit test for the labelled-zero short-circuit."""
    ext = MetadataExtractor({
        'extraction_patterns': {
            'amount_labels': ['pago del mes', 'statement balance'],
        },
    })
    text = (
        "Tu Linea de Credito S/ 2,800.00 "
        "PAGO DEL MES (Suma de subtotales) - = 0.00 0.00 "
        "Saldo disponible S/ 2,800.49"
    )
    amount, was_zero = ext._extract_amount_with_zero_flag(text)
    assert amount is None
    assert was_zero is True


def test_extract_amount_labelled_nonzero_returns_value():
    """Sanity: a real non-zero labelled amount overrides any later zeros."""
    ext = MetadataExtractor({
        'extraction_patterns': {
            'amount_labels': ['statement balance', 'pago del mes'],
        },
    })
    text = (
        "Statement Balance: $1,234.56 "
        "PAGO DEL MES = 0.00"
    )
    amount, was_zero = ext._extract_amount_with_zero_flag(text)
    assert amount == '1234.56'
    assert was_zero is False


def test_extract_amount_loose_pass_collapses_dashes():
    """Loose pass must hop over long dash runs that exceed the 150-char cap."""
    ext = MetadataExtractor({
        'extraction_patterns': {'amount_labels': ['pago del mes']},
    })
    # 200 dashes between label and value would defeat the strict 150 cap;
    # the pre-normalisation collapses dashes to a single char.
    text = "PAGO DEL MES (Suma) " + ("-" * 200) + " = 0.00"
    amount, was_zero = ext._extract_amount_with_zero_flag(text)
    assert amount is None
    assert was_zero is True


def test_extract_amount_loose_pass_collapses_double_space():
    """Interbank text has 'PAGO  DEL MES' (double space); whitespace normalisation must let the single-space label match."""
    ext = MetadataExtractor({
        'extraction_patterns': {'amount_labels': ['pago del mes']},
    })
    text = "PAGO  DEL MES (Suma) -- = 0.00"
    amount, was_zero = ext._extract_amount_with_zero_flag(text)
    assert amount is None
    assert was_zero is True


def test_extract_amount_no_label_falls_through_to_currency():
    """With no labelled match at all, fallback finds the first $|S/ amount with cents."""
    ext = MetadataExtractor({'extraction_patterns': {'amount_labels': []}})
    amount, was_zero = ext._extract_amount_with_zero_flag("Total $1,234.56 due now")
    assert amount == '1234.56'
    assert was_zero is False


def test_extract_from_text_propagates_amount_zero_flag():
    """The flag should be visible on the metadata dict consumed by router."""
    ext = MetadataExtractor({
        'extraction_patterns': {'amount_labels': ['pago del mes']},
    })
    text = "PAGO DEL MES (Suma de subtotales) - = 0.00 0.00"
    meta = ext.extract_from_text(text, '20250221_xxx.pdf')
    assert meta['amount'] is None
    assert meta['amount_zero'] is True


def test_router_zero_balance_cc_is_not_a_contract():
    """Regression: an Interbank zero-balance statement should NOT be """
    """renamed `... Contract.pdf` and routed to the issuer root. It is """
    """a regular monthly statement that nets to zero."""
    router = _router()
    meta = {
        'date': '2025-02-21',
        'amount': None,
        'amount_zero': True,
        'currency': 'S/',
        'issuer': 'Interbank',
        'category': 'Credit Card Statements',
    }
    name = router.normalize_filename('20250221_xxx.pdf', meta)
    route = router.build_route_path('Credit Card Statements', meta)
    assert 'Contract' not in name
    assert name == '2025.02 - Interbank Statement.pdf'
    # Goes to the year folder, not the bare issuer root.
    assert route == 'Credit Card Statements\\Interbank\\2025'


def test_router_truly_no_amount_cc_still_treated_as_contract():
    """Sanity: a CC PDF where the extractor found NO amount at all (and """
    """set amount_zero=False) should still be treated as a contract — """
    """preserves existing behaviour for actual cardmember-agreement PDFs."""
    router = _router()
    meta = {
        'date': '2024-08-10',
        'amount': None,
        'amount_zero': False,  # extractor found NO labelled value
        'currency': '$',
        'issuer': 'AMEX Gold',
        'category': 'Credit Card Statements',
    }
    name = router.normalize_filename('ContratoTarjetaCredito.pdf', meta)
    assert 'Contract' in name
