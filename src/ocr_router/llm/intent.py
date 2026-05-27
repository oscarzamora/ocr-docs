"""Natural-language intent parsing for the interactive confirmation prompt.

Routes a free-form user reply through the local LLM (Ollama, format='json')
and returns a structured ``ConfirmIntent``. Falls back gracefully: when
the LLM is unavailable or returns invalid JSON, the caller drops back to
the deterministic literal parser in ``cli._interactive_confirm``.

Why this exists:
  Once an LLM is already loaded in the pipeline (`--llm`), there is no
  reason to make the user remember a fixed prompt syntax. They can say
  "skip 2 because I haven't paid yet" and the LLM picks the right action
  (park 2, capture the rationale).
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ocr_router.llm.backends import LLMBackend, NullBackend
from ocr_router.llm.schema import ClassifierCallInfo

logger = logging.getLogger(__name__)

INTENT_TIMEOUT_S = 15


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class FileRule(BaseModel):
    """A routing rule the user implied for one file.

    Example: 'skip 2, it's actually FPL not AT&T' produces
    ``FileRule(file_index=2, kind='issuer', value='FPL')``. The caller is
    responsible for applying the rule to routing-config.local.yaml.
    """
    model_config = ConfigDict(extra="ignore")

    file_index: int = Field(..., ge=1)
    kind: str = Field(..., pattern="^(issuer|category)$")
    value: str = Field(..., min_length=1)


class FileNote(BaseModel):
    """A free-form rationale the user gave for one specific file."""
    model_config = ConfigDict(extra="ignore")

    file_index: int = Field(..., ge=1)
    note: str = ""


class ConfirmIntent(BaseModel):
    """Structured intent extracted from one user reply.

    Action semantics:
      - ``move_all``   -> approve every file (Enter equivalent)
      - ``move_some``  -> approve only ``indices``
      - ``skip_some``  -> approve all EXCEPT ``indices`` (do not park them)
      - ``park_some``  -> park ``indices``, approve everything else
      - ``quit``       -> abort, no moves
    """
    model_config = ConfigDict(extra="ignore")

    action: str = Field(..., pattern="^(move_all|move_some|skip_some|park_some|quit)$")
    indices: list[int] = Field(default_factory=list)
    note: str = ""
    file_notes: list[FileNote] = Field(default_factory=list)
    rules: list[FileRule] = Field(default_factory=list)

    @field_validator("indices", mode="before")
    @classmethod
    def _coerce_indices(cls, v):
        if v is None:
            return []
        if isinstance(v, int):
            return [v]
        out: list[int] = []
        for x in v:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out

    @field_validator("note", mode="before")
    @classmethod
    def _coerce_note(cls, v):
        if v is None:
            return ""
        return str(v).strip()

    def note_for(self, index: int) -> str:
        """Return the most-specific note for ``index`` (per-file > batch)."""
        for fn in self.file_notes:
            if fn.file_index == index and fn.note:
                return fn.note
        return self.note

    def validate_against(self, valid_indices: set[int]) -> "ConfirmIntent":
        """Drop indices that are out of range. Returns self (chainable).

        We never silently invent files. Out-of-range entries are dropped
        and the caller can decide whether to ask for confirmation.
        """
        self.indices = [i for i in self.indices if i in valid_indices]
        self.file_notes = [fn for fn in self.file_notes if fn.file_index in valid_indices]
        self.rules = [r for r in self.rules if r.file_index in valid_indices]
        return self

    def human_summary(self, n_files: int) -> str:
        """One-line plain-English recap used in the optional re-confirm step."""
        verb = {
            "move_all":  f"move ALL {n_files} files",
            "move_some": f"move only #{','.join(map(str, sorted(self.indices)))}",
            "skip_some": f"skip #{','.join(map(str, sorted(self.indices)))} (move the rest)",
            "park_some": f"park #{','.join(map(str, sorted(self.indices)))} (keep in place)",
            "quit":      "abort without moving anything",
        }.get(self.action, self.action)
        suffix = f' — "{self.note}"' if self.note else ""
        if self.rules:
            rules = ", ".join(f"#{r.file_index} {r.kind}={r.value!r}" for r in self.rules)
            suffix += f"  [rules: {rules}]"
        return verb + suffix


# ---------------------------------------------------------------------------
# Prompt + parser
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You translate a user's free-form reply to a document-routing review prompt
into a strict JSON object. The user was shown a numbered list of N files
and asked which ones to MOVE to organized folders, SKIP (leave in input),
or PARK (keep in place permanently). They may also imply a routing rule
("this is from X, not Y").

Return ONLY a JSON object with this exact shape:

{
  "action": "move_all" | "move_some" | "skip_some" | "park_some" | "quit",
  "indices": [int, ...],          // file numbers the action applies to
  "note": "<verbatim string>",    // batch-level rationale
  "file_notes": [                 // per-file rationale (overrides batch)
    {"file_index": int, "note": "<verbatim string>"}
  ],
  "rules": [                      // config rules implied by the user
    {"file_index": int, "kind": "issuer" | "category", "value": "<string>"}
  ]
}

Action selection rules:
  - "do them all" / "go" / "yes" / "proceed" / blank      -> move_all
  - explicit file numbers ("1, 3 and 5")                  -> move_some
  - "skip N" / "not N" / "ignore N"                       -> skip_some
  - "park N" / "keep N here" / "hold N" / "leave N" /
    "N is unpaid, do not move yet" / "N is a draft"       -> park_some
  - "cancel" / "quit" / "stop" / "abort" / "nevermind"    -> quit

Always copy the user's reason VERBATIM into ``note`` (batch) or
``file_notes`` (per-file). Never paraphrase. If no reason given, "".

Capture implied config edits in ``rules``:
  - "the issuer is X" / "this is from X, not Y" -> kind="issuer", value="X"
  - "this is actually a Bill, not a Receipt"    -> kind="category", value="Bills"

Hard constraints:
  - All index values MUST be integers in 1..N. Never invent files.
  - When the user says "park because unpaid" or similar without a number,
    set action="quit" and put a clarifying request in note. Do not guess.
  - No markdown, no prose outside the JSON object.
"""


