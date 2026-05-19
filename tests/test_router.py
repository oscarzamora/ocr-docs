"""Regression tests for router behavior."""

from ocr_router.router import DocumentRouter


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
