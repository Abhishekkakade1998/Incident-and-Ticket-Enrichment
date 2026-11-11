"""Generate docs/mcp-tool-catalog.md from the live MCP servers.

Schemas come from MCP tool discovery; example responses come from real calls against the
simulators (in-process), so the catalog can never drift from the implementation.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "apps/backend"), str(ROOT / "mcp-servers/alarm-management"), str(ROOT / "mcp-servers/ticketing")]

import httpx  # noqa: E402

from common import approval  # noqa: E402
from tests import conftest as c  # noqa: E402

SOURCE_OP = {
    "search_assets": "GET /assets/search",
    "get_asset_metadata": "GET /assets/{asset_id}/metadata",
    "list_alarms": "GET /alarms (repeated per page when fetch_all_pages)",
    "get_alarm": "GET /alarms/{alarm_id}",
    "get_alarm_summary": "POST /alarms/summary",
    "get_alarm_trends": "POST /alarms/trends",
    "correlate_alarms": "POST /alarms/correlation",
    "analyze_alarm_floods": "POST /alarms/flood-analysis",
    "get_rationalization_candidates": "POST /alarms/rationalization-candidates",
    "score_alarm_priority": "POST /alarms/priority-score",
    "rank_alarms_by_priority": "POST /alarms/priority-score x N (concurrent)",
    "get_operator_recommendations": "POST /recommendations/operator-actions",
    "compute_alarm_kpi": "POST /calculation-code/generate -> POST /calculation-code/execute",
    "list_kpi_definitions": "GET /analytics/kpi-definitions",
    "search_similar_tickets": "POST /tickets/search-similar",
    "list_tickets": "GET /tickets (repeated per page)",
    "get_ticket": "GET /tickets/{ticket_id}",
    "create_ticket": "POST /tickets (Idempotency-Key header)",
    "add_ticket_comment": "POST /tickets/{ticket_id}/comments",
}

DRAFT = {
    "title": "[P2] High Vibration on Boiler Feed Pump 101",
    "description": "Recurring vibration after low suction pressure.",
    "priority": "P2",
    "asset_id": "AST-BFP-101",
}

EXAMPLES: dict[str, dict] = {
    "search_assets": {"query": "Boiler Feed Pump 101", "limit": 3},
    "get_asset_metadata": {"asset_id": "AST-BFP-101"},
    "list_alarms": {"site": "EastRefinery", "status": ["active"], "page_size": 2},
    "get_alarm": {"alarm_id": "ALM-000042"},
    "get_alarm_summary": {"asset_ids": ["AST-BFP-101"], "lookback_days": 90, "severity": ["high", "critical"], "group_by": ["alarm_name"]},
    "get_alarm_trends": {"asset_ids": ["AST-BFP-101"], "lookback_days": 28, "bucket": "weekly"},
    "correlate_alarms": {"asset_ids": ["AST-CMP-501"], "lookback_days": 90, "min_support": 3},
    "analyze_alarm_floods": {"unit": "Unit 2", "lookback_days": 90},
    "get_rationalization_candidates": {"unit": "Unit 4", "lookback_days": 90, "recurrence_threshold": 8},
    "score_alarm_priority": {"alarm_id": "ALM-000042"},
    "rank_alarms_by_priority": {"alarm_ids": ["ALM-000042", "ALM-000043", "ALM-999999"]},
    "get_operator_recommendations": {"alarm_id": "ALM-000042"},
    "compute_alarm_kpi": {"calculation_type": "operator_response_efficiency", "site": "SouthPlant", "lookback_days": 60},
    "list_kpi_definitions": {},
    "search_similar_tickets": {"query": "surge anti-surge valve", "asset_id": "AST-CMP-501", "alarm_name": "Surge Detected", "limit": 2},
    "list_tickets": {"asset_ids": ["AST-CMP-501", "AST-MTR-501"], "statuses": ["open", "in_progress"]},
    "get_ticket": {"ticket_id": "INC-1001"},
    "create_ticket": {
        "draft": DRAFT,
        "approval_token": "<minted by backend after human approval>",
        "approved_by": "jane.operator",
        "idempotency_key": "DRAFT-1a2b3c4d5e",
    },
    "add_ticket_comment": {"ticket_id": "INC-1001", "comment": "Strainer cleaned again.", "author": "jane.operator", "approval_token": "<minted>"},
}

ERRORS = {
    "common": "Structured tool error (`isError: true`, JSON text): `{code, message, status_code, retryable, attempts, details}`. Codes: "
    "`invalid_arguments` (schema/semantic validation), `auth_failed` (401/403 from source), `not_found` (404), `invalid_request` (400/409/422), "
    "`rate_limited` (429, retried), `upstream_unavailable` (5xx/connection, retried), `timeout` (per-call or per-tool), `invalid_upstream_response` "
    "(source payload failed output validation).",
    "create_ticket": "Additionally `approval_required` when the approval token is missing, malformed, expired, signed with another key, or minted for a different payload.",
    "add_ticket_comment": "Additionally `approval_required` (token must be bound to `{ticket_id, comment}`).",
    "rank_alarms_by_priority": "Per-alarm failures are reported inside `ranked[].error` (partial success); the tool only errors if every alarm fails.",
}


def trim(obj, depth=0):
    if isinstance(obj, list):
        out = [trim(x, depth + 1) for x in obj[:2]]
        if len(obj) > 2:
            out.append(f"... {len(obj) - 2} more")
        return out
    if isinstance(obj, dict):
        return {k: trim(v, depth + 1) for k, v in list(obj.items())[:18]}
    if isinstance(obj, str) and len(obj) > 160:
        return obj[:157] + "..."
    return obj


async def main() -> None:
    sim = c.create_alarm_sim(token="demo-token", anchor=c.ANCHOR)
    ts = c.create_ticket_sim(token="ticket-demo-token")
    ac = c.AlarmApiClient("http://alarm-api", "demo-token", transport=httpx.ASGITransport(app=sim))
    tc = c.TicketingClient("http://ticketing-api", "ticket-demo-token", transport=httpx.ASGITransport(app=ts))
    a_srv, t_srv = c.build_alarm_server(client=ac), c.build_ticket_server(c.TicketingMcpSettings(approval_signing_key="k"), client=tc)
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from ticketing_mcp.server import TicketDraft

    lines = [
        "# MCP Tool Catalog",
        "",
        "> Generated by `scripts/gen_tool_catalog.py` from live tool discovery and real calls against the simulators. Do not edit by hand.",
        "",
        "Two candidate-developed MCP servers, both Streamable HTTP (`/mcp`, stateless, JSON responses) and also runnable over stdio:",
        "",
        "| Server | Default URL | Tools | Source system |",
        "|---|---|---|---|",
        "| `alarm-management` | `http://localhost:9001/mcp` | 14 (read-only) | Alarm Management API simulator |",
        "| `ticketing` | `http://localhost:9002/mcp` | 5 (3 read, 2 write) | Mock ticketing API |",
        "",
        "## Behaviour common to every tool",
        "",
        "- **Authentication (client -> MCP server):** `Authorization: Bearer <ALARM_MCP_TOKEN | TICKETING_MCP_TOKEN>` required on `/mcp` (401 otherwise). `/health` is open.",
        "- **Authentication (MCP server -> source):** the server injects `Authorization: Bearer <ALARM_API_TOKEN | TICKETING_API_TOKEN>`. The copilot never holds source tokens; tokens are never logged or returned (errors are redacted).",
        "- **Trace propagation:** `trace_id`, `x-request-id`, `x-client-id`, `x-metadata-tag`, `x-conversation-id` are read from MCP request `_meta` (or HTTP headers), re-sent to the source API, and returned as `trace_id` in every output.",
        "- **Timeouts:** per source HTTP call `SOURCE_TIMEOUT_S` (default 8 s); whole tool `TOOL_TIMEOUT_S` (alarm 25 s, ticketing 20 s) -> `timeout` error.",
        "- **Retries:** connect errors, timeouts, 429, 502, 503, 504 are retried `SOURCE_MAX_RETRIES` times (default 2) with exponential backoff + jitter. Ticket creation is safe to retry thanks to the idempotency key.",
        "- **Output validation:** every response is validated against the tool's output model (published as `outputSchema`); each output carries `source_calls[]` (`method, path, status_code, attempts, retry_count, duration_ms`).",
        f"- **Errors:** {ERRORS['common']}",
        "",
    ]
    for server, name in ((a_srv, "alarm-management"), (t_srv, "ticketing")):
        async with connect(server._mcp_server) as s:
            tools = (await s.list_tools()).tools
            lines += [f"## Server `{name}`", ""]
            for t in tools:
                args = dict(EXAMPLES[t.name])
                call_args = args
                if t.name == "create_ticket":
                    call_args = {**args, "approval_token": approval.mint(TicketDraft(**DRAFT).model_dump(mode="json"), "k")}
                if t.name == "add_ticket_comment":
                    call_args = {**args, "approval_token": approval.mint({"ticket_id": "INC-1001", "comment": args["comment"]}, "k")}
                res = await s.call_tool(t.name, call_args, meta={"trace_id": "trace-catalog-example"})
                response = res.structuredContent if not res.isError else {"isError": True, "text": res.content[0].text}
                write = not (t.annotations and t.annotations.readOnlyHint)
                lines += [
                    f"### `{t.name}`{' (WRITE - approval required)' if write else ''}",
                    "",
                    f"**Purpose:** {t.description}",
                    "",
                    f"- **Underlying source operation:** `{SOURCE_OP[t.name]}`",
                    f"- **Annotations:** readOnly={bool(t.annotations and t.annotations.readOnlyHint)}, idempotent={bool(t.annotations and t.annotations.idempotentHint)}",
                    f"- **Errors:** common codes{'; ' + ERRORS[t.name] if t.name in ERRORS else ''}",
                    "",
                    "<details><summary>Input schema</summary>",
                    "",
                    "```json",
                    json.dumps(t.inputSchema, indent=2),
                    "```",
                    "</details>",
                    "",
                    "<details><summary>Output schema</summary>",
                    "",
                    "```json",
                    json.dumps(t.outputSchema, indent=2),
                    "```",
                    "</details>",
                    "",
                    "**Example invocation** (`tools/call`):",
                    "",
                    "```json",
                    json.dumps({"name": t.name, "arguments": args, "_meta": {"trace_id": "trace-catalog-example"}}, indent=2),
                    "```",
                    "",
                    "**Example response** (structuredContent, lists trimmed):",
                    "",
                    "```json",
                    json.dumps(trim(response), indent=2),
                    "```",
                    "",
                ]
    out = ROOT / "docs" / "mcp-tool-catalog.md"
    out.write_text("\n".join(lines))
    print("wrote", out)


asyncio.run(main())
