"""Prompt-injection screening for document text.

Retrieved documents are *untrusted data*. At ingestion time we detect sentences that
look like instructions to an AI system (override instructions, request secrets, bypass
approval/safety) and replace them with a neutral marker. The chunk keeps a list of
``injection_flags`` so retrieval can down-rank or exclude it and the GUI can warn.
This is defence in depth: the generation prompt also fences document text as data.
"""

from __future__ import annotations

import re

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(previous|prior|above|all|system)\b[^.]{0,30}\b(instructions?|prompts?|rules?)\b",
        re.I,
    ),
    "role_hijack": re.compile(r"\byou are now\b|\bact as\b[^.]{0,30}\b(admin|system|developer)\b|\bmaintenance mode\b", re.I),
    "secret_exfiltration": re.compile(
        r"\b(reveal|include|print|send|output|share)\b[^.]{0,60}\b(api[_ ]?keys?|tokens?|passwords?|secrets?|credentials?|ALARM_API_TOKEN)\b",
        re.I,
    ),
    "approval_bypass": re.compile(r"\bwithout (asking|approval|confirmation)\b|\bskip (the )?(approval|confirmation)\b", re.I),
    "safety_bypass": re.compile(r"\b(disregard|bypass|disable|defeat)\b[^.]{0,40}\b(safety|trip|interlock|procedure)\b", re.I),
}

NEUTRALISED = "[content removed: instruction-like text in source document]"
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def screen(text: str) -> tuple[str, list[str]]:
    """Return (sanitised text, flags). Offending sentences are replaced, others kept."""
    flags: list[str] = []
    lines_out: list[str] = []
    for line in text.split("\n"):  # keep line structure (list items stay list items)
        out: list[str] = []
        for sentence in _SENTENCE.split(line):
            hit = [name for name, pat in INJECTION_PATTERNS.items() if pat.search(sentence)]
            if hit:
                flags.extend(hit)
                if not out or out[-1] != NEUTRALISED:
                    out.append(NEUTRALISED)
            else:
                out.append(sentence)
        lines_out.append(" ".join(out).strip())
    return "\n".join(ln for ln in lines_out if ln).strip(), sorted(set(flags))


def contains_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS.values())
