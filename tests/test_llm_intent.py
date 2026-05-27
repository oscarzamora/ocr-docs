"""Tests for the natural-language intent parser (llm/intent.py).

Uses a fake backend that returns canned JSON dicts — no real Ollama needed.
"""

from __future__ import annotations

import pytest

from ocr_router.llm import ConfirmIntent, NullBackend, parse_intent
from ocr_router.llm.backends import LLMBackend, BackendInfo
from ocr_router.llm.intent import FileNote, FileRule
from ocr_router.llm.schema import ClassifierCallInfo


class _FakeBackend(LLMBackend):
    """Backend that returns a canned dict from chat_json."""

    label = "fake"

    def __init__(self, payload: dict | None = None, raise_exc=None):
        self.payload = payload
        self.raise_exc = raise_exc

    def classify(self, *, system, user, timeout_s=30):
        return None, ClassifierCallInfo(backend=self.label, duration_ms=1)

    def info(self) -> BackendInfo:
        return BackendInfo(label=self.label, available=True, model="fake")

    def chat_json(self, *, system, user, timeout_s=15):
        if self.raise_exc:
            return None, ClassifierCallInfo(
                backend=self.label, duration_ms=1, error=str(self.raise_exc),
            )
        return self.payload, ClassifierCallInfo(
            backend=self.label, duration_ms=1, completion_chars=50,
        )


# ---------------------------------------------------------------------------
# ConfirmIntent validation
# ---------------------------------------------------------------------------

def test_intent_rejects_unknown_action():
    with pytest.raises(Exception):
        ConfirmIntent(action="frobulate")


def test_intent_coerces_int_indices():
    i = ConfirmIntent(action="move_some", indices=["1", "2", "3"])
    assert i.indices == [1, 2, 3]


def test_intent_drops_invalid_indices():
    i = ConfirmIntent(action="park_some", indices=[1, 5, 10])
    i.validate_against({1, 2, 3})
    assert i.indices == [1]


def test_intent_note_for_returns_per_file_when_present():
    i = ConfirmIntent(
        action="park_some",
        indices=[1, 2],
        note="batch reason",
        file_notes=[FileNote(file_index=2, note="specific to 2")],
    )
    assert i.note_for(1) == "batch reason"
    assert i.note_for(2) == "specific to 2"


def test_intent_note_for_falls_back_to_batch():
    i = ConfirmIntent(
        action="park_some",
        indices=[1],
        note="batch",
        file_notes=[FileNote(file_index=1, note="")],  # empty -> use batch
    )
    assert i.note_for(1) == "batch"


def test_intent_human_summary_for_park():
    i = ConfirmIntent(action="park_some", indices=[3, 1], note="unpaid")
    s = i.human_summary(5)
    assert "park" in s
    assert "#1,3" in s
    assert "unpaid" in s


def test_intent_human_summary_for_quit():
    i = ConfirmIntent(action="quit", note="nevermind")
    assert "abort" in i.human_summary(5)


# ---------------------------------------------------------------------------
# parse_intent
# ---------------------------------------------------------------------------

def test_parse_intent_returns_none_for_null_backend():
    intent, info = parse_intent("park 7", n_files=10, backend=NullBackend("off"))
    assert intent is None
    assert "skipped" in (info.error or "")


def test_parse_intent_returns_none_when_n_files_zero():
    intent, info = parse_intent("anything", n_files=0, backend=_FakeBackend({"action": "move_all"}))
    assert intent is None


def test_parse_intent_returns_none_for_backend_without_chat_json():
    class NoJson(LLMBackend):
        label = "nojson"
        def classify(self, *, system, user, timeout_s=30):
            return None, ClassifierCallInfo(backend=self.label, duration_ms=0)
        def info(self):
            return BackendInfo(label=self.label, available=True, model="x")
    intent, info = parse_intent("go", n_files=3, backend=NoJson())
    assert intent is None
    assert "chat_json" in (info.error or "")


def test_parse_intent_happy_path_park():
    payload = {
        "action": "park_some",
        "indices": [2],
        "note": "I haven't paid yet",
        "file_notes": [],
        "rules": [],
    }
    intent, info = parse_intent(
        "skip 2 because I haven't paid yet",
        n_files=5, backend=_FakeBackend(payload),
    )
    assert intent is not None
    assert intent.action == "park_some"
    assert intent.indices == [2]
    assert intent.note == "I haven't paid yet"


def test_parse_intent_happy_path_move_all():
    payload = {"action": "move_all", "indices": [], "note": ""}
    intent, _ = parse_intent("do them all", n_files=3, backend=_FakeBackend(payload))
    assert intent.action == "move_all"


def test_parse_intent_drops_out_of_range_indices():
    """If the LLM returns indices outside 1..N and that leaves no valid
    indices for an action that needs them, we report failure (None)."""
    payload = {"action": "park_some", "indices": [99]}
    intent, info = parse_intent("park whatever", n_files=5, backend=_FakeBackend(payload))
    assert intent is None
    assert "no valid indices" in (info.error or "")


def test_parse_intent_keeps_some_when_partial_range_overlap():
    payload = {"action": "skip_some", "indices": [1, 99, 3]}
    intent, _ = parse_intent("skip 1 and 3", n_files=5, backend=_FakeBackend(payload))
    assert intent is not None
    assert intent.indices == [1, 3]


def test_parse_intent_validation_failure_returns_none():
    """Invalid Pydantic shape -> None (caller falls back)."""
    payload = {"action": "GIBBERISH", "indices": "not a list"}
    intent, info = parse_intent("park 2", n_files=5, backend=_FakeBackend(payload))
    assert intent is None
    assert "validation" in (info.error or "").lower() or "intent" in (info.error or "").lower()


def test_parse_intent_backend_error_returns_none():
    intent, info = parse_intent(
        "go", n_files=3, backend=_FakeBackend(raise_exc="timeout"),
    )
    assert intent is None


def test_parse_intent_extracts_rules():
    payload = {
        "action": "skip_some",
        "indices": [2],
        "note": "wrong issuer",
        "rules": [{"file_index": 2, "kind": "issuer", "value": "FPL"}],
    }
    intent, _ = parse_intent(
        "skip 2, it's FPL not AT&T", n_files=5, backend=_FakeBackend(payload),
    )
    assert intent is not None
    assert len(intent.rules) == 1
    assert intent.rules[0].kind == "issuer"
    assert intent.rules[0].value == "FPL"


def test_parse_intent_drops_rules_with_invalid_file_index():
    payload = {
        "action": "skip_some",
        "indices": [2],
        "rules": [
            {"file_index": 2, "kind": "issuer", "value": "FPL"},
            {"file_index": 99, "kind": "category", "value": "Bills"},
        ],
    }
    intent, _ = parse_intent("skip 2", n_files=5, backend=_FakeBackend(payload))
    assert intent is not None
    assert len(intent.rules) == 1
    assert intent.rules[0].value == "FPL"
