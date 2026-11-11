"""Request-scoped trace context propagated GUI -> backend -> MCP -> source API.

The Alarm Management API contract (see postman collections) uses three headers:
``trace_id``, ``x-client-id`` and ``x-metadata-tag``. We additionally carry
``x-request-id`` and ``x-conversation-id``. The context lives in a ContextVar so
that it follows asyncio tasks without being threaded through every call.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

TRACE_HEADER = "trace_id"
CLIENT_ID_HEADER = "x-client-id"
METADATA_TAG_HEADER = "x-metadata-tag"
REQUEST_ID_HEADER = "x-request-id"
CONVERSATION_ID_HEADER = "x-conversation-id"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class TraceContext:
    trace_id: str = field(default_factory=lambda: new_id("trace"))
    request_id: str = field(default_factory=lambda: new_id("req"))
    conversation_id: str | None = None
    client_id: str = "copilot"
    metadata_tag: str | None = None

    def to_headers(self) -> dict[str, str]:
        headers = {
            TRACE_HEADER: self.trace_id,
            REQUEST_ID_HEADER: self.request_id,
            CLIENT_ID_HEADER: self.client_id,
        }
        if self.metadata_tag:
            headers[METADATA_TAG_HEADER] = self.metadata_tag
        if self.conversation_id:
            headers[CONVERSATION_ID_HEADER] = self.conversation_id
        return headers

    def as_meta(self) -> dict[str, str]:
        """Shape used inside MCP ``_meta`` / tool arguments."""
        return {k: v for k, v in self.to_headers().items() if v}

    def as_log_fields(self) -> dict[str, str | None]:
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "client_id": self.client_id,
        }

    def child(self, **changes: str | None) -> TraceContext:
        return replace(self, **changes)  # type: ignore[arg-type]

    @classmethod
    def from_mapping(cls, data: dict[str, str] | None, client_id: str = "copilot") -> TraceContext:
        """Build from incoming headers / MCP meta, generating IDs that are absent."""
        data = {k.lower(): v for k, v in (data or {}).items() if isinstance(v, str)}
        return cls(
            trace_id=data.get(TRACE_HEADER) or data.get("x-trace-id") or new_id("trace"),
            request_id=data.get(REQUEST_ID_HEADER) or new_id("req"),
            conversation_id=data.get(CONVERSATION_ID_HEADER),
            client_id=data.get(CLIENT_ID_HEADER) or client_id,
            metadata_tag=data.get(METADATA_TAG_HEADER),
        )


_current: ContextVar[TraceContext | None] = ContextVar("trace_context", default=None)


def current_trace() -> TraceContext | None:
    return _current.get()


def set_trace(ctx: TraceContext | None) -> object:
    return _current.set(ctx)


def reset_trace(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]
