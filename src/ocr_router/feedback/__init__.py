"""Feedback / learning subsystem.

Captures user decisions during the interactive Confirm phase so that future
runs can learn from them. This module is intentionally additive: failures
never propagate to the main pipeline.

Scopes (filled in incrementally):
- log.py  : append-only JSONL of confirmed / corrected / skipped proposals (L1)
- store.py: SQLite-backed embedding store for similarity search             (L3, future)
- bootstrap.py: import past decisions from PROCESSED_PDFS.md / Documents     (L2, future)
"""

from ocr_router.feedback.log import FeedbackLog, FeedbackRecord

__all__ = ["FeedbackLog", "FeedbackRecord"]
