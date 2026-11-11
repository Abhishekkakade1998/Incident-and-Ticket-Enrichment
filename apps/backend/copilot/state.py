"""Conversation context and ticket drafts (in-memory, bounded)."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    title: str
    section: str | None = None


class TicketFields(BaseModel):
    """User-editable fields of a ticket draft. Mirrors the ticketing MCP ``TicketDraft``."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=10, max_length=20000)
    priority: str = Field(pattern=r"^P[1-4]$")
    asset_id: str = Field(pattern=r"^AST-[A-Z]{3}-\d{3}$")
    asset_name: str | None = None
    site: str | None = None
    alarm_ids: list[str] = Field(default_factory=list, max_length=50)
    alarm_name: str | None = None
    category: str = "alarm_incident"
    assignee_group: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    linked_documents: list[DocumentLink] = Field(default_factory=list, max_length=20)
    similar_ticket_ids: list[str] = Field(default_factory=list, max_length=20)


class TicketDraft(BaseModel):
    draft_id: str
    conversation_id: str
    status: str = "draft"  # draft | submitted | rejected | failed
    fields: TicketFields
    rationale: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    ticket_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class Conversation:
    conversation_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    asset: dict[str, Any] | None = None
    alarm: dict[str, Any] | None = None
    enrichment: dict[str, Any] = field(default_factory=dict)
    correlated_assets: list[dict[str, Any]] = field(default_factory=list)
    similar_tickets: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    draft_id: str | None = None
    updated_at: float = field(default_factory=time.time)

    def remember(self, role: str, content: str, **extra: Any) -> None:
        self.messages.append({"role": role, "content": content[:4000], "ts": time.time(), **extra})
        self.messages = self.messages[-40:]
        self.updated_at = time.time()

    def context_summary(self) -> dict[str, Any]:
        return {
            "asset": {k: self.asset.get(k) for k in ("asset_id", "asset_name", "site")} if self.asset else None,
            "alarm": {k: self.alarm.get(k) for k in ("alarm_id", "alarm_name", "severity", "status")} if self.alarm else None,
            "draft_id": self.draft_id,
            "correlated_assets": [a.get("asset_name") for a in self.correlated_assets],
        }


class ConversationStore:
    def __init__(self, max_conversations: int = 500, ttl_s: int = 6 * 3600) -> None:
        self._items: OrderedDict[str, Conversation] = OrderedDict()
        self.drafts: dict[str, TicketDraft] = {}
        self.max = max_conversations
        self.ttl_s = ttl_s

    def get_or_create(self, conversation_id: str | None) -> Conversation:
        self._evict()
        if conversation_id and conversation_id in self._items:
            self._items.move_to_end(conversation_id)
            return self._items[conversation_id]
        cid = conversation_id if conversation_id and len(conversation_id) <= 64 else f"conv-{uuid.uuid4().hex[:12]}"
        conv = Conversation(conversation_id=cid)
        self._items[cid] = conv
        return conv

    def get(self, conversation_id: str) -> Conversation | None:
        return self._items.get(conversation_id)

    def _evict(self) -> None:
        now = time.time()
        for cid in [c for c, v in self._items.items() if now - v.updated_at > self.ttl_s]:
            self._items.pop(cid, None)
        while len(self._items) > self.max:
            self._items.popitem(last=False)

    def save_draft(self, draft: TicketDraft) -> TicketDraft:
        draft.updated_at = time.time()
        self.drafts[draft.draft_id] = draft
        return draft

    def new_draft_id(self) -> str:
        return f"DRAFT-{uuid.uuid4().hex[:10]}"
