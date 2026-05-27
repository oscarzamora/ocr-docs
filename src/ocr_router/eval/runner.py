"""Evaluation runner: compare keyword vs LLM vs ground truth across sampled PDFs.

Design notes:
- *Reuses* DocumentRouter for keyword classification and LLMClassifier for
  the LLM second opinion - the eval is testing the same code paths used
  by ``process``, not a parallel implementation.
- Ground truth is inferred from the path layout via Step 2's
  ``infer_label_from_path``. A file at ``root/Bills/FPL/2026/x.pdf`` has
  ground truth ``category=Bills, issuer=FPL, year=2026``.
- Each per-file result is also written to a JSONL audit log so the user
  can ``grep`` individual misses.
- Sampling is deterministic given ``seed`` so re-running the eval on the
  same corpus reproduces results.
"""

from __future__ import annotations

import json
import logging
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from ocr_router.extractor import PdfTextExtractor
from ocr_router.feedback.bootstrap import (
    DEFAULT_TREE_EXCLUDES,
    InferredLabel,
    infer_label_from_path,
)
from ocr_router.llm import LLMClassifier
from ocr_router.ocr_engine import OcrEngine
from ocr_router.router import DocumentRouter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

@dataclass
class EvalSample:
    """One PDF picked for evaluation, paired with its inferred ground truth."""
    path: Path
    label: InferredLabel


def sample_files(
    root: Path,
    *,
    n: Optional[int] = 200,
    only_categories: Optional[Iterable[str]] = None,
    excluded_categories: Optional[Iterable[str]] = None,
    excluded_dirs: Optional[Iterable[str]] = None,
    seed: int = 42,
) -> list[EvalSample]:
    """Pick PDFs to evaluate; deterministic for a given seed.

    Sampling is stratified by category so we don't end up with 90% Bills
    just because that's the biggest directory.
    """
    excludes = set(excluded_dirs) if excluded_dirs is not None else set(DEFAULT_TREE_EXCLUDES)
    only = set(only_categories) if only_categories else None
    excl = set(excluded_categories or ())

    by_cat: dict[str, list[EvalSample]] = {}
    for top in sorted(p for p in root.iterdir() if p.is_dir()):
        if top.name in excludes or top.name in excl:
            continue
        if only is not None and top.name not in only:
            continue
        for pdf in top.rglob("*.pdf"):
            if any(part in excludes for part in pdf.parts):
                continue
            label = infer_label_from_path(pdf, root)
            if not label.category:
                continue
            by_cat.setdefault(label.category, []).append(EvalSample(path=pdf, label=label))

    rng = random.Random(seed)
    for samples in by_cat.values():
        rng.shuffle(samples)

    total_available = sum(len(s) for s in by_cat.values())
    if n is None or n >= total_available:
        out: list[EvalSample] = []
        for samples in by_cat.values():
            out.extend(samples)
        rng.shuffle(out)
        return out

    # Stratified pick: target per-category proportional to its size
    selected: list[EvalSample] = []
    for _cat, samples in by_cat.items():
        take = max(1, round(n * len(samples) / total_available))
        selected.extend(samples[:take])
    rng.shuffle(selected)
    return selected[:n]


# ---------------------------------------------------------------------------
# Per-file result
# ---------------------------------------------------------------------------

@dataclass
class FileResult:
    """Result of running both classifiers against one ground-truth sample."""
    path: Path
    truth_category: str
    truth_issuer: Optional[str]

    keyword_category: Optional[str] = None
    keyword_correct: bool = False

    llm_category: Optional[str] = None
    llm_issuer: Optional[str] = None
    llm_confidence: Optional[float] = None
    llm_correct: Optional[bool] = None       # None when LLM disabled
    llm_duration_ms: Optional[int] = None
    llm_fewshot_count: Optional[int] = None
    llm_error: Optional[str] = None

    # Hybrid (Step 5 decision rule): which would have won
    hybrid_category: Optional[str] = None
    hybrid_correct: Optional[bool] = None
    hybrid_backend: Optional[str] = None

    text_chars: int = 0
    text_confidence: float = 0.0
    skipped: Optional[str] = None            # set when we couldn't evaluate

    def to_json(self) -> dict:
        return {
            "path": str(self.path),
            "truth_category": self.truth_category,
            "truth_issuer": self.truth_issuer,
            "keyword_category": self.keyword_category,
            "keyword_correct": self.keyword_correct,
            "llm_category": self.llm_category,
            "llm_issuer": self.llm_issuer,
            "llm_confidence": self.llm_confidence,
            "llm_correct": self.llm_correct,
            "llm_duration_ms": self.llm_duration_ms,
            "llm_fewshot_count": self.llm_fewshot_count,
            "llm_error": self.llm_error,
            "hybrid_category": self.hybrid_category,
            "hybrid_correct": self.hybrid_correct,
            "hybrid_backend": self.hybrid_backend,
            "text_chars": self.text_chars,
            "text_confidence": self.text_confidence,
            "skipped": self.skipped,
        }


