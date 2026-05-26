"""LLM-assisted classification (Step 4 of the L4 roadmap).

Local-first by design: when Ollama is reachable, the classifier asks
``llama3.2:3b`` (or any other tagged model) for a JSON verdict, optionally
seeding the prompt with the *k* most-similar past decisions from the
:class:`ocr_router.feedback.store.EmbeddingStore`.

When Ollama is unavailable, every call returns ``None`` so callers can
gracefully fall back to keyword scoring + HITL.

This module is intentionally additive: nothing here modifies the existing
keyword pipeline. The wiring into :class:`DocumentRouter` happens in Step 5.
"""

from ocr_router.llm.schema import ClassificationResult, ClassifierCallInfo
from ocr_router.llm.backends import (
    LocalBackend,
    LLMBackend,
    LLMUnavailable,
    NullBackend,
    OllamaBackend,
)
from ocr_router.llm.classifier import LLMClassifier
from ocr_router.llm.prompts import build_classification_prompt

__all__ = [
    "ClassificationResult",
    "ClassifierCallInfo",
    "LLMBackend",
    "LLMUnavailable",
    "LocalBackend",
    "NullBackend",
    "OllamaBackend",
    "LLMClassifier",
    "build_classification_prompt",
]
