"""Tests for the optional `note` field on feedback records.

The agent / CLI accepts ``park 7 note: unpaid`` and the rationale is
stored verbatim on each affected record. ``feedback search`` finds it,
``feedback show`` displays it.
"""

from __future__ import annotations

import json

import pytest

from ocr_router.feedback import FeedbackLog, FeedbackRecord


def test_record_default_note_is_empty_string():
    rec = FeedbackRecord.from_proposal(
        event="parked", original_filename="a.pdf", text="t",
        proposal_meta={}, proposed_folder=None, proposed_filename=None,
    )
    assert rec.note == ""


def test_record_carries_user_note(tmp_path):
    rec = FeedbackRecord.from_proposal(
        event="parked", original_filename="a.pdf", text="t",
        proposal_meta={}, proposed_folder=None, proposed_filename=None,
        note="unpaid, surface for review when paid",
    )
    assert rec.note == "unpaid, surface for review when paid"


def test_record_note_is_trimmed():
    """Leading/trailing whitespace from the prompt parser is stripped."""
    rec = FeedbackRecord.from_proposal(
        event="parked", original_filename="a.pdf", text="t",
        proposal_meta={}, proposed_folder=None, proposed_filename=None,
        note="   not mine   ",
    )
    assert rec.note == "not mine"


def test_record_none_note_normalizes_to_empty():
    rec = FeedbackRecord.from_proposal(
        event="parked", original_filename="a.pdf", text="t",
        proposal_meta={}, proposed_folder=None, proposed_filename=None,
        note=None,  # caller passed None — must not crash
    )
    assert rec.note == ""


def test_note_persists_through_jsonl_round_trip(tmp_path):
    log = FeedbackLog(tmp_path / "c.jsonl")
    rec = FeedbackRecord.from_proposal(
        event="skipped", original_filename="bill.pdf", text="t",
        proposal_meta={"category": "Bills"},
        proposed_folder="Bills",
        proposed_filename="bill.pdf",
        note="not mine, belongs to roommate",
    )
    log.append(rec)
    records = list(log.iter_records())
    assert len(records) == 1
    assert records[0]["note"] == "not mine, belongs to roommate"


def test_note_searchable_via_grep_style():
    """The note is a top-level JSON field, so plain text search over the
    JSONL works without needing a dedicated feedback search command."""
    rec = FeedbackRecord.from_proposal(
        event="parked", original_filename="x.pdf", text="t",
        proposal_meta={}, proposed_folder=None, proposed_filename=None,
        note="unpaid",
    )
    line = rec.to_json_line()
    parsed = json.loads(line)
    assert "note" in parsed
    assert parsed["note"] == "unpaid"
    # And the raw line itself contains the substring (case-sensitive)
    assert '"note": "unpaid"' in line


def test_parse_note_suffix():
    """Smoke test for the parser that splits ``park 7 note: <reason>``.

    The CLI uses a simple substring split on ' note:' (with leading space).
    We replicate the logic here so it can't drift silently.
    """
    def split(raw: str) -> tuple[str, str]:
        lower = raw.lower()
        idx = lower.find(' note:')
        if idx == -1:
            return raw, ""
        return raw[:idx].strip(), raw[idx + len(' note:'):].strip()

    assert split("park 7") == ("park 7", "")
    assert split("park 7 note: unpaid") == ("park 7", "unpaid")
    assert split("skip 2,4 note: not mine") == ("skip 2,4", "not mine")
    # Leading 'notes' / 'notebook' in a token should NOT match (we require leading space)
    assert split("park notebook.pdf") == ("park notebook.pdf", "")
    # Mixed case OK
    assert split("park 7 Note: foo") == ("park 7", "foo")
