"""Tests for the L1 feedback log."""

import json
from pathlib import Path

import pytest

from ocr_router.feedback import FeedbackLog, FeedbackRecord
from ocr_router.feedback.log import SCHEMA_VERSION


def test_record_from_proposal_truncates_text(temp_dir):
    rec = FeedbackRecord.from_proposal(
        event="confirmed",
        original_filename="x.pdf",
        text="x" * 5000,
        proposal_meta={"category": "Bills", "issuer": "FPL"},
        proposed_folder="Bills/FPL/2026",
        proposed_filename="2026.04 - FPL Monthly - $100.00.pdf",
        proposed_confidence=0.9,
        text_excerpt_chars=100,
    )
    assert len(rec.text_excerpt) == 100
    assert rec.schema_version == SCHEMA_VERSION
    assert rec.event == "confirmed"
    assert rec.proposed_category == "Bills"
    assert rec.proposed_issuer == "FPL"
    assert rec.backend == "keyword"


def test_record_serializes_to_valid_json(temp_dir):
    rec = FeedbackRecord.from_proposal(
        event="skipped",
        original_filename="weird.pdf",
        text="some ocr text",
        proposal_meta={"category": "Uncategorized"},
        proposed_folder=None,
        proposed_filename=None,
    )
    line = rec.to_json_line()
    assert line.endswith("\n")
    parsed = json.loads(line)
    assert parsed["event"] == "skipped"
    assert parsed["original_filename"] == "weird.pdf"
    assert parsed["text_excerpt"] == "some ocr text"
    assert parsed["proposed_category"] == "Uncategorized"


def test_append_creates_parent_dir(temp_dir):
    target = temp_dir / "deep" / "nested" / "corrections.jsonl"
    log = FeedbackLog(target)
    rec = FeedbackRecord.from_proposal(
        event="confirmed",
        original_filename="a.pdf",
        text="t",
        proposal_meta={"category": "Bills"},
        proposed_folder="Bills/FPL/2026",
        proposed_filename="x.pdf",
    )
    assert log.append(rec) is True
    assert target.exists()
    assert target.read_text(encoding="utf-8").count("\n") == 1


def test_append_many_writes_all(temp_dir):
    target = temp_dir / "c.jsonl"
    log = FeedbackLog(target)
    records = [
        FeedbackRecord.from_proposal(
            event="confirmed",
            original_filename=f"{i}.pdf",
            text="t",
            proposal_meta={"category": "Bills"},
            proposed_folder="Bills",
            proposed_filename=f"{i}.pdf",
        )
        for i in range(5)
    ]
    assert log.append_many(records) == 5
    assert target.read_text(encoding="utf-8").count("\n") == 5


def test_append_is_append_only(temp_dir):
    target = temp_dir / "c.jsonl"
    log = FeedbackLog(target)
    for i in range(3):
        rec = FeedbackRecord.from_proposal(
            event="confirmed",
            original_filename=f"{i}.pdf",
            text="t",
            proposal_meta={"category": "Bills"},
            proposed_folder=None,
            proposed_filename=None,
        )
        log.append(rec)

    content_before = target.read_text(encoding="utf-8")
    # Appending more must not rewrite earlier lines
    log.append(FeedbackRecord.from_proposal(
        event="skipped",
        original_filename="extra.pdf",
        text="t",
        proposal_meta={},
        proposed_folder=None,
        proposed_filename=None,
    ))
    content_after = target.read_text(encoding="utf-8")
    assert content_after.startswith(content_before)
    assert content_after.count("\n") == 4


def test_iter_records_skips_malformed_lines(temp_dir):
    target = temp_dir / "c.jsonl"
    target.write_text(
        '{"event":"confirmed","ts":"2026-05-26T12:00:00+00:00","original_filename":"a.pdf","text_excerpt":"t"}\n'
        'this is not json\n'
        '{"event":"skipped","ts":"2026-05-26T12:01:00+00:00","original_filename":"b.pdf","text_excerpt":"t"}\n',
        encoding="utf-8",
    )
    log = FeedbackLog(target)
    records = list(log.iter_records())
    assert len(records) == 2
    assert records[0]["event"] == "confirmed"
    assert records[1]["event"] == "skipped"


def test_stats_aggregates_counts(temp_dir):
    target = temp_dir / "c.jsonl"
    log = FeedbackLog(target)
    log.append(FeedbackRecord.from_proposal(
        event="confirmed", original_filename="a.pdf", text="t",
        proposal_meta={"category": "Bills"},
        proposed_folder=None, proposed_filename=None,
    ))
    log.append(FeedbackRecord.from_proposal(
        event="confirmed", original_filename="b.pdf", text="t",
        proposal_meta={"category": "Bills"},
        proposed_folder=None, proposed_filename=None,
    ))
    log.append(FeedbackRecord.from_proposal(
        event="skipped", original_filename="c.pdf", text="t",
        proposal_meta={"category": "Insurance"},
        proposed_folder=None, proposed_filename=None,
    ))

    s = log.stats()
    assert s["total"] == 3
    assert s["by_event"]["confirmed"] == 2
    assert s["by_event"]["skipped"] == 1
    assert s["by_category"]["Bills"] == 2
    assert s["by_category"]["Insurance"] == 1
    assert s["by_backend"]["keyword"] == 3


def test_iter_records_on_missing_file_returns_empty(temp_dir):
    log = FeedbackLog(temp_dir / "does_not_exist.jsonl")
    assert list(log.iter_records()) == []
    assert log.stats()["total"] == 0


def test_record_marks_final_state_for_corrections(temp_dir):
    rec = FeedbackRecord.from_proposal(
        event="corrected",
        original_filename="chase.pdf",
        text="checking account",
        proposal_meta={"category": "Uncategorized", "issuer": None},
        proposed_folder="Uncategorized",
        proposed_filename="chase.pdf",
        final_category="Bank Account & Statements",
        final_issuer="Chase Checking",
        final_folder="Bank Account & Statements/Chase Checking/2026",
        final_filename="2026.04 - Chase Checking Statement.pdf",
    )
    payload = json.loads(rec.to_json_line())
    assert payload["proposed_category"] == "Uncategorized"
    assert payload["final_category"] == "Bank Account & Statements"
    assert payload["final_issuer"] == "Chase Checking"
