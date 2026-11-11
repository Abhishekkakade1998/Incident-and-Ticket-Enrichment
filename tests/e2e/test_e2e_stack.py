"""End-to-end: every service on a real TCP port, real Streamable-HTTP MCP, real HTTP APIs.

GUI-equivalent request -> copilot backend -> MCP client -> (HTTP) MCP servers -> (HTTP)
Alarm API simulator / ticketing mock, plus RAG retrieval, producing a grounded, cited answer.
"""

import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn

from alarm_mcp.server import AlarmMcpSettings
from alarm_mcp.server import build_server as build_alarm
from common.mcp_support import BearerAuthMiddleware
from copilot.api import create_app
from copilot.config import Settings
from simulators.alarm_api_sim.app import create_app as alarm_sim
from simulators.ticketing_api_sim.app import create_app as ticket_sim
from ticketing_mcp.server import TicketingMcpSettings
from ticketing_mcp.server import build_server as build_ticketing

pytestmark = pytest.mark.e2e
ANCHOR = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ServerThread(threading.Thread):
    def __init__(self, app, port: int) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


def wait_ready(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} not ready")


@pytest.fixture(scope="module")
def stack(index_path, tmp_path_factory) -> Iterator[dict[str, str]]:
    ports = {k: free_port() for k in ("alarm_api", "ticket_api", "alarm_mcp", "ticket_mcp", "backend")}
    base = {k: f"http://127.0.0.1:{p}" for k, p in ports.items()}
    a_set = AlarmMcpSettings(alarm_api_base_url=base["alarm_api"], alarm_api_token="e2e-alarm-token")
    t_set = TicketingMcpSettings(ticketing_api_url=base["ticket_api"], ticketing_api_token="e2e-ticket-token", approval_signing_key="e2e-approval")
    settings = Settings(
        alarm_mcp_url=f"{base['alarm_mcp']}/mcp",
        alarm_mcp_token="e2e-mcp-a",
        ticketing_mcp_url=f"{base['ticket_mcp']}/mcp",
        ticketing_mcp_token="e2e-mcp-t",
        approval_signing_key="e2e-approval",
        rag_index_path=index_path,
        audit_db_path=tmp_path_factory.mktemp("audit") / "a.db",
    )
    apps = {
        "alarm_api": alarm_sim(token="e2e-alarm-token", anchor=ANCHOR),
        "ticket_api": ticket_sim(token="e2e-ticket-token"),
        "alarm_mcp": BearerAuthMiddleware(build_alarm(a_set).streamable_http_app(), "e2e-mcp-a"),
        "ticket_mcp": BearerAuthMiddleware(build_ticketing(t_set).streamable_http_app(), "e2e-mcp-t"),
    }
    threads = []
    for name, app in apps.items():
        t = ServerThread(app, ports[name])
        t.start()
        threads.append(t)
        wait_ready(f"{base[name]}/health")
    backend = ServerThread(create_app(settings), ports["backend"])
    backend.start()
    threads.append(backend)
    wait_ready(f"{base['backend']}/health")
    yield base
    for t in threads:
        t.stop()


def test_stack_ready_and_tools_discovered_over_http(stack):
    r = httpx.get(f"{stack['backend']}/ready", timeout=30)
    assert r.status_code == 200, r.text
    tools = httpx.get(f"{stack['backend']}/api/tools", timeout=30).json()
    assert len(tools["tools"]) == 19


def test_mcp_server_rejects_missing_token(stack):
    r = httpx.post(f"{stack['alarm_mcp']}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_e2e_acceptance_scenario_mcp_plus_rag(stack):
    q = (
        "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
        "contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source evidence."
    )
    r = httpx.post(f"{stack['backend']}/api/chat", json={"message": q}, headers={"trace_id": "trace-e2e-1"}, timeout=90)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["trace_id"] == "trace-e2e-1"
    mcp = [s for s in body["trace"]["steps"] if s["kind"] == "mcp"]
    assert mcp[0]["tool"] == "search_assets" and all(s["status"] == "ok" for s in mcp)
    assert all(s["trace_id"] == "trace-e2e-1" for s in mcp)  # propagated through MCP into tools
    assert all(c["status_code"] == 200 for s in mcp for c in s["source_calls"])  # real API calls happened
    assert any(s["kind"] == "rag" and s["hits"] for s in body["trace"]["steps"])
    assert "SOP-PUMP-004" in {c["doc_id"] for c in body["citations"]}
    md = body["answer_markdown"]
    assert "Boiler Feed Pump 101" in md and "[D" in md and "[T" in md
    assert body["sections"]["likely_causes"] and body["sections"]["recommended_actions"]


def test_e2e_incident_draft_and_approved_write(stack):
    b = stack["backend"]
    chat = httpx.post(f"{b}/api/chat", json={"message": "Prepare an incident for the highest-priority active alarm in EastRefinery."}, timeout=90).json()
    draft = chat["ticket_draft"]
    assert draft["fields"]["asset_id"] == "AST-CMP-501"
    res = httpx.post(
        f"{b}/api/drafts/{draft['draft_id']}/approve", json={"approved_by": "e2e-operator", "confirm": True, "fields": draft["fields"]}, timeout=60
    )
    assert res.status_code == 200, res.text
    ticket_id = res.json()["ticket_id"]
    got = httpx.get(f"{stack['ticket_api']}/tickets/{ticket_id}", headers={"Authorization": "Bearer e2e-ticket-token"}).json()
    assert got["approval"]["approved_by"] == "e2e-operator" and got["title"] == draft["fields"]["title"]


def test_e2e_degraded_when_source_api_fails(stack):
    httpx.post(
        f"{stack['alarm_api']}/_admin/faults",
        json={"path_pattern": "^/alarms$", "mode": "error", "status_code": 503, "remaining": 50},
        headers={"Authorization": "Bearer e2e-alarm-token"},
    )
    try:
        body = httpx.post(
            f"{stack['backend']}/api/chat", json={"message": "Prepare an incident for the highest-priority active alarm in EastRefinery."}, timeout=90
        ).json()
    finally:
        httpx.delete(f"{stack['alarm_api']}/_admin/faults", headers={"Authorization": "Bearer e2e-alarm-token"})
    assert body["status"] == "error" and body["ticket_draft"] is None
    step = next(s for s in body["trace"]["steps"] if s.get("tool") == "list_alarms")
    assert step["error"]["code"] == "upstream_unavailable" and step["error"]["attempts"] == 3