def _user_prompt(raw: str, n: int, file_summaries: Optional[list[str]] = None) -> str:
    parts = [f"There are N = {n} files (numbered 1..{n})."]
    if file_summaries:
        parts.append("Files for context (do not re-classify):")
        for i, summary in enumerate(file_summaries, 1):
            parts.append(f"  {i}. {summary[:120]}")
    parts += ["", "User reply to interpret:", f'"""{raw}"""', "", "Return the JSON now."]
    return "\n".join(parts)


def parse_intent(
    raw: str,
    *,
    n_files: int,
    backend: LLMBackend,
    file_summaries: Optional[list[str]] = None,
    timeout_s: int = INTENT_TIMEOUT_S,
) -> tuple[Optional[ConfirmIntent], ClassifierCallInfo]:
    """Parse a natural-language reply into a structured ``ConfirmIntent``.

    Returns ``(intent, info)``. ``intent`` is ``None`` when:
      - the backend is a NullBackend (LLM disabled)
      - the LLM call failed (timeout, connection)
      - the LLM returned invalid JSON
      - the response failed Pydantic validation
      - no valid indices remain after range-checking against ``n_files``
    The caller should fall back to the literal parser in those cases.
    """
    if isinstance(backend, NullBackend) or n_files <= 0:
        return None, ClassifierCallInfo(
            backend=getattr(backend, "label", "null"),
            duration_ms=0,
            error="intent parse skipped (no LLM)",
        )

    # backends with chat_json (OllamaBackend) give us a raw dict
    chat_json = getattr(backend, "chat_json", None)
    if chat_json is None:
        return None, ClassifierCallInfo(
            backend=getattr(backend, "label", "unknown"),
            duration_ms=0,
            error="backend does not support chat_json",
        )

    system = _SYSTEM_PROMPT
    user = _user_prompt(raw, n_files, file_summaries)

    data, info = chat_json(system=system, user=user, timeout_s=timeout_s)
    if data is None:
        return None, info

    try:
        intent = ConfirmIntent(**data)
    except ValidationError as exc:
        logger.info("Intent parse validation failed: %s\nRaw: %s", exc, data)
        return None, ClassifierCallInfo(
            backend=info.backend, duration_ms=info.duration_ms,
            prompt_chars=info.prompt_chars, completion_chars=info.completion_chars,
            error=f"intent validation: {exc.errors()[0]['msg']}",
        )

    valid = set(range(1, n_files + 1))
    intent.validate_against(valid)

    # If the action implies indices but all were out of range, treat as failure
    if intent.action in ("move_some", "skip_some", "park_some") and not intent.indices:
        return None, ClassifierCallInfo(
            backend=info.backend, duration_ms=info.duration_ms,
            prompt_chars=info.prompt_chars, completion_chars=info.completion_chars,
            error=f"no valid indices after range-check (got {data.get('indices')!r})",
        )

    return intent, info


__all__ = [
    "INTENT_TIMEOUT_S",
    "ConfirmIntent",
    "FileNote",
    "FileRule",
    "parse_intent",
]
