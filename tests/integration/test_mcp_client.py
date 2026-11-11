"""MCP client / tool registry tests: connectivity, discovery, invocation and failure handling."""

from typing import Any

from mcp import types

from common.tracing import TraceContext, reset_trace, set_trace
from copilot.mcp_client import InMemoryMcpConnection, McpConnection, ToolRegistry


class DeadConnection(McpConnection):
    def __init__(self, name: str) -> None:
        self.name = name

    async def list_tools(self) -> list[types.Tool]:
        raise ConnectionError("connection refused")

    async def call_tool(self, tool: str, arguments: dict[str, Any], meta: dict[str, Any], timeout_s: float) -> types.CallToolResult:
        raise ConnectionError("connection refused")


async def test_discovery_across_servers(registry):
    assert registry.server_status["alarm-management"]["status"] == "ok"
    assert registry.server_status["ticketing"]["tool_count"] == 5
    assert registry.tools["create_ticket"].server == "ticketing"
    assert registry.tools["search_assets"].read_only


async def test_invocation_returns_structured_record_and_propagates_trace(registry):
    token = set_trace(TraceContext(trace_id="trace-client-1"))
    try:
        rec = await registry.call("search_assets", {"query": "compressor", "site": None}, purpose="test")
    finally:
        reset_trace(token)
    assert rec.ok and rec.server == "alarm-management"
    assert "site" not in rec.arguments  # None values are dropped before validation
    assert rec.output["trace_id"] == "trace-client-1"
    t = rec.as_trace()
    assert t["api_status_codes"] == [200] and t["retry_count"] == 0 and t["purpose"] == "test"


async def test_client_side_schema_validation_blocks_bad_args(registry):
    rec = await registry.call("get_alarm", {"alarm_id": "123"})
    assert rec.status == "invalid_arguments" and "does not match" in rec.error["message"]
    rec = await registry.call("search_assets", {"query": "x", "unexpected": 1})
    assert rec.status in ("invalid_arguments", "ok")  # additionalProperties is server policy


async def test_missing_tool_is_unavailable_not_exception(registry):
    rec = await registry.call("delete_everything", {})
    assert rec.status == "unavailable" and rec.error["code"] == "tool_not_found"


async def test_tool_error_is_parsed(registry):
    rec = await registry.call("get_alarm", {"alarm_id": "ALM-999999"})
    assert rec.status == "error" and rec.error["code"] == "not_found"


async def test_unreachable_server_is_recorded_and_other_server_still_works(alarm_mcp):
    reg = ToolRegistry([InMemoryMcpConnection("alarm-management", alarm_mcp), DeadConnection("ticketing")])
    await reg.discover()
    assert reg.server_status["ticketing"]["status"] == "unreachable"
    assert reg.has("search_assets") and not reg.has("search_similar_tickets")
    rec = await reg.call("search_similar_tickets", {"query": "x"})
    assert rec.status == "unavailable"
    assert (await reg.call("search_assets", {"query": "pump"})).ok


async def test_server_dying_after_discovery_gives_unavailable(registry):
    registry.connections["ticketing"] = DeadConnection("ticketing")
    rec = await registry.call("search_similar_tickets", {"query": "surge"})
    assert rec.status == "unavailable" and rec.error["code"] == "server_unreachable"
