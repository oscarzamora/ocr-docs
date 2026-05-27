"""Orchestrator that ties backends + prompts + embedding store together.

Typical lifecycle (Step 5 will wire this into the CLI):

    classifier = LLMClassifier(
        backend=OllamaBackend("llama3.2:3b"),
        embedder=OllamaEmbedder("nomic-embed-text"),
        store=EmbeddingStore("…/examples.sqlite"),
        config=cfg.model_dump(),
    )
    result, info = classifier.classify(text=ocr_text, filename="x.pdf")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from ocr_router.feedback.store import EmbeddingStore, Neighbor, OllamaEmbedder, OllamaUnavailable
from ocr_router.llm.backends import (
    DEFAULT_TIMEOUT_S,
    LLMBackend,
    NullBackend,
)
from ocr_router.llm.prompts import (
    DEFAULT_FEWSHOT_EXCERPT_CHARS,
    DEFAULT_TEXT_FIRST_CHARS,
    DEFAULT_TEXT_LAST_CHARS,
    build_classification_prompt,
)
from ocr_router.llm.schema import ClassificationResult, ClassifierCallInfo

logger = logging.getLogger(__name__)

DEFAULT_FEWSHOT_K = 5


@dataclass
class LLMConfig:
    """Subset of routing config we care about. Built via ``LLMConfig.from_dict``."""
    enabled: bool = False
    local_model: str = "llama3.2:3b"
    embed_model: str = "nomic-embed-text"
    host: Optional[str] = None
    timeout_s: int = DEFAULT_TIMEOUT_S
    fewshot_k: int = DEFAULT_FEWSHOT_K
    confidence_threshold: float = 0.6
    text_first_chars: int = DEFAULT_TEXT_FIRST_CHARS
    text_last_chars: int = DEFAULT_TEXT_LAST_CHARS
    fewshot_excerpt_chars: int = DEFAULT_FEWSHOT_EXCERPT_CHARS

    @classmethod
    def from_dict(cls, config: dict) -> "LLMConfig":
        block = (config or {}).get("llm", {}) or {}
        local = block.get("local", {}) or {}
        return cls(
            enabled=bool(block.get("enabled", False)),
            local_model=local.get("model") or "llama3.2:3b",
            embed_model=(block.get("embedder", {}) or {}).get("model") or "nomic-embed-text",
            host=local.get("host"),
            timeout_s=int(local.get("timeout_s", DEFAULT_TIMEOUT_S)),
            fewshot_k=int(block.get("fewshot_k", DEFAULT_FEWSHOT_K)),
            confidence_threshold=float(block.get("confidence_threshold", 0.6)),
            text_first_chars=int(block.get("text_first_chars", DEFAULT_TEXT_FIRST_CHARS)),
            text_last_chars=int(block.get("text_last_chars", DEFAULT_TEXT_LAST_CHARS)),
            fewshot_excerpt_chars=int(block.get("fewshot_excerpt_chars", DEFAULT_FEWSHOT_EXCERPT_CHARS)),
        )


class LLMClassifier:
    """Compose backend + few-shot retrieval + prompt assembly.

    Designed to be **safe to instantiate even when LLM is disabled** — the
    backend will be a :class:`NullBackend` in that case, and ``classify()``
    cleanly returns ``None`` for every call.
    """

    def __init__(
        self,
        *,
        backend: LLMBackend,
        embedder: Optional[OllamaEmbedder] = None,
        store: Optional[EmbeddingStore] = None,
        config: Optional[dict] = None,
        categories: Optional[Iterable[str]] = None,
        known_issuers: Optional[Iterable[str]] = None,
    ):
        self.backend = backend
        self.embedder = embedder
        self.store = store
        self.llm_cfg = LLMConfig.from_dict(config or {})
        self._config = config or {}
        # Cache of categories / issuers from the routing config
        if categories is None:
            categories = list((config or {}).get("categories", {}).keys())
        if known_issuers is None:
            known_issuers = list((config or {}).get("known_issuers", {}).values())
        self.categories = list(categories)
        self.known_issuers = list(known_issuers)

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return not isinstance(self.backend, NullBackend)

    # ------------------------------------------------------------------
    def fetch_neighbors(self, text: str) -> list[Neighbor]:
        """Embed ``text`` and pull k nearest past confirmed decisions.

        Returns [] silently when the store is empty or embeddings are unavailable.
        The query text is truncated to the embedder's context window to avoid
        the (very common) ``input length exceeds the context length`` error
        from Ollama when feeding it full multi-page statements.
        """
        if self.embedder is None or self.store is None or self.llm_cfg.fewshot_k <= 0:
            return []
        if self.store.count() == 0:
            return []
        # nomic-embed-text has a 2048-token context (~8000 chars). Be conservative
        # and use the same trimming policy as the prompt builder.
        query_text = (text or "")[: self.llm_cfg.text_first_chars]
        try:
            qv = self.embedder.embed(query_text)
        except OllamaUnavailable as exc:
            logger.warning("Embedder unavailable for few-shot: %s", exc)
            return []
        except Exception as exc:                                       # pragma: no cover
            logger.warning("Embedder error for few-shot: %s", exc)
            return []
        return self.store.search(qv, k=self.llm_cfg.fewshot_k)

    # ------------------------------------------------------------------
    def classify(
        self,
        *,
        text: str,
        filename: str,
        extra_categories: Optional[Iterable[str]] = None,
    ) -> tuple[Optional[ClassificationResult], ClassifierCallInfo]:
        """Run a full classification: retrieve few-shot → build prompt → call backend."""
        if not self.enabled:
            return self.backend.classify(system="", user="")

        cats = list(self.categories)
        if extra_categories:
            for c in extra_categories:
                if c not in cats:
                    cats.append(c)

        neighbors = self.fetch_neighbors(text)

        system, user = build_classification_prompt(
            text=text,
            filename=filename,
            categories=cats,
            neighbors=neighbors,
            known_issuers=self.known_issuers,
            first_chars=self.llm_cfg.text_first_chars,
            last_chars=self.llm_cfg.text_last_chars,
            fewshot_excerpt_chars=self.llm_cfg.fewshot_excerpt_chars,
        )

        result, info = self.backend.classify(
            system=system, user=user, timeout_s=self.llm_cfg.timeout_s,
        )
        # Annotate diagnostics with how many neighbors went into the prompt
        info.fewshot_count = len(neighbors)

        # Reject category that the model invented (defensive — Ollama JSON
        # mode is strict but llamas occasionally hallucinate).
        if result is not None and result.category not in cats:
            logger.info(
                "LLM proposed non-config category %r; falling back to keyword. "
                "(Add it to routing-config.yaml `categories:` if it's a real folder.)",
                result.category,
            )
            return None, ClassifierCallInfo(
                backend=info.backend, duration_ms=info.duration_ms,
                fewshot_count=info.fewshot_count, prompt_chars=info.prompt_chars,
                completion_chars=info.completion_chars,
                error=f"invalid category: {result.category!r}",
            )

        return result, info


__all__ = ["DEFAULT_FEWSHOT_K", "LLMConfig", "LLMClassifier"]
