"""Embedding providers.

The default ``HashingEmbedder`` is a deterministic, dependency-free dense embedding:
signed feature hashing of stemmed unigrams, bigrams and character 4-grams, with a small
domain synonym map, L2-normalised. It needs no model download, so ingestion and tests
are fully reproducible offline. It is intentionally behind the ``Embedder`` protocol so a
neural model (e.g. a sentence-transformer or a hosted embedding API) can be swapped in
via the ingestion ``--embedder`` option without touching retrieval code.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "is",
    "are",
    "be",
    "with",
    "by",
    "at",
    "as",
    "if",
    "it",
    "this",
    "that",
    "from",
    "than",
    "within",
    "must",
    "shall",
    "should",
    "any",
    "all",
    "not",
    "was",
    "were",
    "what",
    "which",
    "how",
    "do",
    "does",
    "i",
    "me",
    "my",
    "we",
    "our",
    "please",
    "can",
    "give",
    "show",
    "get",
    "find",
}
SYNONYMS = {
    "npsh": ["suction", "cavitation"],
    "cavitation": ["npsh", "suction"],
    "procedure": ["sop", "steps"],
    "sop": ["procedure"],
    "troubleshoot": ["diagnose", "fault"],
    "escalate": ["escalation", "notify"],
    "escalation": ["escalate", "priority"],
    "anti": ["recycle"],
    "recycle": ["anti", "surge"],
    "overcurrent": ["current", "overload"],
    "bearing": ["lubrication"],
    "seal": ["leak"],
    "leak": ["seal"],
}


def stem(token: str) -> str:
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    name = "hashing-v1"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _bucket(self, feature: str) -> tuple[int, float]:
        h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % self.dim
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        raw = [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]
        toks = [stem(t) for t in raw]
        feats: list[tuple[str, float]] = [(f"u:{t}", 1.0) for t in toks]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in zip(toks, toks[1:], strict=False)]
        for t in raw:
            for syn in SYNONYMS.get(t, []):
                feats.append((f"u:{stem(syn)}", 0.5))
            padded = f"#{t}#"
            feats += [(f"c:{padded[i : i + 4]}", 0.25) for i in range(max(1, len(padded) - 3))]
        for f, w in feats:
            idx, sign = self._bucket(f)
            vec[idx] += sign * w
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def get_embedder(name: str = "hashing-v1", dim: int = 512) -> Embedder:
    if name == "hashing-v1":
        return HashingEmbedder(dim)
    raise ValueError(f"unknown embedder '{name}' (available: hashing-v1)")


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))
