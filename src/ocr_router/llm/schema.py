"""Strict schema for LLM classification output.

Using Pydantic so:
- Ollama's ``format='json'`` mode returns a string we can validate immediately
- Bad output (hallucinated category, missing confidence, wrong types) becomes
  an exception we can catch and treat as "LLM unavailable for this doc"
- The wire format is stable for the agent (Step 7 / MCP)
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClassificationResult(BaseModel):
    """One LLM verdict on a single document.

    All confidence values are in [0.0, 1.0]. ``category`` is required;
    everything else is optional because not every doc has a clear issuer
    (think paystubs, tax forms, personal notices).
    """
    model_config = ConfigDict(extra="ignore")

    category: str = Field(..., description="One of the configured categories, exact match.")
    issuer: Optional[str] = Field(None, description="Issuer name, or null if unclear.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str] = Field(
        default_factory=list,
        description="1-3 short bullets explaining the verdict; surfaced to the user in HITL.",
    )

    @field_validator("category", mode="before")
    @classmethod
    def _strip_category(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("issuer", mode="before")
    @classmethod
    def _normalize_issuer(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if s.lower() in ("", "none", "null", "unknown", "n/a", "—", "-"):
                return None
            return s
        return v

    @field_validator("reasons", mode="before")
    @classmethod
    def _coerce_reasons(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            # LLM sometimes returns a single string instead of a list
            return [v.strip()] if v.strip() else []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []


class ClassifierCallInfo(BaseModel):
    """Diagnostics about one classifier call — useful for the Review badge & logs."""
    model_config = ConfigDict(extra="ignore")

    backend: str                       # e.g. "local:llama3.2:3b" | "null"
    duration_ms: int                   # wall-clock time of the model call
    fewshot_count: int = 0             # number of exemplars injected into prompt
    prompt_chars: int = 0              # for cost / context-window visibility
    completion_chars: int = 0
    error: Optional[str] = None        # populated on failure


__all__ = ["ClassificationResult", "ClassifierCallInfo"]
