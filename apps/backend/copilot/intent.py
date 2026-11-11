"""Intent detection and entity extraction.

Deterministic, explainable rules (keyword scoring + regex entities) rather than an LLM
call, so planning is fast, testable and works offline. Entities are *references* only:
asset names are resolved later through the ``search_assets`` MCP tool, never against a
hard-coded list, so new assets in the source system work without code changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Intent = Literal[
    "prepare_incident",
    "investigate_asset",
    "similar_tickets",
    "summarize_issue",
    "add_procedure",
    "linked_tickets",
    "create_ticket",
    "knowledge_question",
    "list_tools",
    "help",
]

SITES = {"eastrefinery": "EastRefinery", "northplant": "NorthPlant", "southplant": "SouthPlant"}
ASSET_TYPES = {
    "pump": "pump",
    "pumps": "pump",
    "compressor": "compressor",
    "compressors": "compressor",
    "motor": "motor",
    "motors": "motor",
    "turbine": "turbine",
    "exchanger": "heat_exchanger",
    "deaerator": "vessel",
    "vessel": "vessel",
    "skid": "skid",
}

_ASSET_PHRASE = re.compile(r"\b((?:[A-Z][A-Za-z]+\s){0,3}(?:Pump|Compressor|Motor|Turbine|Exchanger|Deaerator|Skid|System)(?:\s+[A-Z]{0,3}-?\d{2,4})?)\b")
_TAG = re.compile(r"\b([A-Z]{1,3}-\d{3})\b")
_ASSET_ID = re.compile(r"\b(AST-[A-Z]{3}-\d{3})\b")
_ALARM_ID = re.compile(r"\b(ALM-\d{6})\b")
_TICKET_ID = re.compile(r"\b(INC-\d{4,8})\b")
_UNIT = re.compile(r"\bunit\s*(\d+)\b", re.I)
_LOOKBACK = re.compile(r"\b(?:last|past|previous)\s+(\d+)\s*(day|days|week|weeks|month|months)\b", re.I)

RULES: dict[Intent, list[tuple[str, float]]] = {
    "prepare_incident": [
        (r"\bprepare\b.*\b(incident|ticket)\b", 3),
        (r"\b(draft|raise|open)\b.*\b(incident|ticket)\b", 2.5),
        (r"highest[- ]priority", 1.5),
        (r"\bincident for\b", 2),
    ],
    "investigate_asset": [
        (r"\binvestigate\b", 3),
        (r"\brecurring\b", 1.5),
        (r"contributing factors|root cause|why does|why is", 2),
        (r"\banaly[sz]e\b", 1.5),
        (r"\blast \d+ (days|weeks|months)\b", 1),
    ],
    "similar_tickets": [(r"\bsimilar\b.*\b(tickets?|incidents?|cases?)\b", 4), (r"\bhistorical\b.*\b(tickets?|incidents?)\b", 3), (r"\bhappened before\b", 2)],
    "summarize_issue": [(r"\bsummar(y|ise|ize)\b", 3), (r"likely cause", 1.5), (r"affected asset", 1.5), (r"recommended action", 1)],
    "add_procedure": [(r"\badd\b.*\b(procedure|sop|troubleshooting|guide)\b", 4), (r"\b(attach|include)\b.*\b(procedure|sop|guide)\b.*\b(ticket|draft)\b", 4)],
    "linked_tickets": [(r"\bopen tickets?\b", 3), (r"\b(tickets?|incidents?)\b.*\b(linked|related|correlated)\b", 3), (r"\bcorrelated assets?\b", 2)],
    "create_ticket": [
        (r"^\s*(please\s+)?(create|submit|file|raise) (the|this|that) (ticket|incident)", 5),
        (r"\bsubmit\b.*\bdraft\b", 4),
        (r"\bconfirm\b.*\b(ticket|draft)\b", 3),
    ],
    "knowledge_question": [
        (r"\b(what|how|when|who)\b", 1),
        (r"\bprocedure\b|\bsop\b|\bguide\b|\bpolicy\b|\bphilosophy\b", 1.5),
        (r"\bescalat", 1.5),
        (r"\bdefinition\b|\bdefine\b", 1),
    ],
    "list_tools": [(r"\b(list|show|what)\b.*\b(tools|capabilities)\b", 4)],
    "help": [(r"^\s*(help|hi|hello|hey)\b", 3)],
}


@dataclass
class Entities:
    asset_ref: str | None = None
    asset_id: str | None = None
    asset_type: str | None = None
    alarm_id: str | None = None
    ticket_id: str | None = None
    site: str | None = None
    unit: str | None = None
    lookback_days: int | None = None
    severity: list[str] | None = None
    status: list[str] | None = None
    refers_to_context: bool = False

    def as_dict(self) -> dict[str, object]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, False, [])}


@dataclass
class ParsedRequest:
    intent: Intent
    confidence: float
    entities: Entities
    scores: dict[str, float] = field(default_factory=dict)
    text: str = ""


def extract_entities(text: str) -> Entities:
    e = Entities()
    low = text.lower()
    for key, site in SITES.items():
        if key in low.replace(" ", ""):
            e.site = site
            break
    if m := _UNIT.search(text):
        e.unit = f"Unit {m.group(1)}"
    if m := _ASSET_ID.search(text):
        e.asset_id = m.group(1)
    if m := _ALARM_ID.search(text):
        e.alarm_id = m.group(1)
    if m := _TICKET_ID.search(text):
        e.ticket_id = m.group(1)
    if m := _ASSET_PHRASE.search(text):
        phrase = m.group(1).strip()
        # Drop leading verbs/determiners captured by the capitalised-word pattern.
        phrase = re.sub(r"^(?:For|On|The|This|That|Investigate|Show|Find|Prepare)\s+", "", phrase)
        if phrase.lower() not in ASSET_TYPES:
            e.asset_ref = phrase
    if not e.asset_ref and (m := _TAG.search(text)) and not m.group(1).startswith(("ALM", "INC", "AST")):
        e.asset_ref = m.group(1)
    for word, t in ASSET_TYPES.items():
        if re.search(rf"\b{word}\b", low):
            e.asset_type = t
            break
    if m := _LOOKBACK.search(text):
        n, unit = int(m.group(1)), m.group(2).lower()
        e.lookback_days = n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
    elif re.search(r"\blast (month)\b", low):
        e.lookback_days = 30
    elif re.search(r"\blast (week)\b", low):
        e.lookback_days = 7
    if re.search(r"high[- ]severity|high[- ]priority alarms|severe", low):
        e.severity = ["high", "critical"]
    elif re.search(r"\bcritical\b", low):
        e.severity = ["critical"]
    if re.search(r"\bactive\b", low):
        e.status = ["active", "acknowledged"]
    e.refers_to_context = bool(re.search(r"\b(this|that|the same|it|current)\b\s*(alarm|asset|compressor|pump|motor|issue|incident|ticket|draft)?", low))
    return e


def classify(text: str) -> ParsedRequest:
    low = text.lower().strip()
    scores: dict[str, float] = {}
    for name, rules in RULES.items():
        scores[name] = sum(w for pat, w in rules if re.search(pat, low))
    ents = extract_entities(text)
    # Structural hints
    if ents.asset_ref and ents.lookback_days:
        scores["investigate_asset"] += 1
    if ents.site and ents.status:
        scores["prepare_incident"] += 0.5
    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    if scores[best] == 0:
        fallback: Intent = "knowledge_question" if len(low.split()) > 3 else "help"
        return ParsedRequest(intent=fallback, confidence=0.2, entities=ents, scores=scores, text=text)
    return ParsedRequest(intent=best, confidence=round(scores[best] / total, 2), entities=ents, scores=scores, text=text)  # type: ignore[arg-type]
