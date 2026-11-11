"""MCP server tests through the real MCP protocol (in-memory transport).

Covers tool registration/discovery, schema validation, auth to the source system,
pagination, timeouts, retries, API error mapping, trace propagation and write approval.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

from alarm_mcp.server import AlarmMcpSettings, build_server
from common import approval
from common.mcp_support import BearerAuthMiddleware
from connectors.alarm_api import AlarmApiClient
from tests.conftest import APPROVAL_KEY, FAST_RETRY

ALARM_TOOLS = {
    "search_assets",
    "get_asset_metadata",
    "list_alarms",
    "get_alarm",
    "get_alarm_summary",
    "get_alarm_trends",
    "correlate_alarms",
    "analyze_alarm_floods",
    "get_rationalization_candidates",
    "score_alarm_priority",
    "rank_alarms_by_priority",
    "get_operator_recommendations",
    "compute_alarm_kpi",
    "list_kpi_definitions",
}


@asynccontextmanager
async def session(server):
    async with create_connected_server_and_client_session(server._mcp_server) as s:
        yield s


def err(result) -> dict:
    text = result.content[0].text
    return json.loads(text[text.index("{") :]) if "{" in text else {"raw": text}


async def test_tool_discovery_and_typed_schemas(alarm_mcp):
    async with session(alarm_mcp) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == ALARM_TOOLS
    for t in tools.values():
        assert t.description and t.inputSchema["type"] == "object"
        assert t.outputSchema is not None, f"{t.name} lacks an output schema"
        assert t.annotations.readOnlyHint is True
    assert tools["get_alarm"].inputSchema["properties"]["alarm_id"]["pattern"] == r"^ALM-\d{6}$"
    assert "alarm_ids" in tools["rank_alarms_by_priority"].inputSchema["required"]


async def test_ticketing_tools_mark_writes(ticket_mcp):
    async with session(ticket_mcp) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == {"search_similar_tickets", "list_tickets", "get_ticket", "create_ticket", "add_ticket_comment"}
    assert tools["create_ticket"].annotations.readOnlyHint is False
    assert "approval_token" in tools["create_ticket"].inputSchema["required"]


async def test_invalid_arguments_rejected_by_schema(alarm_mcp):
    async with session(alarm_mcp) as s:
        r = await s.call_tool("get_alarm", {"alarm_id": "not-an-id"})
        assert r.isError and "pattern" in r.content[0].text
        r = await s.call_tool("get_alarm_summary", {"lookback_days": 30})  # no scope
        assert r.isError and err(r)["code"] == "invalid_arguments"
        r = await s.call_tool("get_alarm_trends", {"asset_ids": ["AST-BFP-101"], "start_time": "yesterday"})
        assert r.isError and err(r)["code"] == "invalid_arguments"


async def test_successful_call_returns_structured_output_with_source_calls(alarm_mcp):
    async with session(alarm_mcp) as s:
        r = await s.call_tool("search_assets", {"query": "Boiler Feed Pump 101"}, meta={"trace_id": "trace-test-1"})
    assert not r.isError
    out = r.structuredContent
    assert out["results"][0]["asset_id"] == "AST-BFP-101"
    assert out["trace_id"] == "trace-test-1"
    assert out["source_calls"][0]["status_code"] == 200


async def test_trace_and_auth_propagate_to_source_system(alarm_sim_app):
    seen = []

    async def spy(request):
        seen.append(dict(request.headers))
        return await transport.handle_async_request(request)

    transport = httpx.ASGITransport(app=alarm_sim_app)
    client = AlarmApiClient("http://sim", "demo-token", transport=httpx.MockTransport(spy), retry=FAST_RETRY)
    server = build_server(AlarmMcpSettings(), client=client)
    async with session(server) as s:
        r = await s.call_tool("get_alarm", {"alarm_id": "ALM-000001"}, meta={"trace_id": "trace-prop", "x-conversation-id": "conv-9"})
    assert not r.isError
    assert seen[0]["authorization"] == "Bearer demo-token"
    assert seen[0]["trace_id"] == "trace-prop"
    assert seen[0]["x-conversation-id"] == "conv-9"
    assert seen[0]["x-client-id"] == "alarm-management"


async def test_pagination_follows_pages_until_max(alarm_mcp):
    async with session(alarm_mcp) as s:
        r = await s.call_tool("list_alarms", {"asset_id": "AST-BFP-101", "page_size": 50, "fetch_all_pages": True, "max_pages": 3})
        one = await s.call_tool("list_alarms", {"asset_id": "AST-BFP-101", "page_size": 50})
    out = r.structuredContent
    assert out["pages_fetched"] == 3 and len(out["alarms"]) == 150 and out["truncated"] is True
    assert len(out["source_calls"]) == 3
    assert one.structuredContent["pages_fetched"] == 1


async def test_not_found_is_mapped(alarm_mcp):
    async with session(alarm_mcp) as s:
        r = await s.call_tool("get_asset_metadata", {"asset_id": "AST-XXX-999"})
    e = err(r)
    assert r.isError and e["code"] == "not_found" and e["status_code"] == 404 and e["retryable"] is False


async def test_retry_on_transient_source_failure(alarm_mcp, alarm_sim_app):
    admin = TestClient(alarm_sim_app)
    h = {"Authorization": "Bearer demo-token"}
    admin.post("/_admin/faults", json={"path_pattern": "^/alarms/ALM-000002$", "mode": "error", "status_code": 503, "remaining": 2}, headers=h)
    async with session(alarm_mcp) as s:
        r = await s.call_tool("get_alarm", {"alarm_id": "ALM-000002"})
    assert not r.isError
    call = r.structuredContent["source_calls"][0]
    assert call["attempts"] == 3 and call["retry_count"] == 2


async def test_retries_exhausted_maps_to_upstream_unavailable(alarm_mcp, alarm_sim_app):
    admin = TestClient(alarm_sim_app)
    h = {"Authorization": "Bearer demo-token"}
    admin.post("/_admin/faults", json={"path_pattern": "^/alarms/ALM-000003$", "mode": "error", "status_code": 503, "remaining": 5}, headers=h)
    async with session(alarm_mcp) as s:
        r = await s.call_tool("get_alarm", {"alarm_id": "ALM-000003"})
    admin.delete("/_admin/faults", headers=h)
    e = err(r)
    assert e["code"] == "upstream_unavailable" and e["retryable"] is True and e["attempts"] == 3


async def test_tool_timeout(alarm_sim_app):
    admin = TestClient(alarm_sim_app)
    h = {"Authorization": "Bearer demo-token"}
    admin.post("/_admin/faults", json={"path_pattern": "^/analytics", "mode": "delay", "delay_ms": 1500, "remaining": 1}, headers=h)
    client = AlarmApiClient("http://sim", "demo-token", transport=httpx.ASGITransport(app=alarm_sim_app), retry=FAST_RETRY)
    server = build_server(AlarmMcpSettings(tool_timeout_s=0.3), client=client)
    async with session(server) as s:
        r = await s.call_tool("list_kpi_definitions", {})
    admin.delete("/_admin/faults", headers=h)
    assert r.isError and err(r)["code"] == "timeout"


async def test_source_auth_failure_is_mapped_without_leaking_token(alarm_sim_app):
    client = AlarmApiClient("http://sim", "wrong-secret", transport=httpx.ASGITransport(app=alarm_sim_app), retry=FAST_RETRY)
    server = build_server(AlarmMcpSettings(), client=client)
    async with session(server) as s:
        r = await s.call_tool("get_alarm", {"alarm_id": "ALM-000001"})
    assert err(r)["code"] == "auth_failed"
    assert "wrong-secret" not in r.content[0].text


async def test_rank_alarms_reports_partial_failures(alarm_mcp):
    async with session(alarm_mcp) as s:
        r = await s.call_tool("rank_alarms_by_priority", {"alarm_ids": ["ALM-000001", "ALM-999999", "ALM-000010"]})
    out = r.structuredContent
    assert out["failures"] == 1
    scores = [x["priority_score"] for x in out["ranked"] if x["priority_score"] is not None]
    assert scores == sorted(scores, reverse=True)
    assert out["ranked"][-1]["error"] == "not_found"


async def test_kpi_tool_chains_generate_and_execute(alarm_mcp):
    async with session(alarm_mcp) as s:
        r = await s.call_tool("compute_alarm_kpi", {"calculation_type": "critical_alarm_density", "unit": "Unit 3"})
    out = r.structuredContent
    assert [c["path"] for c in out["source_calls"]] == ["/calculation-code/generate", "/calculation-code/execute"]
    assert out["calculation_id"].startswith("CALC-")


# ------------------------------------------------------------------ ticketing writes

DRAFT = {
    "title": "[P2] High Vibration on Boiler Feed Pump 101",
    "description": "Pump vibration recurring after low suction pressure.",
    "priority": "P2",
    "asset_id": "AST-BFP-101",
    "asset_name": "Boiler Feed Pump 101",
}


def full_payload(d):
    from ticketing_mcp.server import TicketDraft

    return TicketDraft(**d).model_dump(mode="json")


async def test_create_ticket_requires_valid_approval(ticket_mcp):
    async with session(ticket_mcp) as s:
        r = await s.call_tool(
            "create_ticket", {"draft": DRAFT, "approval_token": "v1.0.forged-signature", "approved_by": "x", "idempotency_key": "DRAFT-aaaaaaaa"}
        )
        assert r.isError and err(r)["code"] == "approval_required"
        token = approval.mint(full_payload(DRAFT), APPROVAL_KEY)
        tampered = {**DRAFT, "priority": "P4"}
        r = await s.call_tool("create_ticket", {"draft": tampered, "approval_token": token, "approved_by": "x", "idempotency_key": "DRAFT-aaaaaaaa"})
        assert r.isError and "does not match" in err(r)["message"]


async def test_create_ticket_with_approval_is_idempotent(ticket_mcp):
    token = approval.mint(full_payload(DRAFT), APPROVAL_KEY)
    args = {"draft": DRAFT, "approval_token": token, "approved_by": "jane", "idempotency_key": "DRAFT-bbbbbbbb"}
    async with session(ticket_mcp) as s:
        first = await s.call_tool("create_ticket", args)
        second = await s.call_tool("create_ticket", args)
        got = await s.call_tool("get_ticket", {"ticket_id": first.structuredContent["ticket"]["ticket_id"]})
    assert not first.isError and first.structuredContent["idempotent_replay"] is False
    assert second.structuredContent["idempotent_replay"] is True
    assert second.structuredContent["ticket"]["ticket_id"] == first.structuredContent["ticket"]["ticket_id"]
    assert got.structuredContent["ticket"]["status"] == "open"


async def test_similar_ticket_search_ranks_same_asset_first(ticket_mcp):
    async with session(ticket_mcp) as s:
        r = await s.call_tool(
            "search_similar_tickets", {"query": "surge anti-surge valve compressor", "asset_id": "AST-CMP-501", "alarm_name": "Surge Detected"}
        )
    res = r.structuredContent["results"]
    assert res[0]["asset_id"] == "AST-CMP-501" and "same_asset" in res[0]["match_reasons"]


async def test_list_tickets_filters_open_on_assets(ticket_mcp):
    async with session(ticket_mcp) as s:
        r = await s.call_tool("list_tickets", {"asset_ids": ["AST-CMP-501", "AST-MTR-501"], "statuses": ["open", "in_progress"]})
    tickets = r.structuredContent["tickets"]
    assert {t["ticket_id"] for t in tickets} == {"INC-1011", "INC-1012"}


def test_mcp_http_endpoint_requires_bearer_token(alarm_mcp):
    app = BearerAuthMiddleware(alarm_mcp.streamable_http_app(), "mcp-secret")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/mcp", json={}).status_code == 401
        assert c.post("/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
        ok = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer mcp-secret", "Accept": "application/json, text/event-stream"},
        )
        assert ok.status_code != 401


@pytest.mark.parametrize("tool", sorted(ALARM_TOOLS))
async def test_every_tool_has_meaningful_description(alarm_mcp, tool):
    async with session(alarm_mcp) as s:
        t = next(t for t in (await s.list_tools()).tools if t.name == tool)
    assert len(t.description) > 30
