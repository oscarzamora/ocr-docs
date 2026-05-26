"""SQLite-backed embedding store for the feedback log.

Stores one embedding per ``confirmed`` feedback record so the LLM classifier
(Step 4) can retrieve the *k* most-similar past decisions as few-shot
exemplars.

Design choices:
- **Storage**: plain SQLite (stdlib, zero deps). One table ``examples`` with
  the record's identity fields + a BLOB of the embedding (np.float32). At
  10k-100k records, a one-shot table scan + numpy dot product is sub-second
  on a laptop — no need for a vector index extension.
- **Hash key**: ``(original_filename, ts)`` is the natural key from the
  JSONL log. We additionally store ``text_hash`` so dedupe can detect
  re-embeds of unchanged content.
- **Embedding model**: ``nomic-embed-text`` via Ollama (768 dims, ~270 MB).
  Configurable via ``OllamaEmbedder(model=...)``.
- **Failure mode**: a record that can't be embedded is logged and skipped;
  the store remains consistent.

CLI surface (added in cli.py):
    ocr-router feedback embed       incrementally embed new log entries
    ocr-router feedback search "…"  show top-k nearest decisions to a query
    ocr-router feedback embed-stats show DB size, by category, etc.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Bump when the on-disk schema changes incompatibly.
DB_SCHEMA_VERSION = 1

# Default Ollama embedding model. nomic-embed-text returns 768-dim vectors.
DEFAULT_EMBED_MODEL = "nomic-embed-text"

# Default vector dimension; verified on first insert.
DEFAULT_DIM = 768


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------

class OllamaEmbedder:
    """Thin wrapper over the official ``ollama`` Python client.

    Falls back to raising :class:`OllamaUnavailable` when the daemon or model
    is missing so callers can degrade gracefully (e.g. skip embedding step
    when running tests on a fresh machine).
    """

    def __init__(self, model: str = DEFAULT_EMBED_MODEL, host: Optional[str] = None):
        self.model = model
        self.host = host
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
            except ImportError as exc:                                # pragma: no cover
                raise OllamaUnavailable(
                    "ollama package is not installed; pip install ollama"
                ) from exc
            self._client = ollama.Client(host=self.host) if self.host else ollama.Client()
        return self._client

    def embed(self, text: str) -> np.ndarray:
        """Embed one string. Returns float32 numpy array."""
        client = self._get_client()
        try:
            resp = client.embeddings(model=self.model, prompt=text)
        except Exception as exc:
            raise OllamaUnavailable(f"Ollama embed call failed: {exc}") from exc
        vec = resp.get("embedding") if isinstance(resp, dict) else getattr(resp, "embedding", None)
        if not vec:
            raise OllamaUnavailable(f"Ollama returned no embedding for model {self.model!r}")
        return np.asarray(vec, dtype=np.float32)

    def embed_many(self, texts: list[str]) -> list[np.ndarray]:
        """Embed a batch sequentially. Ollama doesn't batch on the wire."""
        return [self.embed(t) for t in texts]


class OllamaUnavailable(RuntimeError):
    """Raised when Ollama / the embedding model is not reachable."""


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS examples (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    ts                TEXT NOT NULL,
    category          TEXT,
    issuer            TEXT,
    folder            TEXT,
    final_filename    TEXT,
    backend           TEXT,
    text_hash         TEXT NOT NULL,
    text_excerpt      TEXT NOT NULL,
    dim               INTEGER NOT NULL,
    embedding         BLOB NOT NULL,
    embed_model       TEXT NOT NULL,
    UNIQUE(original_filename, ts)
);

