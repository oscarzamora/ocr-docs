"""Tests for history-log path resolution (Option B: monthly auto-rotation)."""

from pathlib import Path

import pytest

from ocr_router.cli import _resolve_history_path


RUN_TIME = "2026-05-26 14:32"


def test_default_path_uses_output_dir(temp_dir):
    p = _resolve_history_path(temp_dir, RUN_TIME, config={})
    assert p == temp_dir / "PROCESSED_PDFS.md"


def test_default_path_when_no_config(temp_dir):
    p = _resolve_history_path(temp_dir, RUN_TIME, config=None)
    assert p == temp_dir / "PROCESSED_PDFS.md"


def test_explicit_path_wins_over_monthly(temp_dir):
    explicit = temp_dir / "custom" / "log.md"
    p = _resolve_history_path(
        temp_dir, RUN_TIME,
        config={"history": {"path": str(explicit), "monthly": True}},
    )
    assert p == explicit


def test_monthly_mode_under_output_dir(temp_dir):
    p = _resolve_history_path(
        temp_dir, RUN_TIME,
        config={"history": {"monthly": True}},
    )
    assert p == temp_dir / "2026.05 - PROCESSED_PDFS.md"


def test_monthly_mode_under_custom_dir(temp_dir):
    custom = temp_dir / "__downloads__"
    p = _resolve_history_path(
        temp_dir, RUN_TIME,
        config={"history": {"monthly": True, "dir": str(custom)}},
    )
    assert p == custom / "2026.05 - PROCESSED_PDFS.md"


def test_monthly_label_changes_with_month(temp_dir):
    p1 = _resolve_history_path(
        temp_dir, "2026-12-01 09:00", config={"history": {"monthly": True}},
    )
    p2 = _resolve_history_path(
        temp_dir, "2027-01-15 10:00", config={"history": {"monthly": True}},
    )
    assert p1.name == "2026.12 - PROCESSED_PDFS.md"
    assert p2.name == "2027.01 - PROCESSED_PDFS.md"
    assert p1 != p2
