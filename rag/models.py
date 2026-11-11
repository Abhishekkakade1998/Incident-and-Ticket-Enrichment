"""Domain models shared by RAG ingestion and retrieval."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DocType = Literal[
    "operating_procedure",
    "troubleshooting_guide",
    "knowledge_article",
    "resolution_notes",
    "escalation_procedure",
    "alarm_philosophy",
    "safety_instruction",
]


class Document(BaseModel):
    doc_id: str
    title: str
    doc_type: str
    source_path: str
    version: str | None = None
    effective_date: str | None = None
    owner: str | None = None
    trust: Literal["verified", "unverified"] = "verified"
    asset_types: list[str] = Field(default_factory=list)
    alarm_names: list[str] = Field(default_factory=list)
    sites: list[str] = Field(default_factory=list)
    sections: list[tuple[str, str]] = Field(default_factory=list, description="(heading path, text)")
    checksum: str = ""


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    section: str
    text: str
    source_path: str
    version: str | None = None
    effective_date: str | None = None
    trust: str = "verified"
    asset_types: list[str] = Field(default_factory=list)
    alarm_names: list[str] = Field(default_factory=list)
    sites: list[str] = Field(default_factory=list)
    position: int = 0
    word_count: int = 0
    checksum: str = ""
    injection_flags: list[str] = Field(default_factory=list, description="Prompt-injection patterns found and neutralised")


class Citation(BaseModel):
    ref: str = Field(description="Inline marker used in the answer, e.g. [D1]")
    doc_id: str
    title: str
    section: str
    chunk_id: str
    doc_type: str
    source_path: str
    version: str | None = None
    asset_types: list[str] = Field(default_factory=list)
    score: float
    snippet: str


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float
    bm25: float
    vector: float
    coverage: float = 0.0
    boosts: dict[str, float] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    query: str
    filters: dict[str, Any]
    hits: list[RetrievedChunk]
    confidence: Literal["high", "medium", "low", "none"]
    warnings: list[str] = Field(default_factory=list)
    excluded_chunk_ids: list[str] = Field(default_factory=list)

    def citations(self, prefix: str = "D") -> list[Citation]:
        out = []
        for i, h in enumerate(self.hits, start=1):
            c = h.chunk
            snippet = c.text if len(c.text) <= 320 else c.text[:317].rsplit(" ", 1)[0] + "..."
            out.append(
                Citation(
                    ref=f"[{prefix}{i}]",
                    doc_id=c.doc_id,
                    title=c.title,
                    section=c.section,
                    chunk_id=c.chunk_id,
                    doc_type=c.doc_type,
                    source_path=c.source_path,
                    version=c.version,
                    asset_types=c.asset_types,
                    score=round(min(h.score, 1.0), 3),  # display relevance in [0, 1]; raw score stays in the trace
                    snippet=snippet,
                )
            )
        return out
