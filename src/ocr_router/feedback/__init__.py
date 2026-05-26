"""Feedback / learning subsystem.

Captures user decisions during the interactive Confirm phase so that future
runs can learn from them. This module is intentionally additive: failures
never propagate to the main pipeline.

Scopes (filled in incrementally):
- log.py       : append-only JSONL of confirmed / corrected / skipped proposals (L1)
- bootstrap.py : import past decisions from PROCESSED_PDFS.md + __downloads__   (L2)
- store.py     : SQLite-backed embedding store for similarity search             (L3, future)
"""

from ocr_router.feedback.log import FeedbackLog, FeedbackRecord
from ocr_router.feedback.bootstrap import (
    BootstrapStats,
    HistoryEntry,
    InferredLabel,
    bootstrap_from_downloads,
    bootstrap_from_tree,
    index_history,
    infer_label_from_path,
    parse_processed_history,
    parse_processed_history_paths,
)

__all__ = [
    "FeedbackLog",
    "FeedbackRecord",
    "BootstrapStats",
    "HistoryEntry",
    "InferredLabel",
    "bootstrap_from_downloads",
    "bootstrap_from_tree",
    "index_history",
    "infer_label_from_path",
    "parse_processed_history",
    "parse_processed_history_paths",
]
