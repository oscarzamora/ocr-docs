"""Regression tests for router behavior."""

from ocr_router.router import DocumentRouter, _description_from_stem


def test_contract_route_path_drops_year_segment():
    """Contract-like CC docs should route to issuer root without year."""
    config = {
        "categories": {},
        "route_templates": {
            "default": "{category}/{issuer}/{year}",
            "Credit Card Statements": "Credit Card Statements/{issuer}/{year}",
        },
        "owners": [],
    }

    router = DocumentRouter(config)
    path = router.build_route_path(
        "Credit Card Statements",
        {
            "issuer": "AMEX (A)",
            "amount": "",
            "date": "2024-01-10",
        },
    )

    assert path == "Credit Card Statements\\AMEX (A)"


def test_description_from_stem_strips_generic_words():
    """Stem description should strip category/doc-type words and keep meaningful ones."""
    assert _description_from_stem("rental filters", "Invoice") == "Filters"
    assert _description_from_stem("home depot receipt", "Receipt") == "Home Depot"
    assert _description_from_stem("invoice", "Invoice") == ""
    assert _description_from_stem("pool service hose repair", "Receipt") == "Pool Hose Repair"


def test_normalize_filename_rental_includes_stem_description():
    """Rental Expenses filename should include issuer, stem description, doc type, and amount."""
    config = {
        "categories": {},
        "route_templates": {},
        "owners": [],
        "doc_types": {"Rental Expenses": "Invoice"},
        "monthly_categories": [],
        "account_in_filename_categories": [],
        "no_amount_categories": [],
        "description_from_filename_categories": ["Rental Expenses"],
    }
    router = DocumentRouter(config)
    metadata = {
        "category": "Rental Expenses",
        "date": "2026-06-04",
        "date_year_only": False,
        "issuer": "Home Depot",
        "owner": None,
        "account": None,
        "account_masked": False,
        "account_digits": 0,
        "amount": "14.97",
        "currency": "$",
    }
    name = router.normalize_filename("rental filters.pdf", metadata)
    assert name == "2026.06.04 - Home Depot Filters Invoice - $14.97.pdf"


def test_normalize_filename_hsa_includes_owner():
    """HSA & FSA filename should include recipient name."""
    config = {
        "categories": {},
        "route_templates": {},
        "owners": [],
        "doc_types": {"HSA & FSA Transactions": "EOB"},
        "monthly_categories": [],
        "account_in_filename_categories": [],
        "no_amount_categories": [],
        "description_from_filename_categories": [],
    }
    router = DocumentRouter(config)
    metadata = {
        "category": "HSA & FSA Transactions",
        "date": "2026-05-28",
        "date_year_only": False,
        "issuer": "Example Dental",
        "owner": "Jane Doe",
        "account": None,
        "account_masked": False,
        "account_digits": 0,
        "amount": "181.00",
        "currency": "$",
    }
    name = router.normalize_filename("Report_000001.pdf", metadata)
    assert name == "2026.05.28 - Example Dental EOB Jane Doe - $181.00.pdf"
