"""Pluggable LLM backends for classification.

This file defines the *transport* layer (how we talk to the model).
The *what to ask* lives in ``prompts.py``; the *orchestration* in
``classifier.py``.

Why a base class + Null backend rather than ``Optional[Backend]`` everywhere:
the rest of the pipeline can call ``backend.classify(...)`` unconditionally
and get a clean structured ``ClassificationResult | None`` back, instead of
sprinkling ``if backend is not None`` everywhere.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from ocr_router.llm.schema import ClassificationResult, ClassifierCallInfo

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MODEL = "llama3.2:3b"
DEFAULT_TIMEOUT_S = 30
DEFAULT_NUM_CTX = 4096       # plenty for excerpt + few-shot, fits comfortably on 8GB+ GPUs


class LLMUnavailable(RuntimeError):
    """Raised when a backend can't be reached (daemon down, model missing, timeout)."""


@dataclass
class BackendInfo:
    """Static info about a configured backend (used by `llm doctor`)."""
    label: str                    # human-friendly: "local:llama3.2:3b"
    available: bool
    model: Optional[str]
    note: Optional[str] = None    # populated with the reason when unavailable


class LLMBackend(ABC):
    """Abstract base. Implementations must return strict JSON-validated results."""

    label: str = "backend"

    @abstractmethod
    def classify(
        self,
        *,
        system: str,
        user: str,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> tuple[Optional[ClassificationResult], ClassifierCallInfo]:
        """Return (result, info). result is None when classification fails."""

    @abstractmethod
    def info(self) -> BackendInfo:
        """Probe the backend and return a status snapshot."""


# ---------------------------------------------------------------------------
# Null backend — used when LLM is disabled or unavailable. Always returns None.
# ---------------------------------------------------------------------------

class NullBackend(LLMBackend):
    """No-op backend. Lets callers always invoke ``classify`` safely."""

    label = "null"

    def __init__(self, reason: str = "LLM disabled"):
        self.reason = reason

    def classify(self, *, system: str, user: str, timeout_s: int = DEFAULT_TIMEOUT_S):
        return None, ClassifierCallInfo(
            backend=self.label, duration_ms=0, error=self.reason,
        )

    def info(self) -> BackendInfo:
        return BackendInfo(label=self.label, available=False, model=None, note=self.reason)


# ---------------------------------------------------------------------------
# Ollama backend — local model via the official `ollama` Python client
# ---------------------------------------------------------------------------

class OllamaBackend(LLMBackend):
    """Talks to a local (or remote) Ollama server.

    Uses Ollama's ``format='json'`` mode for strict JSON output. The model
    is invoked once per call (no streaming) so we get a single response
    string that we validate with Pydantic.
    """

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_MODEL,
        host: Optional[str] = None,
        num_ctx: int = DEFAULT_NUM_CTX,
        temperature: float = 0.0,
    ):
        self.model = model
        self.host = host
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.label = f"local:{model}"
        self._client = None

    # ------------------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            try:
                import ollama
            except ImportError as exc:                                # pragma: no cover
                raise LLMUnavailable("ollama package not installed") from exc
            self._client = ollama.Client(host=self.host) if self.host else ollama.Client()
        return self._client

    # ------------------------------------------------------------------
    def info(self) -> BackendInfo:
        try:
            client = self._get_client()
            tags = client.list()
            # ``tags`` is e.g. {"models":[{"model":"llama3.2:3b","size":...}, ...]}
            models = []
            if isinstance(tags, dict):
                models = [m.get("model") or m.get("name") for m in tags.get("models", [])]
            else:
                models = [getattr(m, "model", None) or getattr(m, "name", None)
                          for m in getattr(tags, "models", [])]
            if self.model not in models:
                return BackendInfo(
                    label=self.label, available=False, model=self.model,
                    note=f"model not pulled (try: ollama pull {self.model})",
                )
            return BackendInfo(label=self.label, available=True, model=self.model)
        except LLMUnavailable as exc:
            return BackendInfo(label=self.label, available=False, model=self.model, note=str(exc))
        except Exception as exc:
            return BackendInfo(
                label=self.label, available=False, model=self.model,
                note=f"daemon unreachable: {exc}",
            )

    # ------------------------------------------------------------------
    def classify(
        self,
        *,
        system: str,
        user: str,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> tuple[Optional[ClassificationResult], ClassifierCallInfo]:
        client = self._get_client()
        start = time.time()
        prompt_chars = len(system) + len(user)
        completion = ""
        try:
            # Ollama's chat() with format='json' guarantees a single JSON object.
            resp = client.chat(
                model=self.model,
                format="json",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                options={
                    "temperature": self.temperature,
                    "num_ctx": self.num_ctx,
                },
                keep_alive="10m",
            )
            completion = (
                resp.get("message", {}).get("content", "")
                if isinstance(resp, dict)
                else getattr(getattr(resp, "message", None), "content", "")
            )
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("OllamaBackend.classify failed: %s", exc)
            return None, ClassifierCallInfo(
                backend=self.label, duration_ms=duration_ms,
                prompt_chars=prompt_chars, error=str(exc),
            )

        duration_ms = int((time.time() - start) * 1000)

        try:
            data = json.loads(completion) if completion else {}
            result = ClassificationResult(**data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Invalid JSON from %s: %s\nRaw: %s", self.label, exc, completion[:300])
            return None, ClassifierCallInfo(
                backend=self.label, duration_ms=duration_ms,
                prompt_chars=prompt_chars, completion_chars=len(completion),
                error=f"invalid JSON: {exc}",
            )

        return result, ClassifierCallInfo(
            backend=self.label, duration_ms=duration_ms,
            prompt_chars=prompt_chars, completion_chars=len(completion),
        )

    # ------------------------------------------------------------------
    def chat_json(
        self,
        *,
        system: str,
        user: str,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> tuple[Optional[dict], ClassifierCallInfo]:
        """Generic JSON chat: returns a raw dict (not a ClassificationResult).

        Used by callers that need a different JSON schema than the classifier
        (e.g. the intent parser for the interactive confirm prompt).
        Returns ``(parsed_dict, info)``; ``parsed_dict`` is None on
        transport / JSON-parse failure.
        """
        client = self._get_client()
        start = time.time()
        prompt_chars = len(system) + len(user)
        completion = ""
        try:
            resp = client.chat(
                model=self.model,
                format="json",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                options={
                    "temperature": self.temperature,
                    "num_ctx": self.num_ctx,
                },
                keep_alive="10m",
            )
            completion = (
                resp.get("message", {}).get("content", "")
                if isinstance(resp, dict)
                else getattr(getattr(resp, "message", None), "content", "")
            )
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("OllamaBackend.chat_json failed: %s", exc)
            return None, ClassifierCallInfo(
                backend=self.label, duration_ms=duration_ms,
                prompt_chars=prompt_chars, error=str(exc),
            )

        duration_ms = int((time.time() - start) * 1000)
        try:
            data = json.loads(completion) if completion else {}
        except json.JSONDecodeError as exc:
            logger.info("OllamaBackend.chat_json: invalid JSON: %s\nRaw: %s", exc, completion[:300])
            return None, ClassifierCallInfo(
                backend=self.label, duration_ms=duration_ms,
                prompt_chars=prompt_chars, completion_chars=len(completion),
                error=f"invalid JSON: {exc}",
            )

        return data, ClassifierCallInfo(
            backend=self.label, duration_ms=duration_ms,
            prompt_chars=prompt_chars, completion_chars=len(completion),
        )


# Backwards-compat alias
LocalBackend = OllamaBackend


__all__ = [
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_TIMEOUT_S",
    "BackendInfo",
    "LLMBackend",
    "LLMUnavailable",
    "LocalBackend",
    "NullBackend",
    "OllamaBackend",
]
