"""File-backed vector + BM25 index.

The whole index (chunks, vectors, per-document checksums) is a single JSON file, written
atomically. That is enough for a corpus of hundreds of documents and keeps the stack free
of an extra database container. ``VectorIndex`` is the seam where a real vector DB
(pgvector, Qdrant, OpenSearch) would plug in.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rag.embeddings import Embedder, cosine, tokenize
from rag.models import Chunk

INDEX_VERSION = 1


@dataclass
class VectorIndex:
    embedder_name: str
    dim: int
    chunks: list[Chunk] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    doc_checksums: dict[str, str] = field(default_factory=dict)
    built_at: str = ""
    # BM25 statistics (derived, not persisted)
    _tf: list[Counter[str]] = field(default_factory=list, repr=False)
    _df: Counter[str] = field(default_factory=Counter, repr=False)
    _avgdl: float = 0.0

    def rebuild_stats(self) -> None:
        self._tf = [Counter(tokenize(f"{c.section} {c.text}")) for c in self.chunks]
        self._df = Counter()
        for tf in self._tf:
            self._df.update(tf.keys())
        self._avgdl = (sum(sum(tf.values()) for tf in self._tf) / len(self._tf)) if self._tf else 0.0

    def bm25(self, query: str, k1: float = 1.4, b: float = 0.75) -> list[float]:
        q = tokenize(query)
        n = len(self.chunks)
        scores = [0.0] * n
        for term in set(q):
            df = self._df.get(term, 0)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._tf):
                f = tf.get(term, 0)
                if f:
                    dl = sum(tf.values())
                    scores[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / (self._avgdl or 1)))
        return scores

    def dense(self, query_vec: list[float]) -> list[float]:
        return [cosine(query_vec, v) for v in self.vectors]

    # ------------------------------------------------------------------ persistence
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "index_version": INDEX_VERSION,
            "embedder": self.embedder_name,
            "dim": self.dim,
            "built_at": self.built_at or datetime.now(UTC).isoformat(timespec="seconds"),
            "doc_checksums": self.doc_checksums,
            "chunks": [c.model_dump() for c in self.chunks],
            "vectors": [[round(x, 5) for x in v] for v in self.vectors],
        }
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> VectorIndex:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("index_version") != INDEX_VERSION:
            raise ValueError(f"index version mismatch at {path}; re-run ingestion")
        idx = cls(
            embedder_name=data["embedder"],
            dim=data["dim"],
            chunks=[Chunk(**c) for c in data["chunks"]],
            vectors=data["vectors"],
            doc_checksums=data.get("doc_checksums", {}),
            built_at=data.get("built_at", ""),
        )
        idx.rebuild_stats()
        return idx

    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder, checksums: dict[str, str], previous: VectorIndex | None = None) -> VectorIndex:
        """Embed chunks, reusing vectors from ``previous`` for unchanged chunk checksums."""
        reuse: dict[tuple[str, str], list[float]] = {}
        if previous and previous.embedder_name == embedder.name and previous.dim == embedder.dim:
            reuse = {(c.chunk_id, c.checksum): v for c, v in zip(previous.chunks, previous.vectors, strict=True)}
        vectors = [reuse.get((c.chunk_id, c.checksum)) or embedder.embed(f"{c.title}. {c.section}. {c.text}") for c in chunks]
        idx = cls(embedder_name=embedder.name, dim=embedder.dim, chunks=chunks, vectors=vectors, doc_checksums=checksums)
        idx.built_at = datetime.now(UTC).isoformat(timespec="seconds")
        idx.rebuild_stats()
        return idx
