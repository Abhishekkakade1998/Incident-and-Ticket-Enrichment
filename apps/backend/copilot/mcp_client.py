"""MCP client integration: multi-server tool registry with discovery and schema-aware invocation.

* ``McpConnection`` abstracts one MCP server. ``HttpMcpConnection`` speaks Streamable HTTP
  (production); ``InMemoryMcpConnection`` wires a FastMCP instance in-process (tests).
* ``ToolRegistry`` discovers tools from every configured server, validates arguments
  against each tool's JSON Schema *before* sending, and turns every outcome (success,
  invalid arguments, tool error, missing tool, unreachable server, timeout) into a
  ``ToolCallRecord`` instead of an exception, so orchestration can degrade gracefully.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import jsonschema
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

from common.logging import log_event
from common.tracing import current_trace

log = logging.getLogger("copilot.mcp")

CallStatus = Literal["ok", "error", "invalid_arguments", "unavailable", "timeout"]


@dataclass(frozen=True)
class ToolSpec:
    server: str
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    read_only: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "read_only": self.read_only,
        }


@dataclass
class ToolCallRecord:
    id: str
    server: str
    tool: str
    arguments: dict[str, Any]
    status: CallStatus
    started_at: str
    duration_ms: float
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    purpose: str = ""
    depends_on: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def source_calls(self) -> list[dict[str, Any]]:
        return list((self.output or {}).get("source_calls", []))

    def as_trace(self) -> dict[str, Any]:
        out = self.output or {}
        return {
            "id": self.id,
            "kind": "mcp",
            "server": self.server,
            "tool": self.tool,
            "purpose": self.purpose,
            "arguments": self.arguments,
            "status": self.status,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
            "depends_on": self.depends_on,
            "trace_id": out.get("trace_id"),
            "source_calls": self.source_calls,
            "retry_count": sum(int(c.get("retry_count", 0)) for c in self.source_calls),
            "api_status_codes": [c.get("status_code") for c in self.source_calls],
            "output_preview": _preview(out),
        }


def _preview(out: dict[str, Any]) -> dict[str, Any]:
    """Compact view of tool output for the GUI (lists truncated)."""
    prev: dict[str, Any] = {}
    for k, v in out.items():
        if k in ("source_calls", "trace_id"):
            continue
        if isinstance(v, list):
            prev[k] = {"count": len(v), "first": v[:2]}
        else:
            prev[k] = v
    return prev


class McpConnection(ABC):
    name: str

    @abstractmethod
    async def list_tools(self) -> list[types.Tool]: ...

    @abstractmethod
    async def call_tool(self, tool: str, arguments: dict[str, Any], meta: dict[str, Any], timeout_s: float) -> types.CallToolResult: ...


class HttpMcpConnection(McpConnection):
    """Streamable HTTP connection. One short-lived session per operation: the servers are
    stateless, so this keeps the client free of long-lived session state and makes
    trace headers per-request."""

    def __init__(self, name: str, url: str, token: str | None, connect_timeout_s: float = 5.0) -> None:
        self.name = name
        self.url = url
        self._token = token
        self.connect_timeout_s = connect_timeout_s

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        trace = current_trace()
        if trace:
            headers.update(trace.to_headers())
        return headers

    @asynccontextmanager
    async def _session(self, read_timeout_s: float) -> AsyncIterator[ClientSession]:
        async with (
            streamablehttp_client(self.url, headers=self._headers(), timeout=self.connect_timeout_s, sse_read_timeout=read_timeout_s) as (read, write, _),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=read_timeout_s)) as session,
        ):
            await session.initialize()
            yield session

    async def list_tools(self) -> list[types.Tool]:
        async with self._session(15) as s:
            return (await s.list_tools()).tools

    async def call_tool(self, tool: str, arguments: dict[str, Any], meta: dict[str, Any], timeout_s: float) -> types.CallToolResult:
        async with self._session(timeout_s) as s:
            return await s.call_tool(tool, arguments, read_timeout_seconds=timedelta(seconds=timeout_s), meta=meta)


class InMemoryMcpConnection(McpConnection):
    """In-process connection to a FastMCP server (used by tests)."""

    def __init__(self, name: str, server: Any) -> None:
        self.name = name
        self.server = server

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(self.server._mcp_server) as s:
            yield s

    async def list_tools(self) -> list[types.Tool]:
        async with self._session() as s:
            return (await s.list_tools()).tools

    async def call_tool(self, tool: str, arguments: dict[str, Any], meta: dict[str, Any], timeout_s: float) -> types.CallToolResult:
        async with self._session() as s:
            return await s.call_tool(tool, arguments, read_timeout_seconds=timedelta(seconds=timeout_s), meta=meta)


class ToolRegistry:
    def __init__(self, connections: list[McpConnection], default_timeout_s: float = 30.0) -> None:
        self.connections = {c.name: c for c in connections}
        self.tools: dict[str, ToolSpec] = {}
        self.server_status: dict[str, dict[str, Any]] = {}
        self.default_timeout_s = default_timeout_s
        self.discovered_at: str | None = None
        self._lock = asyncio.Lock()

    async def discover(self) -> dict[str, ToolSpec]:
        """(Re)discover tools from every server. Unreachable servers are recorded, not fatal."""
        async with self._lock:
            tools: dict[str, ToolSpec] = {}

            async def one(conn: McpConnection) -> None:
                started = time.perf_counter()
                try:
                    listed = await asyncio.wait_for(conn.list_tools(), timeout=15)
                except Exception as exc:  # noqa: BLE001 - recorded as server status
                    self.server_status[conn.name] = {"status": "unreachable", "error": f"{type(exc).__name__}: {exc}"[:300], "tool_count": 0}
                    log_event(log, "mcp.discovery.failed", level=logging.WARNING, mcp_server=conn.name, error=type(exc).__name__)
                    return
                for t in listed:
                    ann = t.annotations
                    tools[t.name] = ToolSpec(
                        server=conn.name,
                        name=t.name,
                        title=t.title,
                        description=t.description or "",
                        input_schema=t.inputSchema,
                        output_schema=t.outputSchema,
                        read_only=bool(ann and ann.readOnlyHint),
                    )
                self.server_status[conn.name] = {
                    "status": "ok",
                    "tool_count": len(listed),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                }
                log_event(log, "mcp.discovery.ok", mcp_server=conn.name, tool_count=len(listed))

            await asyncio.gather(*(one(c) for c in self.connections.values()))
            self.tools = tools
            self.discovered_at = datetime.now(UTC).isoformat(timespec="seconds")
            return tools

    def has(self, tool: str) -> bool:
        return tool in self.tools

    def validate(self, tool: str, arguments: dict[str, Any]) -> list[str]:
        spec = self.tools[tool]
        validator = jsonschema.Draft202012Validator(spec.input_schema)
        return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in validator.iter_errors(arguments)]

    async def call(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        purpose: str = "",
        depends_on: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> ToolCallRecord:
        started_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        t0 = time.perf_counter()
        call_id = f"T-{uuid.uuid4().hex[:8]}"
        args = {k: v for k, v in arguments.items() if v is not None}

        def record(status: CallStatus, *, output: dict[str, Any] | None = None, error: dict[str, Any] | None = None, server: str = "?") -> ToolCallRecord:
            rec = ToolCallRecord(
                id=call_id,
                server=server,
                tool=tool,
                arguments=args,
                status=status,
                started_at=started_at,
                duration_ms=(time.perf_counter() - t0) * 1000,
                output=output,
                error=error,
                purpose=purpose,
                depends_on=depends_on or [],
            )
            log_event(
                log,
                "mcp.client.call",
                level=logging.INFO if status == "ok" else logging.WARNING,
                mcp_server=server,
                mcp_tool=tool,
                outcome=status,
                tool_duration_ms=round(rec.duration_ms, 1),
                retry_count=rec.as_trace()["retry_count"],
                error_code=(error or {}).get("code"),
            )
            return rec

        if tool not in self.tools:
            await self._maybe_rediscover()
        spec = self.tools.get(tool)
        if spec is None:
            return record("unavailable", error={"code": "tool_not_found", "message": f"No MCP server currently exposes tool '{tool}'"})
        problems = self.validate(tool, args)
        if problems:
            return record("invalid_arguments", server=spec.server, error={"code": "invalid_arguments", "message": "; ".join(problems)[:500]})
        conn = self.connections[spec.server]
        trace = current_trace()
        meta = trace.as_meta() if trace else {}
        timeout = timeout_s or self.default_timeout_s
        try:
            result = await asyncio.wait_for(conn.call_tool(tool, args, meta, timeout), timeout=timeout + 2)
        except TimeoutError:
            return record("timeout", server=spec.server, error={"code": "timeout", "message": f"MCP call exceeded {timeout:.0f}s", "retryable": True})
        except (httpx.HTTPError, OSError, ConnectionError) as exc:
            return record(
                "unavailable",
                server=spec.server,
                error={"code": "server_unreachable", "message": f"MCP server '{spec.server}' unreachable: {type(exc).__name__}"},
            )
        except Exception as exc:  # noqa: BLE001 - e.g. ExceptionGroup from anyio, McpError
            msg = _unwrap(exc)
            code = "server_unreachable" if "connect" in msg.lower() else "mcp_protocol_error"
            return record("unavailable" if code == "server_unreachable" else "error", server=spec.server, error={"code": code, "message": msg[:300]})
        if result.isError:
            return record("error", server=spec.server, error=_parse_tool_error(result))
        output = result.structuredContent
        if output is None:
            output = _json_from_content(result)
        return record("ok", server=spec.server, output=output)

    async def _maybe_rediscover(self) -> None:
        """A tool might belong to a server that was down at startup; retry discovery once."""
        if any(s.get("status") != "ok" for s in self.server_status.values()) or not self.tools:
            await self.discover()


def _unwrap(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _parse_tool_error(result: types.CallToolResult) -> dict[str, Any]:
    text = " ".join(c.text for c in result.content if isinstance(c, types.TextContent))
    start = text.find("{")
    if start != -1:
        try:
            data = json.loads(text[start:])
            if isinstance(data, dict) and "code" in data:
                return data
        except json.JSONDecodeError:
            pass
    if "validation error" in text.lower():
        return {"code": "invalid_arguments", "message": text[:400]}
    return {"code": "tool_error", "message": text[:400]}


def _json_from_content(result: types.CallToolResult) -> dict[str, Any]:
    for c in result.content:
        if isinstance(c, types.TextContent):
            try:
                data = json.loads(c.text)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                return {"text": c.text}
    return {}
