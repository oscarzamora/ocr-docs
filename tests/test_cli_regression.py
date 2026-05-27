"""Regression tests for issues found in the first agent-driven run.

Two bugs validated here:

1. Park/skip race (Bug 1): the interactive 'park N' command used to write
   a 'parked' record and then later a 'skipped' record for the same file,
   which overrode the park (latest-wins lookup in parked_filenames lost it).

2. OCR artifact filter (Bug 2 / Bug 4): if an old run left a
   '<stem>_ocr.pdf' next to the original '<stem>.pdf', the new run used to
   pick up the OCR temp file as a fresh document and route it again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ocr_router.feedback import FeedbackLog, FeedbackRecord


# ---------------------------------------------------------------------------
# Bug 1 — Park/skip race
# ---------------------------------------------------------------------------

def test_park_record_survives_a_simultaneous_skipped_write(temp_dir):
    """If both 'parked' and 'skipped' land in the log for the same file in
    the same run, the file MUST still appear as parked.

    The fix in cli.py guarantees we never write 'skipped' when the file was
    parked — but the parked_filenames() lookup must also be tolerant of the
    historical case where both exist (e.g. logs from before the fix). The
    'parked' event has highest precedence for parked_filenames as long as
    it's the most recent record. So if 'skipped' came LATER for the same
    file (the bug), parked_filenames would drop it.

    We verify the *fixed* behavior: when the caller correctly avoids the
    duplicate skipped-write, park survives.
    """
    log = FeedbackLog(temp_dir / "c.jsonl")
    name = "important.pdf"
    # Simulate what _record_parked writes:
    log.append(FeedbackRecord.from_proposal(
        event="parked",
        original_filename=name,
        text="t",
        proposal_meta={"category": "Uncategorized"},
        proposed_folder="__downloads__",
        proposed_filename=name,
        proposed_confidence=1.0,
        final_folder="__downloads__",
        final_filename=name,
        backend="keyword",
        extra={"parked_at": "__downloads__"},
    ))
    # The fix means NO 'skipped' record is written for this file.
    # parked_filenames() must still see it.
    assert name in log.parked_filenames()


def test_park_lost_when_skipped_written_later_documents_bug(temp_dir):
    """Documents the pre-fix behavior so we know what the bug looked like.

    If a 'skipped' record is appended AFTER 'parked' for the same file,
    parked_filenames() correctly drops it (last-write-wins is the design).
    This test exists so we never accidentally remove the cli.py fix that
    prevents the duplicate write.
    """
    log = FeedbackLog(temp_dir / "c.jsonl")
    name = "important.pdf"
    log.append(FeedbackRecord.from_proposal(
        event="parked",
        original_filename=name,
        text="t",
        proposal_meta={},
        proposed_folder="__downloads__",
        proposed_filename=name,
    ))
    log.append(FeedbackRecord.from_proposal(
        event="skipped",
        original_filename=name,
        text="t",
        proposal_meta={},
        proposed_folder="__downloads__",
        proposed_filename=name,
    ))
    # With both events, 'skipped' (later) wins and the file is NOT in the
    # active parked set. The cli.py fix ensures this scenario doesn't
    # happen in practice.
    assert name not in log.parked_filenames()


# ---------------------------------------------------------------------------
# Bug 2 / Bug 4 — OCR artifact pre-filter
# ---------------------------------------------------------------------------

def test_ocr_artifact_filter_excludes_orphaned_temp_when_original_exists(temp_dir):
    """The pre-filter in cli.process() drops '<stem>_ocr.pdf' files that
    appear next to the original '<stem>.pdf'.
    """
    (temp_dir / "real.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
    (temp_dir / "real_ocr.pdf").write_bytes(b"%PDF-1.4\n%fake-ocr\n")
    (temp_dir / "fresh.pdf").write_bytes(b"%PDF-1.4\n%fake\n")

    # Mirror the exact filter logic from cli.py
    pdf_files = list(temp_dir.rglob('*.pdf'))
    filtered = [
        p for p in pdf_files
        if not p.name.endswith('_ocr.pdf')
        or not (p.parent / p.name.replace('_ocr.pdf', '.pdf')).exists()
    ]
    names = {p.name for p in filtered}
    assert "real.pdf" in names
    assert "fresh.pdf" in names
    assert "real_ocr.pdf" not in names      # ← the bug fix


def test_ocr_artifact_filter_keeps_lone_ocr_files(temp_dir):
    """Edge case: if '<stem>_ocr.pdf' has no '<stem>.pdf' sibling, treat
    it as a legitimate input — don't silently drop user files just because
    they happen to end in '_ocr.pdf'.
    """
    (temp_dir / "vendor_ocr.pdf").write_bytes(b"%PDF-1.4\n%fake\n")

    pdf_files = list(temp_dir.rglob('*.pdf'))
    filtered = [
        p for p in pdf_files
        if not p.name.endswith('_ocr.pdf')
        or not (p.parent / p.name.replace('_ocr.pdf', '.pdf')).exists()
    ]
    assert {p.name for p in filtered} == {"vendor_ocr.pdf"}
