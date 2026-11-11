"""Helpers shared by the candidate-developed MCP servers.

* ``run_tool`` wraps every tool body: it restores the caller's trace context from the
  MCP request ``_meta`` (or HTTP headers), enforces a per-tool timeout, maps connector
  errors to structured MCP tool errors, and emits one structured log line per call.
* ``BearerAuthMiddleware`` protects the MCP HTTP endpoint (client -> MCP server boundary).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from common.logging import log_event
from common.tracing import TraceContext, reset_trace, set_trace
from connectors.http import CallStats, SourceError

T = TypeVar("T")
log = logging.getLogger("mcp.tool")


class SourceCall(BaseModel):
    method: str
    path: str
    status_code: int | None = None
    attempts: int = 1
    retry_count: int = 0
    duration_ms: float = 0.0
    retry_reasons: list[str] = []


class CallRecorder:
    """Collects ``CallStats`` from every upstream call made while serving one tool call."""

    def __init__(self) -> None:
        self.calls: list[SourceCall] = []

    def add(self, stats: CallStats) -> None:
        self.calls.append(SourceCall(**stats.as_dict()))

    async def call(self, coro: Awaitable[tuple[Any, CallStats]]) -> Any:
        body, stats = await coro
        self.add(stats)
        return body


def trace_from_ctx(ctx: Context | None, server: str) -> TraceContext:
    data: dict[str, str] = {}
    if ctx is not None:
        try:
            rc = ctx.request_context
        except (ValueError, LookupError):
            rc = None
        if rc is not None:
            request = getattr(rc, "request", None)
            if request is not None and hasattr(request, "headers"):
                data.update({k: v for k, v in request.headers.items() if k.lower() in _TRACE_KEYS})
            meta = getattr(rc, "meta", None)
            if meta is not None:
                extra = meta.model_extra or {}
                for k, v in extra.items():
                    if isinstance(v, str) and k.lower() in _TRACE_KEYS:
                        data[k.lower()] = v
    return TraceContext.from_mapping(data, client_id=f"{server}")


_TRACE_KEYS = {"trace_id", "x-trace-id", "x-request-id", "x-client-id", "x-metadata-tag", "x-conversation-id"}


def tool_error(code: str, message: str, **extra: Any) -> ToolError:
    """Encode a structured error as JSON in the tool error text (parsed by our MCP client)."""
    return ToolError(json.dumps({"code": code, "message": message, **extra}))


async def run_tool(
    server: str,
    tool: str,
    ctx: Context | None,
    body: Callable[[CallRecorder], Awaitable[T]],
    *,
    timeout_s: float,
) -> T:
    trace = trace_from_ctx(ctx, server)
    token = set_trace(trace)
    recorder = CallRecorder()
    started = time.perf_counter()
    outcome = "ok"
    try:
        return await asyncio.wait_for(body(recorder), timeout=timeout_s)
    except TimeoutError as exc:
        outcome = "timeout"
        raise tool_error("timeout", f"Tool '{tool}' exceeded its {timeout_s:.0f}s timeout", retryable=True) from exc
    except SourceError as exc:
        outcome = exc.code
        raise tool_error(exc.code, exc.message, status_code=exc.status_code, retryable=exc.retryable, attempts=exc.attempts, details=exc.details) from exc
    except ValidationError as exc:
        outcome = "invalid_upstream_response"
        raise tool_error("invalid_upstream_response", "Source system returned data that failed output validation", details=str(exc)[:500]) from exc
    except ToolError:
        outcome = "tool_error"
        raise
    finally:
        log_event(
            log,
            "mcp.tool.call",
            level=logging.INFO if outcome == "ok" else logging.WARNING,
            mcp_server=server,
            mcp_tool=tool,
            outcome=outcome,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            upstream_calls=len(recorder.calls),
            retry_count=sum(c.retry_count for c in recorder.calls),
            api_status_codes=[c.status_code for c in recorder.calls],
        )
        reset_trace(token)


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer <MCP_SERVER_TOKEN>`` on the MCP endpoint (not /health)."""

    def __init__(self, app: ASGIApp, token: str | None, open_paths: tuple[str, ...] = ("/health",)) -> None:
        self.app = app
        self.token = token
        self.open_paths = open_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.token or scope.get("path") in self.open_paths:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or not secrets.compare_digest(auth[7:].strip(), self.token):
            response = JSONResponse({"error": {"code": "unauthorized", "message": "Invalid or missing MCP bearer token"}}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
