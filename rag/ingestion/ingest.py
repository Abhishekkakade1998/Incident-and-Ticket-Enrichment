"""Ingestion CLI: ``python -m rag.ingestion.ingest --docs rag/documents --index rag/index/index.json``.

Pipeline: discover -> extract (per format) -> sanitise -> chunk -> embed -> write index.
Re-running is incremental: unchanged documents (same checksum) keep their vectors.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.logging import configure_logging, log_event
from rag.embeddings import get_embedder
from rag.index_store import VectorIndex
from rag.ingestion.chunking import chunk_document
from rag.ingestion.extract import ExtractionError, discover, load_document
from rag.models import Chunk

log = logging.getLogger("rag.ingest")


@dataclass
class IngestReport:
    documents: int = 0
    chunks: int = 0
    unchanged_documents: int = 0
    flagged_chunks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    index_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def ingest(
    docs_dir: Path, index_path: Path, *, embedder_name: str = "hashing-v1", max_words: int = 160, overlap_words: int = 30
) -> tuple[VectorIndex, IngestReport]:
    report = IngestReport(index_path=str(index_path))
    embedder = get_embedder(embedder_name)
    previous: VectorIndex | None = None
    if index_path.exists():
        try:
            previous = VectorIndex.load(index_path)
        except (ValueError, KeyError, json.JSONDecodeError):
            previous = None
    chunks: list[Chunk] = []
    checksums: dict[str, str] = {}
    seen_ids: set[str] = set()
    for path in discover(docs_dir):
        try:
            doc = load_document(path, root=docs_dir)
        except (ExtractionError, ValueError) as exc:
            report.errors.append(f"{path.name}: {exc}")
            continue
        if doc.doc_id in seen_ids:
            report.errors.append(f"{path.name}: duplicate doc_id {doc.doc_id}")
            continue
        seen_ids.add(doc.doc_id)
        if previous and previous.doc_checksums.get(doc.doc_id) == doc.checksum:
            report.unchanged_documents += 1
        doc_chunks = chunk_document(doc, max_words=max_words, overlap_words=overlap_words)
        chunks.extend(doc_chunks)
        checksums[doc.doc_id] = doc.checksum
        report.documents += 1
        report.flagged_chunks += [c.chunk_id for c in doc_chunks if c.injection_flags]
    index = VectorIndex.build(chunks, embedder, checksums, previous)
    index.save(index_path)
    report.chunks = len(chunks)
    log_event(log, "rag.ingest.complete", **report.as_dict())
    return index, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest documents into the RAG index")
    parser.add_argument("--docs", default="rag/documents")
    parser.add_argument("--index", default="rag/index/index.json")
    parser.add_argument("--embedder", default="hashing-v1")
    parser.add_argument("--max-words", type=int, default=160)
    parser.add_argument("--overlap-words", type=int, default=30)
    args = parser.parse_args(argv)
    configure_logging("rag-ingest", "WARNING")
    _, report = ingest(Path(args.docs), Path(args.index), embedder_name=args.embedder, max_words=args.max_words, overlap_words=args.overlap_words)
    print(json.dumps(report.as_dict(), indent=2))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
