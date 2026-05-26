"""Tests for the L3 embedding store (SQLite-backed)."""

from pathlib import Path

import numpy as np
import pytest

from ocr_router.feedback import (
    EmbeddingStore,
    FeedbackLog,
    FeedbackRecord,
    Neighbor,
    index_log_into_store,
)


# A trivial embedder that produces deterministic vectors based on the input
# text. Real Ollama calls are out of scope for unit tests.
class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, dim: int = 16):
        self.dim = dim
        self.calls = 0

    def embed(self, text: str) -> np.ndarray:
        self.calls += 1
        rng = np.random.default_rng(seed=abs(hash(text)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-12)


# ---------------------------------------------------------------------------
# EmbeddingStore basics
# ---------------------------------------------------------------------------

def test_store_starts_empty(temp_dir):
    store = EmbeddingStore(temp_dir / "ex.sqlite")
    assert store.count() == 0
    assert store.stats().total == 0
    store.close()


def test_upsert_inserts_then_replaces(temp_dir):
    store = EmbeddingStore(temp_dir / "ex.sqlite")
    v1 = np.ones(8, dtype=np.float32)
    v2 = np.full(8, 0.5, dtype=np.float32)
    common = dict(
        original_filename="a.pdf", ts="2026-05-26T12:00:00+00:00",
        category="Bills", issuer="FPL", folder="Bills/FPL/2026",
        final_filename="a.pdf", backend="bootstrap-tree",
        text_excerpt="amount due 100",
        embed_model="fake",
    )
    assert store.upsert(embedding=v1, **common) is True
    assert store.count() == 1
    # Same (original_filename, ts) → upsert replaces, count stays 1
    store.upsert(embedding=v2, **common)
    assert store.count() == 1
    store.close()


def test_existing_keys_round_trips(temp_dir):
    store = EmbeddingStore(temp_dir / "ex.sqlite")
    store.upsert(
        original_filename="a.pdf", ts="t1",
        category="Bills", issuer="FPL", folder=None, final_filename="a.pdf",
        backend="x", text_excerpt="t", embedding=np.ones(4, dtype=np.float32),
        embed_model="m",
    )
    assert store.existing_keys() == {("a.pdf", "t1")}
    store.close()


def test_search_returns_self_first(temp_dir):
    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder(dim=8)
    texts = ["chase bank statement", "fpl electric bill", "amex credit card"]
    for i, txt in enumerate(texts):
        store.upsert(
            original_filename=f"{i}.pdf", ts=f"t{i}",
            category="Bills" if i == 1 else "Bank Account & Statements" if i == 0 else "Credit Card Statements",
            issuer=["Chase", "FPL", "AMEX"][i],
            folder=None, final_filename=f"{i}.pdf",
            backend="bootstrap-tree", text_excerpt=txt,
            embedding=embedder.embed(txt),
            embed_model="fake",
        )

    # Querying the exact same string returns that record with score ~1.0
    q = embedder.embed("fpl electric bill")
    hits = store.search(q, k=3)
    assert len(hits) == 3
    assert hits[0].issuer == "FPL"
    assert hits[0].score > 0.99
    store.close()


def test_search_category_filter(temp_dir):
    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder(dim=8)
    rows = [
        ("Bills", "FPL", "fpl bill"),
        ("Bills", "AT&T", "att bill"),
        ("Credit Card Statements", "AMEX", "amex card"),
    ]
    for i, (cat, iss, txt) in enumerate(rows):
        store.upsert(
            original_filename=f"{i}.pdf", ts=f"t{i}",
            category=cat, issuer=iss, folder=None, final_filename=f"{i}.pdf",
            backend="x", text_excerpt=txt,
            embedding=embedder.embed(txt), embed_model="f",
        )

    q = embedder.embed("amex card")
    hits = store.search(q, k=5, category="Bills")
    # Only Bills records should appear, AMEX must be filtered out
    assert all(h.category == "Bills" for h in hits)
    assert all(h.issuer != "AMEX" for h in hits)
    store.close()


def test_search_empty_store_returns_empty(temp_dir):
    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder(dim=8)
    assert store.search(embedder.embed("anything"), k=5) == []
    store.close()


def test_stats_by_category(temp_dir):
    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder(dim=4)
    for i in range(3):
        store.upsert(
            original_filename=f"b{i}.pdf", ts=f"tb{i}",
            category="Bills", issuer="FPL", folder=None, final_filename=f"b{i}.pdf",
            backend="x", text_excerpt=f"bill {i}",
            embedding=embedder.embed(f"bill {i}"), embed_model="f",
        )
    for i in range(2):
        store.upsert(
            original_filename=f"c{i}.pdf", ts=f"tc{i}",
            category="Credit Card Statements", issuer="AMEX",
            folder=None, final_filename=f"c{i}.pdf",
            backend="x", text_excerpt=f"card {i}",
            embedding=embedder.embed(f"card {i}"), embed_model="f",
        )
    s = store.stats()
    assert s.total == 5
    assert s.by_category["Bills"] == 3
    assert s.by_category["Credit Card Statements"] == 2
    assert s.dim == 4
    store.close()


# ---------------------------------------------------------------------------
# index_log_into_store
# ---------------------------------------------------------------------------

def _make_record(name: str, text: str, category: str, issuer: str) -> FeedbackRecord:
    return FeedbackRecord.from_proposal(
        event="confirmed",
        original_filename=name,
        text=text,
        proposal_meta={"category": category, "issuer": issuer},
        proposed_folder=f"{category}/{issuer}/2026",
        proposed_filename=name,
        proposed_confidence=1.0,
        final_category=category,
        final_issuer=issuer,
        final_folder=f"{category}/{issuer}/2026",
        final_filename=name,
        backend="bootstrap-tree",
    )


def test_index_log_skips_non_confirmed_and_empty_text(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    log.append(_make_record("a.pdf", "real text here", "Bills", "FPL"))
    log.append(_make_record("b.pdf", "", "Bills", "FPL"))                # empty
    skipped = FeedbackRecord.from_proposal(
        event="skipped", original_filename="c.pdf", text="some text",
        proposal_meta={}, proposed_folder=None, proposed_filename=None,
    )
    log.append(skipped)

    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder(dim=8)
    stats = index_log_into_store(list(log.iter_records()), store, embedder)

    assert stats.seen == 3
    assert stats.embedded == 1
    assert stats.skipped_empty == 1
    assert stats.skipped_event == 1
    assert store.count() == 1
    store.close()


def test_index_log_is_idempotent(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    log.append(_make_record("a.pdf", "x", "Bills", "FPL"))
    log.append(_make_record("b.pdf", "y", "Bills", "AT&T"))

    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder(dim=8)

    s1 = index_log_into_store(list(log.iter_records()), store, embedder)
    assert s1.embedded == 2

    s2 = index_log_into_store(list(log.iter_records()), store, embedder)
    assert s2.embedded == 0
    assert s2.skipped_existing == 2
    assert store.count() == 2
    store.close()


def test_search_after_indexing(temp_dir):
    log = FeedbackLog(temp_dir / "c.jsonl")
    log.append(_make_record("chase.pdf", "chase checking account statement", "Bank Account & Statements", "Chase"))
    log.append(_make_record("fpl.pdf",   "fpl electric bill service address", "Bills", "FPL"))
    log.append(_make_record("amex.pdf",  "american express credit card", "Credit Card Statements", "AMEX"))

    store = EmbeddingStore(temp_dir / "ex.sqlite")
    embedder = FakeEmbedder(dim=16)
    index_log_into_store(list(log.iter_records()), store, embedder)

    # Querying for a text identical to one of the stored excerpts: that
    # record should rank first.
    hits = store.search(embedder.embed("fpl electric bill service address"), k=3)
    assert hits[0].issuer == "FPL"
    assert hits[0].category == "Bills"
    store.close()
