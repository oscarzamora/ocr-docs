"""Evaluation harness for the OCR Router pipeline (Step 6 of L4 roadmap).

Measures classification accuracy against a folder where the ground-truth
label for each PDF is encoded in its filesystem path (the same convention
the Step 2 tree-bootstrap uses).

Public surface:
- ``EvalSample``: one sampled file + its inferred ground-truth label
- ``FileResult``: per-file comparison of (keyword vs LLM vs ground truth)
- ``EvalReport``: aggregated counts + helpers to format a summary

Nothing here mutates the user's documents. The eval is strictly read-only.
"""

from ocr_router.eval.runner import (
    EvalReport,
    EvalRunner,
    EvalSample,
    FileResult,
    sample_files,
)

__all__ = [
    "EvalReport",
    "EvalRunner",
    "EvalSample",
    "FileResult",
    "sample_files",
]
