"""Structure-aware chunking.

Strategy: one section (heading) at a time; sections longer than ``max_words`` are split
into windows on paragraph / list-item boundaries with ``overlap_words`` of overlap. The
section title is prepended to the embedded text (not the displayed text) so that short
procedure steps still carry their context ("High Bearing Temperature: 1. Verify ...").
"""

from __future__ import annotations

import hashlib
import re

from rag.ingestion.sanitize import screen
from rag.models import Chunk, Document


def _units(text: str) -> list[str]:
    """Paragraphs and list items are the atomic units we try not to split."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if lines and all(re.match(r"^(\d+[.)]|[-*])\s", ln) for ln in lines):
            units.extend(lines)
        elif lines:
            units.append(" ".join(lines))
    return units


def _windows(units: list[str], max_words: int, overlap_words: int) -> list[str]:
    windows: list[str] = []
    cur: list[str] = []
    count = 0
    for u in units:
        n = len(u.split())
        if cur and count + n > max_words:
            windows.append("\n".join(cur))
            # carry over trailing units up to overlap_words
            carry: list[str] = []
            c = 0
            for prev in reversed(cur):
                c += len(prev.split())
                if c > overlap_words:
                    break
                carry.insert(0, prev)
            cur, count = carry, sum(len(x.split()) for x in carry)
        if n > max_words:  # a single huge paragraph: hard split on words
            words = u.split()
            step = max(1, max_words - overlap_words)
            for i in range(0, len(words), step):
                windows.append(" ".join(words[i : i + max_words]))
            cur, count = [], 0
            continue
        cur.append(u)
        count += n
    if cur:
        windows.append("\n".join(cur))
    return windows


def chunk_document(doc: Document, max_words: int = 160, overlap_words: int = 30) -> list[Chunk]:
    chunks: list[Chunk] = []
    pos = 0
    for section, text in doc.sections:
        for window in _windows(_units(text), max_words, overlap_words):
            clean, flags = screen(window)
            if not clean or len(clean.split()) < 4:
                continue
            pos += 1
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{pos:03d}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    doc_type=doc.doc_type,
                    section=section,
                    text=clean,
                    source_path=doc.source_path,
                    version=doc.version,
                    effective_date=doc.effective_date,
                    trust=doc.trust,
                    asset_types=doc.asset_types,
                    alarm_names=doc.alarm_names,
                    sites=doc.sites,
                    position=pos,
                    word_count=len(clean.split()),
                    checksum=hashlib.sha256(clean.encode()).hexdigest()[:16],
                    injection_flags=flags,
                )
            )
    return chunks
