"""Prompt construction for the LLM classifier.

Kept separate from backends.py so we can iterate on wording without touching
transport code, and so tests can assert on the exact prompt shape.

The contract:
- ``system`` describes the role + the JSON schema the model must return.
- ``user`` carries the document text excerpt + (optional) few-shot exemplars
  drawn from the embedding store.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ocr_router.feedback.store import Neighbor


# How much of the OCR text to feed the model. llama3.2:3b handles ~4k tokens
# comfortably; chars-to-tokens is roughly 4:1 for English text.
DEFAULT_TEXT_FIRST_CHARS = 2500
DEFAULT_TEXT_LAST_CHARS = 1000

# Few-shot exemplars: keep each one short so we don't blow the context window.
DEFAULT_FEWSHOT_EXCERPT_CHARS = 400


def _categories_block(categories: Iterable[str]) -> str:
    """Render the allowed category list as a numbered bullet list."""
    cats = list(categories)
    if not cats:
        return "(no categories configured)"
    return "\n".join(f"  - {c}" for c in cats)


def _trim_excerpt(text: str,
                  first_chars: int = DEFAULT_TEXT_FIRST_CHARS,
                  last_chars: int = DEFAULT_TEXT_LAST_CHARS) -> str:
    """Trim a long OCR string to ``first_chars`` + ``last_chars`` with a marker."""
    text = (text or "").strip()
    if len(text) <= first_chars + last_chars:
        return text
    head = text[:first_chars]
    tail = text[-last_chars:] if last_chars > 0 else ""
    return f"{head}\n…[{len(text) - first_chars - last_chars} chars omitted]…\n{tail}"


def _format_neighbor(n: Neighbor, max_excerpt_chars: int) -> str:
    excerpt = (n.text_excerpt or "").strip().replace("\n", " ")
    if len(excerpt) > max_excerpt_chars:
        excerpt = excerpt[:max_excerpt_chars] + "…"
    issuer = n.issuer or "—"
    return (
        f"- Text: {excerpt!r}\n"
        f"  → category={n.category!r}, issuer={issuer!r}"
    )


def build_classification_prompt(
    *,
    text: str,
    filename: str,
    categories: Iterable[str],
    neighbors: Optional[list[Neighbor]] = None,
    known_issuers: Optional[Iterable[str]] = None,
    first_chars: int = DEFAULT_TEXT_FIRST_CHARS,
    last_chars: int = DEFAULT_TEXT_LAST_CHARS,
    fewshot_excerpt_chars: int = DEFAULT_FEWSHOT_EXCERPT_CHARS,
) -> tuple[str, str]:
    """Build (system, user) messages for the LLM.

    The system message defines the role + strict JSON schema.
    The user message holds the document excerpt, optional few-shot exemplars
    drawn from past confirmed decisions, and the request to classify.
    """
    cat_block = _categories_block(categories)

    system = (
        "You are an expert document classifier for personal financial and "
        "household paperwork. You will be shown the OCR'd text of a single "
        "document, optionally preceded by examples of past human-confirmed "
        "classifications.\n"
        "\n"
        "Pick exactly one category from the list below — DO NOT invent new "
        "categories. If none fit, return the closest match and set confidence "
        "below 0.5.\n"
        "\n"
        "Allowed categories:\n"
        f"{cat_block}\n"
        "\n"
        "Return ONLY a single JSON object with this exact shape:\n"
        '{\n'
        '  "category":   <one of the allowed categories>,\n'
        '  "issuer":     <issuer name as it appears, or null if unclear>,\n'
        '  "confidence": <number from 0.0 to 1.0>,\n'
        '  "reasons":    [<1-3 short bullets explaining the verdict>]\n'
        '}\n'
        "No prose, no markdown, no extra fields."
    )

    parts: list[str] = []

    # Inject few-shot exemplars from past confirmed decisions
    neighbors = neighbors or []
    if neighbors:
        parts.append(
            f"Past confirmed decisions on the {len(neighbors)} most similar documents:"
        )
        for n in neighbors:
            parts.append(_format_neighbor(n, fewshot_excerpt_chars))
        parts.append("")  # blank line

    # Hint at known issuers so the model produces consistent names
    issuers = list(known_issuers or [])
    if issuers:
        sample = ", ".join(sorted(set(issuers))[:30])
        parts.append(f"Known canonical issuer names you may reuse: {sample}")
        parts.append("")

    excerpt = _trim_excerpt(text, first_chars=first_chars, last_chars=last_chars)
    parts.append(f"Filename: {filename}")
    parts.append("Document text:")
    parts.append('"""')
    parts.append(excerpt)
    parts.append('"""')
    parts.append("")
    parts.append("Classify the document. Return the JSON object now.")

    user = "\n".join(parts)
    return system, user


__all__ = [
    "DEFAULT_TEXT_FIRST_CHARS",
    "DEFAULT_TEXT_LAST_CHARS",
    "DEFAULT_FEWSHOT_EXCERPT_CHARS",
    "build_classification_prompt",
]