# ---------------------------------------------------------------------------
# Aggregated report
# ---------------------------------------------------------------------------

@dataclass
class CategoryStats:
    truth_count: int = 0
    keyword_correct: int = 0
    llm_correct: int = 0
    hybrid_correct: int = 0


@dataclass
class EvalReport:
    """Aggregated counts and human-readable summary helpers."""
    n_total: int = 0
    n_evaluated: int = 0
    n_skipped: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    keyword_correct: int = 0
    llm_correct: int = 0
    llm_attempted: int = 0
    hybrid_correct: int = 0

    by_category: dict[str, CategoryStats] = field(default_factory=dict)
    # Confusion matrix: {truth_cat: {predicted_cat: count}} for the hybrid path
    confusion_hybrid: dict[str, dict[str, int]] = field(default_factory=dict)

    # Where LLM helped (keyword wrong, llm right) and hurt (kw right, llm wrong)
    llm_helped: list[FileResult] = field(default_factory=list)
    llm_hurt: list[FileResult] = field(default_factory=list)

    total_llm_ms: int = 0

    @property
    def keyword_accuracy(self) -> float:
        return self.keyword_correct / self.n_evaluated if self.n_evaluated else 0.0

    @property
    def llm_accuracy(self) -> float:
        # LLM accuracy is over files where we actually asked the LLM
        return self.llm_correct / self.llm_attempted if self.llm_attempted else 0.0

    @property
    def hybrid_accuracy(self) -> float:
        return self.hybrid_correct / self.n_evaluated if self.n_evaluated else 0.0

    @property
    def avg_llm_ms(self) -> float:
        return self.total_llm_ms / self.llm_attempted if self.llm_attempted else 0.0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# We import this locally so eval.runner doesn't pull cli.py at module import time
def _apply_decision_lazy(*args, **kwargs):
    from ocr_router.cli import _apply_llm_decision
    return _apply_llm_decision(*args, **kwargs)


