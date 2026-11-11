"""Backend HTTP API tests (FastAPI app with in-memory MCP registry)."""

import pytest
from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.config import Settings
from tests.conftest import APPROVAL_KEY


@pytest.fixture
def client(registry, retriever, audit, tmp_path):
    settings = Settings(approval_signing_key=APPROVAL_KEY, audit_db_path=tmp_path / "a.db", llm_provider="offline")
    app = create_app(settings, registry=registry, retriever=retriever, audit=audit)
    with TestClient(app) as c:
        yield c


def test_health_ready_and_security_headers(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready" and r.json()["rag_index"] is True
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["trace_id"].startswith("trace-")


def test_tool_discovery_endpoint(client):
    body = client.get("/api/tools").json()
    names = {t["name"] for t in body["tools"]}
    assert {"search_assets", "create_ticket"} <= names
    assert body["servers"]["alarm-management"]["status"] == "ok"
    create = next(t for t in body["tools"] if t["name"] == "create_ticket")
    assert create["read_only"] is False and create["input_schema"]["type"] == "object"


def test_chat_propagates_incoming_trace_id(client):
    r = client.post(
        "/api/chat", json={"message": "Prepare an incident for the highest-priority active alarm in EastRefinery."}, headers={"trace_id": "trace-from-gui"}
    )
    body = r.json()
    assert r.status_code == 200 and body["trace_id"] == "trace-from-gui"
    mcp = [s for s in body["trace"]["steps"] if s["kind"] == "mcp"]
    assert all(s["trace_id"] == "trace-from-gui" for s in mcp if s["status"] == "ok")


@pytest.mark.parametrize("payload", [{"message": ""}, {"message": "x" * 4001}, {"message": "hi", "conversation_id": "bad id!"}])
def test_chat_input_validation(client, payload):
    assert client.post("/api/chat", json=payload).status_code == 422


def test_draft_edit_and_approval_flow(client):
    chat = client.post("/api/chat", json={"message": "Prepare an incident for the highest-priority active alarm in EastRefinery."}).json()
    draft = chat["ticket_draft"]
    did = draft["draft_id"]
    fields = {**draft["fields"], "title": "[P1] Edited title for K-501 surge"}
    upd = client.put(f"/api/drafts/{did}", json={"fields": fields})
    assert upd.status_code == 200 and upd.json()["fields"]["title"] == fields["title"]
    no_confirm = client.post(f"/api/drafts/{did}/approve", json={"approved_by": "jane", "confirm": False, "fields": fields})
    assert no_confirm.status_code == 400
    bad_fields = client.post(f"/api/drafts/{did}/approve", json={"approved_by": "jane", "confirm": True, "fields": {**fields, "priority": "P9"}})
    assert bad_fields.status_code == 422
    ok = client.post(f"/api/drafts/{did}/approve", json={"approved_by": "jane", "confirm": True, "fields": fields})
    assert ok.status_code == 200 and ok.json()["ticket"]["title"] == fields["title"]
    assert client.put(f"/api/drafts/{did}", json={"fields": fields}).status_code == 409
    events = [e["action"] for e in client.get(f"/api/audit?conversation_id={chat['conversation_id']}").json()["events"]]
    assert {"chat_request", "mcp_tool_call", "rag_retrieval", "draft_created", "draft_updated", "approval_granted", "ticket_created"} <= set(events)


def test_unknown_draft_404(client):
    assert client.get("/api/drafts/DRAFT-nope").status_code == 404
    assert (
        client.post(
            "/api/drafts/DRAFT-nope/approve",
            json={"approved_by": "x", "confirm": True, "fields": {"title": "t" * 6, "description": "d" * 12, "priority": "P2", "asset_id": "AST-BFP-101"}},
        ).status_code
        == 404
    )


def test_rag_search_endpoint(client):
    r = client.post("/api/rag/search", json={"query": "surge anti-surge valve", "asset_type": "compressor"}).json()
    assert r["citations"][0]["doc_id"] in ("TG-CMP-012", "KA-0057", "RN-2026")


def test_gui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Incident" in r.text
    assert client.get("/static/app.js").status_code == 200
