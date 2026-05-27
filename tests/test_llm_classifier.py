"""Tests for the L4 LLM classifier (Step 4).

Real Ollama is not exercised here — backends are mocked. The 'classify_real_*'
test is opt-in via OLLAMA_TEST=1 and skipped by default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ocr_router.feedback import (
    EmbeddingStore, FeedbackLog, FeedbackRecord, index_log_into_store,
)
from ocr_router.llm import (
    ClassificationResult, LLMClassifier, NullBackend, build_classification_prompt,
)
from ocr_router.llm.backends import BackendInfo, LLMBackend, OllamaBackend
from ocr_router.llm.classifier import LLMConfig
from ocr_router.llm.schema import ClassifierCallInfo


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_classification_result_normalizes_blank_issuer():
    r = ClassificationResult(category="Bills", issuer="  Unknown ", confidence=0.9)
    assert r.issuer is None      # 'Unknown' is normalized to None
    r2 = ClassificationResult(category="Bills", issuer="", confidence=0.5)
    assert r2.issuer is None
    r3 = ClassificationResult(category="Bills", issuer="FPL", confidence=0.8)
    assert r3.issuer == "FPL"


def test_classification_result_coerces_single_reason_string():
    r = ClassificationResult(category="Bills", confidence=0.5, reasons="just one")
    assert r.reasons == ["just one"]


def test_classification_result_validates_confidence_range():
    with pytest.raises(Exception):
        ClassificationResult(category="Bills", confidence=1.5)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def test_prompt_includes_all_categories():
    system, user = build_classification_prompt(
        text="bla", filename="x.pdf",
        categories=["Bills", "Insurance", "Tax Returns"],
    )
    assert "- Bills" in system
    assert "- Insurance" in system
    assert "- Tax Returns" in system
    assert "JSON" in system


def test_prompt_includes_filename_and_text():
    system, user = build_classification_prompt(
        text="bank statement balance $1234", filename="weird name.pdf",
        categories=["Bank Account & Statements"],
    )
    assert "weird name.pdf" in user
    assert "bank statement balance $1234" in user


def test_prompt_trims_long_text():
    long_text = "x" * 10000
    system, user = build_classification_prompt(
        text=long_text, filename="big.pdf",
        categories=["Bills"],
        first_chars=500, last_chars=200,
    )
    # Marker present and total user text far shorter than input
    assert "chars omitted" in user
    assert len(user) < 2000


def test_prompt_includes_known_issuers_hint():
    system, user = build_classification_prompt(
        text="x", filename="x.pdf",
        categories=["Bills"], known_issuers=["FPL", "AT&T", "Pure Water"],
    )
    assert "Known canonical issuer names" in user
    assert "FPL" in user
    assert "AT&T" in user


def test_prompt_includes_fewshot_neighbors():
    from ocr_router.feedback.store import Neighbor
    neighbors = [
        Neighbor(score=0.95, category="Bills", issuer="FPL", folder="Bills/FPL/2026",
                 final_filename="2026.04 - FPL.pdf", original_filename="raw1.pdf",
                 text_excerpt="electric bill amount due", ts="t1"),
        Neighbor(score=0.92, category="Bills", issuer="AT&T", folder="Bills/AT&T/2026",
                 final_filename="2026.04 - ATT.pdf", original_filename="raw2.pdf",
                 text_excerpt="internet service bill", ts="t2"),
    ]
    system, user = build_classification_prompt(
        text="some doc", filename="x.pdf",
        categories=["Bills"], neighbors=neighbors,
    )
    assert "Past confirmed decisions" in user
    assert "FPL" in user
    assert "AT&T" in user
    assert "electric bill amount due" in user


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def test_null_backend_returns_none():
    b = NullBackend("test")
    res, info = b.classify(system="s", user="u")
    assert res is None
    assert info.backend == "null"
    assert info.error == "test"
    info2 = b.info()
    assert info2.available is False


class _FakeBackend(LLMBackend):
    """Returns a fixed JSON response (or simulates an error)."""

    label = "fake"

    def __init__(self, payload=None, raise_exc=None):
        self.payload = payload
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str]] = []

    def classify(self, *, system, user, timeout_s=30):
        self.calls.append((system, user))
        if self.raise_exc:
            return None, ClassifierCallInfo(
                backend=self.label, duration_ms=1, error=str(self.raise_exc),
            )
        try:
            from pydantic import ValidationError
            r = ClassificationResult(**(self.payload or {}))
            return r, ClassifierCallInfo(backend=self.label, duration_ms=1)
        except Exception as exc:
            return None, ClassifierCallInfo(
                backend=self.label, duration_ms=1, error=str(exc),
            )

    def info(self) -> BackendInfo:
        return BackendInfo(label=self.label, available=True, model="fake")


# ---------------------------------------------------------------------------
# LLMClassifier orchestration
# ---------------------------------------------------------------------------

CONFIG_SAMPLE = {
    "categories": {
        "Bills": ["amount due"],
        "Bank Account & Statements": ["checking"],
        "Credit Card Statements": ["statement balance"],
    },
    "known_issuers": {"fpl": "FPL", "chase": "Chase"},
    "llm": {"enabled": True, "fewshot_k": 3},
}


def test_classifier_disabled_uses_null_backend():
    c = LLMClassifier(backend=NullBackend("disabled"), config=CONFIG_SAMPLE)
    assert c.enabled is False
    res, info = c.classify(text="anything", filename="x.pdf")
    assert res is None


def test_classifier_routes_simple_call_through_backend():
    backend = _FakeBackend(payload={
        "category": "Bills", "issuer": "FPL", "confidence": 0.92,
        "reasons": ["mentions amount due and service address"],
    })
    c = LLMClassifier(backend=backend, config=CONFIG_SAMPLE)
    res, info = c.classify(text="amount due $100 fpl", filename="x.pdf")
    assert res is not None
    assert res.category == "Bills"
    assert res.issuer == "FPL"
    assert res.confidence == 0.92
    assert info.backend == "fake"
    # Few-shot count is 0 because no store was provided
    assert info.fewshot_count == 0
    # Prompt mentioned all 3 categories
    assert "Bills" in backend.calls[0][0]
    assert "Bank Account & Statements" in backend.calls[0][0]


def test_classifier_rejects_invalid_category():
    backend = _FakeBackend(payload={
        "category": "Hallucinated Category", "issuer": "X", "confidence": 0.9,
    })
    c = LLMClassifier(backend=backend, config=CONFIG_SAMPLE)
    res, info = c.classify(text="x", filename="x.pdf")
    assert res is None
    assert "invalid category" in (info.error or "")


def test_classifier_fewshot_count_reflects_neighbors(temp_dir):
    # Build a tiny store with one neighbor so fetch_neighbors returns it.
    import numpy as np
    from ocr_router.feedback import OllamaEmbedder

    class FakeEmbedder:
        model = "fake-embed"

        def embed(self, text: str):
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            v = rng.standard_normal(8).astype("float32")
            return v / (np.linalg.norm(v) + 1e-12)

    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder()
    store.upsert(
        original_filename="seed.pdf", ts="t",
        category="Bills", issuer="FPL", folder="Bills/FPL/2026",
        final_filename="seed.pdf", backend="bootstrap-tree",
        text_excerpt="electric bill amount due",
        embedding=embedder.embed("electric bill amount due"),
        embed_model="fake",
    )

    backend = _FakeBackend(payload={
        "category": "Bills", "issuer": "FPL", "confidence": 0.9, "reasons": []
    })
    c = LLMClassifier(
        backend=backend, embedder=embedder, store=store, config=CONFIG_SAMPLE,
    )
    res, info = c.classify(text="electric bill amount due", filename="x.pdf")
    assert res is not None
    assert info.fewshot_count == 1
    # The user prompt the backend received should contain the neighbor text
    assert "electric bill amount due" in backend.calls[0][1]
    store.close()


def test_llm_config_from_dict_uses_defaults():
    cfg = LLMConfig.from_dict({})
    assert cfg.enabled is False
    assert cfg.local_model == "llama3.2:3b"
    assert cfg.embed_model == "nomic-embed-text"


def test_llm_config_from_dict_reads_block():
    cfg = LLMConfig.from_dict({
        "llm": {
            "enabled": True,
            "fewshot_k": 7,
            "confidence_threshold": 0.8,
            "local": {"model": "qwen2.5:3b", "timeout_s": 60},
            "embedder": {"model": "nomic-embed-text"},
        }
    })
    assert cfg.enabled is True
    assert cfg.fewshot_k == 7
    assert cfg.confidence_threshold == 0.8
    assert cfg.local_model == "qwen2.5:3b"
    assert cfg.timeout_s == 60


# ---------------------------------------------------------------------------
# Optional smoke test against a real Ollama daemon. Skipped unless OLLAMA_TEST=1
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("OLLAMA_TEST") != "1",
    reason="Set OLLAMA_TEST=1 to run real Ollama integration tests",
)
def test_ollama_backend_real():
    b = OllamaBackend(model="llama3.2:3b")
    info = b.info()
    assert info.available, info.note