class EvalRunner:
    """Run keyword + LLM classification on each sample, compare to truth."""

    def __init__(
        self,
        *,
        config: dict,
        router: DocumentRouter,
        ocr_engine: Optional[OcrEngine] = None,
        llm_classifier: Optional[LLMClassifier] = None,
        skip_ocr: bool = False,
        confidence_threshold: float = 0.6,
        audit_log_path: Optional[Path] = None,
    ):
        self.config = config
        self.router = router
        self.ocr_engine = ocr_engine
        self.llm = llm_classifier
        self.skip_ocr = skip_ocr or (ocr_engine is None) or not (
            ocr_engine and ocr_engine.is_available()
        )
        self.confidence_threshold = confidence_threshold
        self.audit_log_path = audit_log_path
        if audit_log_path:
            audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            # Truncate previous run's audit log so this run is self-contained
            audit_log_path.write_text("", encoding="utf-8")

    # ------------------------------------------------------------------
    def _extract_text(self, pdf: Path, tmp_dir: Path) -> tuple[str, float]:
        """Try pypdf first; OCR only when there's no text layer and OCR is enabled."""
        text, conf = PdfTextExtractor.extract_text_with_confidence(pdf)
        if conf > 0.0 or self.skip_ocr or self.ocr_engine is None:
            return text, conf
        out = tmp_dir / f"{pdf.stem}_ocr.pdf"
        if self.ocr_engine.ocr_pdf(pdf, out):
            try:
                text, conf = PdfTextExtractor.extract_text_with_confidence(out)
            finally:
                try:
                    out.unlink(missing_ok=True)
                except Exception:                                    # pragma: no cover
                    pass
        return text, conf

    # ------------------------------------------------------------------
    def evaluate(
        self,
        samples: list[EvalSample],
        *,
        progress_cb: Optional[Callable[[int, int, Path], None]] = None,
    ) -> EvalReport:
        """Run the eval and return the aggregated report."""
        report = EvalReport(n_total=len(samples))

        with tempfile.TemporaryDirectory(prefix="ocr_eval_") as td:
            tmp_dir = Path(td)

            for i, sample in enumerate(samples, 1):
                if progress_cb:
                    try:
                        progress_cb(i, len(samples), sample.path)
                    except Exception:                                # pragma: no cover
                        pass

                truth_cat = sample.label.category or ""
                truth_iss = sample.label.issuer

                result = FileResult(
                    path=sample.path,
                    truth_category=truth_cat,
                    truth_issuer=truth_iss,
                )

                try:
                    text, conf = self._extract_text(sample.path, tmp_dir)
                    result.text_chars = len(text or "")
                    result.text_confidence = conf
                except Exception as exc:
                    result.skipped = f"text extraction failed: {exc}"
                    self._record_skipped(report, result, "extract_error")
                    self._audit(result)
                    continue

                if not text or conf == 0.0:
                    result.skipped = "no text"
                    self._record_skipped(report, result, "no_text")
                    self._audit(result)
                    continue

                # --- Keyword
                kw_cat = self.router.classify_document(text)
                result.keyword_category = kw_cat
                result.keyword_correct = (kw_cat == truth_cat)

                # --- LLM (optional)
                llm_cat: Optional[str] = None
                llm_correct: Optional[bool] = None
                llm_result_obj = None
                if self.llm is not None and self.llm.enabled:
                    llm_result_obj, llm_info = self.llm.classify(
                        text=text, filename=sample.path.name,
                        extra_categories=[kw_cat] if kw_cat else None,
                    )
                    if llm_info:
                        result.llm_duration_ms = llm_info.duration_ms
                        result.llm_fewshot_count = llm_info.fewshot_count
                        result.llm_error = llm_info.error
                        if llm_info.duration_ms:
                            report.total_llm_ms += llm_info.duration_ms

                    if llm_result_obj is not None:
                        llm_cat = llm_result_obj.category
                        result.llm_category = llm_cat
                        result.llm_issuer = llm_result_obj.issuer
                        result.llm_confidence = float(llm_result_obj.confidence)
                        llm_correct = (llm_cat == truth_cat)
                        result.llm_correct = llm_correct
                        report.llm_attempted += 1

                # --- Hybrid (Step 5 decision rule)
                if llm_result_obj is not None and self.llm is not None:
                    hybrid_label, hybrid_cat, _iss = _apply_decision_lazy(
                        keyword_category=kw_cat,
                        llm_result=llm_result_obj,
                        threshold=self.confidence_threshold,
                        issues=[],
                    )
                    result.hybrid_category = hybrid_cat
                    result.hybrid_backend = hybrid_label
                    result.hybrid_correct = (hybrid_cat == truth_cat)
                else:
                    # No LLM verdict -> hybrid == keyword
                    result.hybrid_category = kw_cat
                    result.hybrid_backend = "keyword"
                    result.hybrid_correct = result.keyword_correct

                # --- Aggregate
                report.n_evaluated += 1
                if result.keyword_correct:
                    report.keyword_correct += 1
                if llm_correct is True:
                    report.llm_correct += 1
                if result.hybrid_correct:
                    report.hybrid_correct += 1

                stats = report.by_category.setdefault(truth_cat, CategoryStats())
                stats.truth_count += 1
                if result.keyword_correct:
                    stats.keyword_correct += 1
                if llm_correct is True:
                    stats.llm_correct += 1
                if result.hybrid_correct:
                    stats.hybrid_correct += 1

                # Confusion (hybrid path)
                pred = result.hybrid_category or "(none)"
                report.confusion_hybrid.setdefault(truth_cat, {})
                report.confusion_hybrid[truth_cat][pred] = (
                    report.confusion_hybrid[truth_cat].get(pred, 0) + 1
                )

                # Helped / hurt
                if llm_correct is True and not result.keyword_correct:
                    report.llm_helped.append(result)
                elif llm_correct is False and result.keyword_correct:
                    report.llm_hurt.append(result)

                self._audit(result)

        return report

    # ------------------------------------------------------------------
    def _record_skipped(
        self, report: EvalReport, result: FileResult, reason: str,
    ) -> None:
        report.n_skipped += 1
        report.skipped_reasons[reason] = report.skipped_reasons.get(reason, 0) + 1

    def _audit(self, result: FileResult) -> None:
        if not self.audit_log_path:
            return
        try:
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
        except Exception as exc:                                     # pragma: no cover
            logger.warning("Failed to write audit row: %s", exc)


__all__ = [
    "EvalSample", "FileResult", "CategoryStats", "EvalReport",
    "EvalRunner", "sample_files",
]