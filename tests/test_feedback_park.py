"""Tests for the 'park' lifecycle (Step 5.5).

Covers:
- FeedbackLog.parked_filenames() correctly tracks park / unpark / re-confirm
- _record_parked writes one event='parked' record per file with the right
  metadata so future runs can pre-filter on filename.
"""

from pathlib import Path

import pytest

from ocr_router.feedback import FeedbackLog, FeedbackRecord


def _park(log: FeedbackLog, name: str, folder: str = "__downloads__") -> None:
    """Append a parked record."""
    rec = FeedbackRecord.from_proposal(
        event="parked",
        original_filename=name,
        text="t",
        proposal_meta={"category": "Uncategorized"},
        proposed_folder=folder,
        proposed_filename=name,
        proposed_confidence=1.0,
        final_folder=folder,
        final_filename=name,
        backend="keyword",
        extra={"parked_at": folder},
    )
    log.append(rec)


def _confirm(log: FeedbackLog, name: str) -> None:
    log.append(FeedbackRecord.from_proposal(
        event="confirmed",
        original_filename=name,
        text="t",
        proposal_meta={"category": "Bills"},
        proposed_folder="Bills",
        proposed_filename=name,
        final_category="Bills",
        final_folder="Bills/FPL/2026",
        final_filename=name,
        backend="keyword",
    ))


def _unpark(log: FeedbackLog, name: str) -> None:
    log.append(FeedbackRecord.from_proposal(
        event="unparked",
        original_filename=name,
        text="",
        proposal_meta={},
        proposed_folder=None,
        proposed_filename=None,
        backend="manual-unpark",
    ))


# ---------------------------------------------------------------------------

def test_parked_filenames_returns_empty_when_no_records(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    assert log.parked_filenames() == {}


def test_parked_filenames_returns_actively_parked(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    _park(log, "a.pdf")
    _park(log, "b.pdf")
    parked = log.parked_filenames()
    assert set(parked.keys()) == {"a.pdf", "b.pdf"}


def test_unpark_removes_from_parked_set(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    _park(log, "x.pdf")
    assert "x.pdf" in log.parked_filenames()
    _unpark(log, "x.pdf")
    assert "x.pdf" not in log.parked_filenames()


def test_subsequent_confirm_releases_park(temp_dir):
    """If a parked file is later 'confirmed' (i.e. moved on a later run),
    it should no longer be in the parked set."""
    log = FeedbackLog(temp_dir / "c.jsonl")
    _park(log, "y.pdf")
    assert "y.pdf" in log.parked_filenames()
    _confirm(log, "y.pdf")
    assert "y.pdf" not in log.parked_filenames()


def test_park_after_unpark_re_parks(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    _park(log, "z.pdf")
    _unpark(log, "z.pdf")
    _park(log, "z.pdf")
    assert "z.pdf" in log.parked_filenames()


def test_parked_record_carries_location(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    _park(log, "doc.pdf", folder="__downloads__\\manual")
    parked = log.parked_filenames()
    assert parked["doc.pdf"]["extra"]["parked_at"] == "__downloads__\\manual"


# ---------------------------------------------------------------------------
# Pre-analysis filter contract: the set of filenames to skip is exactly
# `parked_filenames()`. cli.process uses this to remove parked PDFs before
# they're even OCR'd.
# ---------------------------------------------------------------------------

def test_pre_filter_excludes_parked(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    _park(log, "skip-me.pdf")

    candidate_filenames = [
        "skip-me.pdf",         # parked → excluded
        "process-me.pdf",      # not in log → included
        "also-process.pdf",    # not in log → included
    ]
    parked = log.parked_filenames()
    survivors = [n for n in candidate_filenames if n not in parked]
    assert survivors == ["process-me.pdf", "also-process.pdf"]


def test_pre_filter_keeps_unparked_files(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    _park(log, "a.pdf")
    _unpark(log, "a.pdf")
    parked = log.parked_filenames()
    # After unpark, the file is processable again
    assert "a.pdf" not in parked
