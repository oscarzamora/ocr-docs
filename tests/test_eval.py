"""Tests for the eval harness (Step 6)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter

from ocr_router.eval import (
    EvalReport,
    EvalRunner,
    EvalSample,
    FileResult,
    sample_files,
)
from ocr_router.eval.runner import CategoryStats
from ocr_router.feedback.bootstrap import infer_label_from_path
from ocr_router.llm import ClassificationResult
from ocr_router.llm.schema import ClassifierCallInfo
from ocr_router.router import DocumentRouter


def _make_blank_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = PdfWriter()
    pdf.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        pdf.write(f)
    return path


# ---------------------------------------------------------------------------
# sample_files
# ---------------------------------------------------------------------------

def test_sample_files_walks_root_and_assigns_labels(temp_dir):
    _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "a.pdf")
    _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "b.pdf")
    _make_blank_pdf(temp_dir / "Paystubs" / "2026" / "c.pdf")
    _make_blank_pdf(temp_dir / "__downloads__" / "ignored.pdf")  # excluded

    samples = sample_files(temp_dir, n=10)
    names = {s.path.name for s in samples}
    assert names == {"a.pdf", "b.pdf", "c.pdf"}
    assert "ignored.pdf" not in names
    bills = next(s for s in samples if s.path.name == "a.pdf")
    assert bills.label.category == "Bills"
    assert bills.label.issuer == "FPL"


def test_sample_files_only_filter(temp_dir):
    _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "a.pdf")
    _make_blank_pdf(temp_dir / "Paystubs" / "2026" / "b.pdf")
    samples = sample_files(temp_dir, n=10, only_categories=["Bills"])
    assert {s.path.name for s in samples} == {"a.pdf"}


def test_sample_files_exclude_filter(temp_dir):
    _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "a.pdf")
    _make_blank_pdf(temp_dir / "Paystubs" / "2026" / "b.pdf")
    samples = sample_files(temp_dir, n=10, excluded_categories=["Paystubs"])
    assert {s.path.name for s in samples} == {"a.pdf"}


def test_sample_files_is_deterministic(temp_dir):
    for i in range(20):
        _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / f"{i:02d}.pdf")
    s1 = [s.path.name for s in sample_files(temp_dir, n=5, seed=7)]
    s2 = [s.path.name for s in sample_files(temp_dir, n=5, seed=7)]
    assert s1 == s2


def test_sample_files_stratifies_across_categories(temp_dir):
    for i in range(8):
        _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / f"b{i}.pdf")
    for i in range(8):
        _make_blank_pdf(temp_dir / "Paystubs" / "2026" / f"p{i}.pdf")

    samples = sample_files(temp_dir, n=8, seed=1)
    cats = [s.label.category for s in samples]
    # Each category had equal weight, so both should appear
    assert "Bills" in cats
    assert "Paystubs" in cats


# ---------------------------------------------------------------------------
# EvalRunner
# ---------------------------------------------------------------------------

class _StubRouter:
    """Behaves like DocumentRouter.classify_document for the eval runner."""
    def __init__(self, verdict_by_filename: dict[str, str]):
        self.verdicts = verdict_by_filename
        self.calls: list[str] = []

    def classify_document(self, text: str) -> str:
        # Use the embedded marker to fake-match
        for fname, cat in self.verdicts.items():
            if fname in text:
                return cat
        return "Uncategorized"


class _StubLLM:
    """LLMClassifier shim with controllable per-file responses."""
    enabled = True

    def __init__(self, verdicts: dict[str, tuple[str, str | None, float]]):
        # filename -> (category, issuer, confidence)
        self.verdicts = verdicts

        class _B:
            label = "stub-llm"
        self.backend = _B()

        class _C:
            confidence_threshold = 0.6
        self.llm_cfg = _C()

    def classify(self, *, text, filename, extra_categories=None):
        if filename not in self.verdicts:
            return None, ClassifierCallInfo(backend="stub-llm", duration_ms=1)
        cat, iss, conf = self.verdicts[filename]
        return (
            ClassificationResult(category=cat, issuer=iss, confidence=conf),
            ClassifierCallInfo(backend="stub-llm", duration_ms=5, fewshot_count=2),
        )


def _patch_extract_text(monkeypatch, text_by_filename: dict[str, str]):
    """Make PdfTextExtractor.extract_text_with_confidence deterministic in tests."""
    from ocr_router.extractor import PdfTextExtractor

    def fake(p):
        text = text_by_filename.get(p.name, "")
        conf = 1.0 if text else 0.0
        return text, conf

    monkeypatch.setattr(
        PdfTextExtractor, "extract_text_with_confidence", staticmethod(fake)
    )


def test_eval_runner_keyword_only(temp_dir, monkeypatch):
    a = _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "a.pdf")
    b = _make_blank_pdf(temp_dir / "Paystubs" / "2026" / "b.pdf")
    samples = [
        EvalSample(path=a, label=infer_label_from_path(a, temp_dir)),
        EvalSample(path=b, label=infer_label_from_path(b, temp_dir)),
    ]
    _patch_extract_text(monkeypatch, {"a.pdf": "marker_a", "b.pdf": "marker_b"})
    router = _StubRouter({"marker_a": "Bills", "marker_b": "Paystubs"})

    runner = EvalRunner(
        config={}, router=router, ocr_engine=None, llm_classifier=None,
        skip_ocr=True,
    )
    report = runner.evaluate(samples)

    assert report.n_evaluated == 2
    assert report.keyword_correct == 2
    assert report.hybrid_correct == 2
    assert report.llm_attempted == 0
    assert report.by_category["Bills"].keyword_correct == 1
    assert report.by_category["Paystubs"].keyword_correct == 1


def test_eval_runner_skips_files_without_text(temp_dir, monkeypatch):
    a = _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "a.pdf")
    b = _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "blank.pdf")
    samples = [
        EvalSample(path=a, label=infer_label_from_path(a, temp_dir)),
        EvalSample(path=b, label=infer_label_from_path(b, temp_dir)),
    ]
    _patch_extract_text(monkeypatch, {"a.pdf": "marker_a"})  # blank.pdf returns ""
    router = _StubRouter({"marker_a": "Bills"})

    runner = EvalRunner(
        config={}, router=router, ocr_engine=None, llm_classifier=None,
        skip_ocr=True,
    )
    report = runner.evaluate(samples)

    assert report.n_evaluated == 1
    assert report.n_skipped == 1
    assert report.skipped_reasons.get("no_text") == 1


def test_eval_runner_llm_helps_when_keyword_wrong(temp_dir, monkeypatch):
    a = _make_blank_pdf(temp_dir / "Credit Card Statements" / "AMEX" / "2026" / "a.pdf")
    samples = [EvalSample(path=a, label=infer_label_from_path(a, temp_dir))]
    _patch_extract_text(monkeypatch, {"a.pdf": "amex statement balance"})

    # Keyword router whiffs; LLM nails it
    router = _StubRouter({})  # always returns "Uncategorized"
    llm = _StubLLM({"a.pdf": ("Credit Card Statements", "AMEX", 0.95)})

    runner = EvalRunner(
        config={}, router=router, ocr_engine=None, llm_classifier=llm,
        skip_ocr=True, confidence_threshold=0.6,
    )
    report = runner.evaluate(samples)

    assert report.keyword_correct == 0
    assert report.llm_correct == 1
    assert report.hybrid_correct == 1
    assert len(report.llm_helped) == 1
    assert len(report.llm_hurt) == 0


def test_eval_runner_llm_hurts_when_keyword_right(temp_dir, monkeypatch):
    a = _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "a.pdf")
    samples = [EvalSample(path=a, label=infer_label_from_path(a, temp_dir))]
    _patch_extract_text(monkeypatch, {"a.pdf": "marker_a"})

    router = _StubRouter({"marker_a": "Bills"})         # correct
    llm = _StubLLM({"a.pdf": ("Insurance", None, 0.9)}) # wrong but confident

    runner = EvalRunner(
        config={}, router=router, ocr_engine=None, llm_classifier=llm,
        skip_ocr=True, confidence_threshold=0.6,
    )
    report = runner.evaluate(samples)

    assert report.keyword_correct == 1
    assert report.llm_correct == 0
    # Hybrid takes LLM at conf >= threshold and disagreement, so hybrid is wrong
    assert report.hybrid_correct == 0
    assert len(report.llm_hurt) == 1
    assert report.llm_hurt[0].keyword_category == "Bills"
    assert report.llm_hurt[0].llm_category == "Insurance"


def test_eval_runner_audit_log_is_written(temp_dir, monkeypatch):
    a = _make_blank_pdf(temp_dir / "Bills" / "FPL" / "2026" / "a.pdf")
    samples = [EvalSample(path=a, label=infer_label_from_path(a, temp_dir))]
    _patch_extract_text(monkeypatch, {"a.pdf": "marker_a"})
    router = _StubRouter({"marker_a": "Bills"})

    audit = temp_dir / "audit" / "eval.jsonl"
    runner = EvalRunner(
        config={}, router=router, ocr_engine=None, llm_classifier=None,
        skip_ocr=True, audit_log_path=audit,
    )
    runner.evaluate(samples)

    assert audit.exists()
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    import json
    rec = json.loads(lines[0])
    assert rec["truth_category"] == "Bills"
    assert rec["keyword_correct"] is True
