"""Hybrid retrieval: BM25 + dense vectors fused with Reciprocal Rank Fusion, then a
metadata-aware rerank.

Filters
  * hard filters (``doc_types``, ``sites``) remove chunks outright;
  * soft context (``asset_type``, ``alarm_names``) boosts matching chunks, so a generic
    escalation procedure can still be retrieved for any asset.

Trust boundary
  * chunks with prompt-injection flags or from ``trust: unverified`` sources are excluded
    from results by default and reported in ``warnings`` / ``excluded_chunk_ids``.

Confidence
  * derived from the best fused score and lexical evidence; ``none`` / ``low`` lets the
    orchestrator answer "insufficient documentation" instead of guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from common.logging import log_event
from rag.embeddings import Embedder, get_embedder, tokenize
from rag.index_store import VectorIndex
from rag.models import Chunk, RetrievalResult, RetrievedChunk

log = logging.getLogger("rag.retrieval")
RRF_K = 60


@dataclass
class RetrievalFilters:
    doc_types: list[str] | None = None
    sites: list[str] | None = None
    asset_type: str | None = None
    alarm_names: list[str] | None = None
    include_unverified: bool = False
    strict_asset_type: bool = False  # hard-filter: drop chunks scoped to *other* asset types

    def as_dict(self) -> dict[str, object]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], False)}


class Retriever:
    def __init__(self, index: VectorIndex, embedder: Embedder | None = None) -> None:
        self.index = index
        self.embedder = embedder or get_embedder(index.embedder_name, index.dim)

    @classmethod
    def from_path(cls, path: Path) -> Retriever:
        return cls(VectorIndex.load(path))

    def _passes(self, c: Chunk, f: RetrievalFilters) -> bool:
        if f.doc_types and c.doc_type not in f.doc_types:
            return False
        if f.strict_asset_type and f.asset_type and c.asset_types and f.asset_type not in c.asset_types:
            return False
        return not (f.sites and c.sites and not set(f.sites) & set(c.sites))

    def search(self, query: str, filters: RetrievalFilters | None = None, k: int = 5, min_score: float = 0.3) -> RetrievalResult:
        f = filters or RetrievalFilters()
        idx = self.index
        if not query.strip() or not idx.chunks:
            return RetrievalResult(query=query, filters=f.as_dict(), hits=[], confidence="none", warnings=["empty query or index"])
        bm = idx.bm25(query)
        dn = idx.dense(self.embedder.embed(query))
        candidates = [i for i, c in enumerate(idx.chunks) if self._passes(c, f)]
        bm_rank = {i: r for r, i in enumerate(sorted(candidates, key=lambda i: -bm[i]), start=1)}
        dn_rank = {i: r for r, i in enumerate(sorted(candidates, key=lambda i: -dn[i]), start=1)}
        max_bm = max((bm[i] for i in candidates), default=0.0) or 1.0
        alarm_set = {a.lower() for a in (f.alarm_names or [])}
        qtok = set(tokenize(query))

        scored: list[RetrievedChunk] = []
        warnings: list[str] = []
        excluded: list[str] = []
        for i in candidates:
            c = idx.chunks[i]
            rrf = 1 / (RRF_K + bm_rank[i]) + 1 / (RRF_K + dn_rank[i])
            # Normalise RRF so the top possible value (rank 1 in both) is 1.0
            fused = rrf / (2 / (RRF_K + 1))
            # Blend relative rank with absolute evidence (query-term coverage) so the best
            # match for an off-topic query still scores low.
            ctok = set(tokenize(f"{c.title} {c.section} {c.text}"))
            coverage = len(qtok & ctok) / len(qtok) if qtok else 0.0
            base = 0.4 * fused + 0.2 * (bm[i] / max_bm) + 0.4 * coverage
            boosts: dict[str, float] = {}
            if f.asset_type and f.asset_type in c.asset_types:
                boosts["asset_type"] = 0.08
            if alarm_set and alarm_set & {a.lower() for a in c.alarm_names}:
                boosts["alarm_name"] = 0.10
            if alarm_set and any(a in c.section.lower() for a in alarm_set):
                boosts["section_match"] = 0.12
            score = base + sum(boosts.values())
            if c.injection_flags or (c.trust == "unverified" and not f.include_unverified):
                if score >= min_score and coverage > 0:
                    excluded.append(c.chunk_id)
                continue
            scored.append(
                RetrievedChunk(chunk=c, score=round(score, 4), bm25=round(bm[i], 3), vector=round(dn[i], 3), coverage=round(coverage, 3), boosts=boosts)
            )
        scored.sort(key=lambda h: -h.score)
        hits = [h for h in scored if h.score >= min_score]
        hits = _diversify(hits, k)
        if excluded:
            warnings.append(f"{len(excluded)} matching chunk(s) excluded: untrusted source or prompt-injection content")
        confidence = _confidence(hits)
        if confidence in ("low", "none"):
            warnings.append("low retrieval confidence: documentation may not cover this question")
        log_event(
            log,
            "rag.retrieve",
            retrieval_query=query[:200],
            filters=f.as_dict(),
            retrieved=[h.chunk.chunk_id for h in hits],
            scores=[h.score for h in hits],
            confidence=confidence,
            excluded=excluded,
        )
        return RetrievalResult(query=query, filters=f.as_dict(), hits=hits, confidence=confidence, warnings=warnings, excluded_chunk_ids=excluded)


def _diversify(hits: list[RetrievedChunk], k: int, per_doc: int = 2) -> list[RetrievedChunk]:
    """Cap chunks per document so one long document cannot crowd out others."""
    out: list[RetrievedChunk] = []
    counts: dict[str, int] = {}
    for h in hits:
        d = h.chunk.doc_id
        if counts.get(d, 0) >= per_doc:
            continue
        counts[d] = counts.get(d, 0) + 1
        out.append(h)
        if len(out) >= k:
            break
    return out


def _confidence(hits: list[RetrievedChunk]) -> Literal["high", "medium", "low", "none"]:
    if not hits:
        return "none"
    top = hits[0]
    if top.coverage >= 0.5 and top.score >= 0.7:
        return "high"
    if top.coverage >= 0.34 and top.score >= 0.5:
        return "medium"
    return "low"