CREATE INDEX IF NOT EXISTS idx_examples_category ON examples(category);
CREATE INDEX IF NOT EXISTS idx_examples_text_hash ON examples(text_hash);
"""


def _text_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


@dataclass
class Neighbor:
    """One hit from :meth:`EmbeddingStore.search`."""
    score: float                # cosine similarity in [-1, 1]
    category: Optional[str]
    issuer: Optional[str]
    folder: Optional[str]
    final_filename: Optional[str]
    original_filename: str
    text_excerpt: str
    ts: str


@dataclass
class EmbedStats:
    total: int
    dim: int
    embed_model: str
    by_category: dict[str, int]


class EmbeddingStore:
    """SQLite store for embedded ``confirmed`` feedback records."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(_SCHEMA_SQL)
        # Record schema version on first insert
        cur = self._conn.execute("SELECT version FROM schema_info LIMIT 1")
        row = cur.fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_info(version) VALUES (?)", (DB_SCHEMA_VERSION,))
            self._conn.commit()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(
        self,
        *,
        original_filename: str,
        ts: str,
        category: Optional[str],
        issuer: Optional[str],
        folder: Optional[str],
        final_filename: Optional[str],
        backend: Optional[str],
        text_excerpt: str,
        embedding: np.ndarray,
        embed_model: str,
    ) -> bool:
        """Insert or replace by (original_filename, ts). Returns True on insert."""
        if embedding.dtype != np.float32:
            embedding = embedding.astype(np.float32)
        blob = embedding.tobytes()
        try:
            cur = self._conn.execute(
                """
                INSERT INTO examples
                    (original_filename, ts, category, issuer, folder, final_filename,
                     backend, text_hash, text_excerpt, dim, embedding, embed_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(original_filename, ts) DO UPDATE SET
                    category       = excluded.category,
                    issuer         = excluded.issuer,
                    folder         = excluded.folder,
                    final_filename = excluded.final_filename,
                    backend        = excluded.backend,
                    text_hash      = excluded.text_hash,
                    text_excerpt   = excluded.text_excerpt,
                    dim            = excluded.dim,
                    embedding      = excluded.embedding,
                    embed_model    = excluded.embed_model
                """,
                (
                    original_filename, ts, category, issuer, folder, final_filename,
                    backend, _text_hash(text_excerpt), text_excerpt,
                    int(embedding.shape[0]), blob, embed_model,
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as exc:
            logger.warning("EmbeddingStore.upsert failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def existing_keys(self) -> set[tuple[str, str]]:
        """Return the set of (original_filename, ts) already embedded."""
        cur = self._conn.execute("SELECT original_filename, ts FROM examples")
        return {(r[0], r[1]) for r in cur.fetchall()}

    def existing_hashes(self) -> set[str]:
        cur = self._conn.execute("SELECT text_hash FROM examples")
        return {r[0] for r in cur.fetchall()}

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM examples")
        return cur.fetchone()[0]

    def stats(self) -> EmbedStats:
        total = self.count()
        if total == 0:
            return EmbedStats(0, 0, "", {})
        cur = self._conn.execute("SELECT dim, embed_model FROM examples LIMIT 1")
        dim, model = cur.fetchone()
        cur = self._conn.execute(
            "SELECT COALESCE(category, '(none)') AS c, COUNT(*) FROM examples GROUP BY c ORDER BY 2 DESC"
        )
        by_cat = {r[0]: r[1] for r in cur.fetchall()}
        return EmbedStats(total=total, dim=dim, embed_model=model, by_category=by_cat)

    def all_records_with_vectors(
        self, category: Optional[str] = None
    ) -> tuple[list[dict], np.ndarray]:
        """Load every (or one-category-only) record + matrix of vectors.

        Returns (records, matrix). ``matrix`` is shape ``(N, dim)`` float32.
        """
        if category:
            cur = self._conn.execute(
                """
                SELECT original_filename, ts, category, issuer, folder,
                       final_filename, text_excerpt, dim, embedding
                FROM examples WHERE category = ?
                """,
                (category,),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT original_filename, ts, category, issuer, folder,
                       final_filename, text_excerpt, dim, embedding
                FROM examples
                """
            )
        records: list[dict] = []
        vecs: list[np.ndarray] = []
        for orig, ts, cat, iss, folder, final_name, text, dim, blob in cur.fetchall():
            v = np.frombuffer(blob, dtype=np.float32)
            if v.shape[0] != dim:
                logger.warning("Skipping record %s: dim mismatch", orig)
                continue
            vecs.append(v)
            records.append({
                "original_filename": orig, "ts": ts,
                "category": cat, "issuer": iss, "folder": folder,
                "final_filename": final_name, "text_excerpt": text,
            })
        if not vecs:
            return [], np.zeros((0, 0), dtype=np.float32)
        return records, np.stack(vecs)

    def search(
        self,
        query_vec: np.ndarray,
        *,
        k: int = 5,
        category: Optional[str] = None,
    ) -> list[Neighbor]:
        """Return the top-``k`` nearest neighbors by cosine similarity.

        Empty store returns an empty list.
        """
        records, matrix = self.all_records_with_vectors(category=category)
        if matrix.shape[0] == 0:
            return []

        q = query_vec.astype(np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []
        q = q / q_norm

        # L2-normalize each row in matrix then dot
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normed = matrix / norms
        scores = normed @ q                                      # shape (N,)

        idx = np.argsort(-scores)[:k]
        out: list[Neighbor] = []
        for i in idx:
            r = records[int(i)]
            out.append(Neighbor(
                score=float(scores[int(i)]),
                category=r["category"],
                issuer=r["issuer"],
                folder=r["folder"],
                final_filename=r["final_filename"],
                original_filename=r["original_filename"],
                text_excerpt=r["text_excerpt"],
                ts=r["ts"],
            ))
        return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:                                            # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Incremental indexing from the JSONL log
# ---------------------------------------------------------------------------

@dataclass
class IndexStats:
    seen: int = 0
    skipped_event: int = 0     # not a 'confirmed' record
    skipped_empty: int = 0     # text excerpt blank
    skipped_existing: int = 0  # already in store
    embedded: int = 0
    errors: int = 0


def index_log_into_store(
    log_records: Iterable[dict],
    store: EmbeddingStore,
    embedder: OllamaEmbedder,
    *,
    progress_cb=None,
) -> IndexStats:
    """Embed every ``confirmed`` record in ``log_records`` not already present.

    ``log_records`` is an iterable of dicts as produced by
    :meth:`ocr_router.feedback.FeedbackLog.iter_records`.

    The first Ollama error aborts the loop because it usually means the
    daemon is down — there is no point continuing to fail.
    """
    stats = IndexStats()
    existing = store.existing_keys()

    records = list(log_records)
    total = len(records)
    for i, r in enumerate(records, 1):
        stats.seen += 1
        if progress_cb:
            try:
                progress_cb(i, total)
            except Exception:                                        # pragma: no cover
                pass

        if r.get("event") != "confirmed":
            stats.skipped_event += 1
            continue
        key = (r.get("original_filename", ""), r.get("ts", ""))
        if key in existing:
            stats.skipped_existing += 1
            continue
        text = (r.get("text_excerpt") or "").strip()
        if not text:
            stats.skipped_empty += 1
            continue

        try:
            vec = embedder.embed(text)
        except OllamaUnavailable as exc:
            logger.warning("Embedder unavailable, aborting indexing: %s", exc)
            stats.errors += 1
            break
        except Exception as exc:                                     # pragma: no cover
            logger.warning("Embed failed for %s: %s", r.get("original_filename"), exc)
            stats.errors += 1
            continue

        ok = store.upsert(
            original_filename=r.get("original_filename", ""),
            ts=r.get("ts", ""),
            category=r.get("final_category") or r.get("proposed_category"),
            issuer=r.get("final_issuer") or r.get("proposed_issuer"),
            folder=r.get("final_folder") or r.get("proposed_folder"),
            final_filename=r.get("final_filename") or r.get("proposed_filename"),
            backend=r.get("backend"),
            text_excerpt=text,
            embedding=vec,
            embed_model=embedder.model,
        )
        if ok:
            stats.embedded += 1

    return stats


__all__ = [
    "DEFAULT_EMBED_MODEL",
    "DEFAULT_DIM",
    "DB_SCHEMA_VERSION",
    "OllamaEmbedder",
    "OllamaUnavailable",
    "EmbeddingStore",
    "Neighbor",
    "EmbedStats",
    "IndexStats",
    "index_log_into_store",
]
