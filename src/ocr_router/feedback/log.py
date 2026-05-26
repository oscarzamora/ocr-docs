"""Append-only JSONL feedback log.

Captures every user decision in the interactive Confirm phase:
- ``confirmed``  : user accepted the proposal as-is
- ``corrected``  : user changed category / issuer / destination / name
- ``skipped``    : user removed the file from the move set
- ``rule_added`` : user added a config rule via the "issuer=" / "category=" prompts

The log is the **substrate** for L2 (auto-tune YAML), L3 (vector store) and
L4 (few-shot prompts) — without it none of the learning layers have data.

Design constraints:
- Append-only: never rewrite earlier entries.
- Failures never raise into the main pipeline (best-effort).
- File path is configurable; defaults to ``<output_dir>/_feedback/corrections.jsonl``.
- Records are flat JSON, one per line, UTF-8, newline-terminated. Easy to grep.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Bump when the on-disk schema changes in a way that breaks readers.
SCHEMA_VERSION = 1

# How many characters of OCR text to persist per record.
# Long enough to be useful as few-shot context, short enough that 10k records fit in memory.
DEFAULT_TEXT_EXCERPT_CHARS = 2000


@dataclass
class FeedbackRecord:
    """One observation captured from the interactive Confirm phase."""

    # Required ----------------------------------------------------------------
    event: str                       # confirmed | corrected | skipped | rule_added
    ts: str                          # ISO-8601 UTC timestamp
    original_filename: str           # name as it appeared in the input folder
    text_excerpt: str                # first N chars of OCR'd text (privacy-trimmed)

    # Proposal snapshot -------------------------------------------------------
    proposed_category: Optional[str] = None
    proposed_issuer: Optional[str] = None
    proposed_folder: Optional[str] = None      # path relative to output_dir
    proposed_filename: Optional[str] = None
    proposed_confidence: Optional[float] = None

    # Final (post-correction) snapshot — only populated for `corrected` events.
    final_category: Optional[str] = None
    final_issuer: Optional[str] = None
    final_folder: Optional[str] = None
    final_filename: Optional[str] = None

    # Diagnostics -------------------------------------------------------------
    backend: str = "keyword"         # keyword | llm:local | llm:cloud:<provider>
    schema_version: int = SCHEMA_VERSION
    extra: dict = field(default_factory=dict)

    def to_json_line(self) -> str:
        """Serialize as a single JSON line with trailing newline."""
        return json.dumps(asdict(self), ensure_ascii=False) + "\n"

    @classmethod
    def from_proposal(
        cls,
        *,
        event: str,
        original_filename: str,
        text: str,
        proposal_meta: dict,
        proposed_folder: Optional[str],
        proposed_filename: Optional[str],
        proposed_confidence: Optional[float] = None,
        final_category: Optional[str] = None,
        final_issuer: Optional[str] = None,
        final_folder: Optional[str] = None,
        final_filename: Optional[str] = None,
        backend: str = "keyword",
        extra: Optional[dict] = None,
        text_excerpt_chars: int = DEFAULT_TEXT_EXCERPT_CHARS,
    ) -> "FeedbackRecord":
        """Build a record from the CLI's Proposal dataclass shape.

        ``proposal_meta`` is the ``Proposal.metadata`` dict.
        """
        return cls(
            event=event,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            original_filename=original_filename,
            text_excerpt=(text or "")[:text_excerpt_chars],
            proposed_category=proposal_meta.get("category"),
            proposed_issuer=proposal_meta.get("issuer"),
            proposed_folder=proposed_folder,
            proposed_filename=proposed_filename,
            proposed_confidence=proposed_confidence,
            final_category=final_category,
            final_issuer=final_issuer,
            final_folder=final_folder,
            final_filename=final_filename,
            backend=backend,
            extra=extra or {},
        )


class FeedbackLog:
    """Append-only JSONL writer.

    Usage::

        log = FeedbackLog(output_dir / "_feedback" / "corrections.jsonl")
        log.append(FeedbackRecord(...))
        for rec in log.iter_records():
            ...
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append(self, record: FeedbackRecord) -> bool:
        """Append a record. Never raises — returns False on failure.

        Failures are logged at WARNING; the caller (CLI) should continue
        the move regardless.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(record.to_json_line())
            return True
        except Exception as exc:                              # pragma: no cover
            logger.warning("FeedbackLog.append failed: %s", exc)
            return False

    def append_many(self, records: Iterable[FeedbackRecord]) -> int:
        """Append a batch. Returns the count successfully written."""
        ok = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                for rec in records:
                    f.write(rec.to_json_line())
                    ok += 1
        except Exception as exc:                              # pragma: no cover
            logger.warning("FeedbackLog.append_many failed after %d records: %s", ok, exc)
        return ok

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def iter_records(self) -> Iterable[dict]:
        """Yield records as plain dicts. Skips malformed lines."""
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed feedback line: %s", exc)

    def stats(self) -> dict[str, Any]:
        """Aggregate counts for `ocr-router feedback stats`."""
        by_event: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_backend: dict[str, int] = {}
        total = 0
        for rec in self.iter_records():
            total += 1
            ev = rec.get("event", "unknown")
            by_event[ev] = by_event.get(ev, 0) + 1
            cat = rec.get("final_category") or rec.get("proposed_category") or "Unknown"
            by_category[cat] = by_category.get(cat, 0) + 1
            be = rec.get("backend", "unknown")
            by_backend[be] = by_backend.get(be, 0) + 1
        return {
            "total": total,
            "path": str(self.path),
            "by_event": by_event,
            "by_category": by_category,
            "by_backend": by_backend,
        }


__all__ = ["FeedbackLog", "FeedbackRecord", "SCHEMA_VERSION", "DEFAULT_TEXT_EXCERPT_CHARS"]
