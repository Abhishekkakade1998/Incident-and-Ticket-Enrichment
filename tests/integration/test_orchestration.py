"""Orchestration tests: multi-step MCP chains, output passing, RAG in the same workflow,
partial failures, conflicting evidence, context retention and write approval."""

from functools import partial

from common import approval
from copilot.state import TicketFields
from tests.conftest import APPROVAL_KEY
from tests.integration.test_mcp_client import DeadConnection

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely contributing "
    "factors, retrieve the relevant operating procedure, and provide recommended actions with source evidence."
)


def mcp_steps(resp):
    return [s for s in resp["trace"]["steps"] if s["kind"] == "mcp"]


def tools_called(resp):
    return [s["tool"] for s in mcp_steps(resp)]


async def test_prepare_incident_chains_tools_and_passes_outputs(orchestrator):
    r = await orchestrator.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    assert r["status"] == "ok" and r["intent"] == "prepare_incident"
    called = tools_called(r)
    assert called[:3] == ["list_alarms", "rank_alarms_by_priority", "get_alarm"]
    assert {"get_asset_metadata", "get_operator_recommendations", "correlate_alarms", "search_similar_tickets", "list_tickets"} <= set(called)
    steps = {s["tool"]: s for s in mcp_steps(r)}
    # outputs flow forward: list -> rank -> get_alarm
    listed_ids = steps["rank_alarms_by_priority"]["arguments"]["alarm_ids"]
    assert steps["rank_alarms_by_priority"]["depends_on"] == [steps["list_alarms"]["id"]]
    assert steps["get_alarm"]["arguments"]["alarm_id"] in listed_ids
    assert steps["get_alarm"]["depends_on"] == [steps["rank_alarms_by_priority"]["id"]]
    assert steps["get_operator_recommendations"]["arguments"]["alarm_id"] == steps["get_alarm"]["arguments"]["alarm_id"]
    # The highest-priority active alarm in EastRefinery is the critical K-501 surge.
    d = r["ticket_draft"]["fields"]
    assert d["asset_id"] == "AST-CMP-501" and d["alarm_name"] == "Surge Detected" and d["priority"] == "P1"
    # RAG participated in the same workflow and is cited
    assert any(s["kind"] == "rag" for s in r["trace"]["steps"])
    assert any(c["doc_id"] == "TG-CMP-012" for c in r["citations"])
    assert "[D1]" in r["answer_markdown"] and "[T1]" in r["answer_markdown"]
    # correlated asset's open ticket surfaced
    assert {"INC-1011", "INC-1012"} <= {t["ticket_id"] for t in r["linked_tickets"]}
    assert r["requires_confirmation"] is False  # drafting is not writing


async def test_acceptance_scenario_investigation(orchestrator):
    r = await orchestrator.handle(ACCEPTANCE, None)
    assert r["intent"] == "investigate_asset" and r["status"] == "ok"
    called = tools_called(r)
    assert called[0] == "search_assets"
    assert {"get_asset_metadata", "get_alarm_summary", "get_alarm_trends", "correlate_alarms", "get_operator_recommendations"} <= set(called)
    summary = next(s for s in mcp_steps(r) if s["tool"] == "get_alarm_summary")
    assert summary["arguments"]["asset_ids"] == ["AST-BFP-101"]
    assert summary["arguments"]["severity"] == ["high", "critical"] and summary["arguments"]["lookback_days"] == 90
    cited_docs = {c["doc_id"] for c in r["citations"]}
    assert "SOP-PUMP-004" in cited_docs
    causes = [c["cause"] for c in r["sections"]["likely_causes"]]
    assert any("strainer" in c.lower() for c in causes) and "Cavitation" in causes
    assert r["sections"]["recommended_actions"]
    assert r["sections"]["procedure"]["doc_id"] == "SOP-PUMP-004"
    # every citation marker used in the answer resolves to a citation or a trace step
    import re

    refs = set(re.findall(r"\[(D\d+|T\d+)\]", r["answer_markdown"]))
    known = {c["ref"].strip("[]") for c in r["citations"]} | {s["ref"].strip("[]") for s in mcp_steps(r) if s["ref"]}
    assert refs <= known


async def test_context_retention_across_turns(orchestrator):
    first = await orchestrator.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    cid = first["conversation_id"]
    sim = await orchestrator.handle("Find similar historical tickets for this compressor alarm.", cid)
    assert sim["intent"] == "similar_tickets" and sim["similar_tickets"][0]["asset_id"] == "AST-CMP-501"
    assert "list_alarms" not in tools_called(sim)  # reused the alarm from context
    linked = await orchestrator.handle("Show open tickets linked to correlated assets.", cid)
    assert {"INC-1011", "INC-1012"} <= {t["ticket_id"] for t in linked["linked_tickets"]}
    summ = await orchestrator.handle("Summarize the issue, likely cause, affected asset, and recommended action.", cid)
    assert {"affected_asset", "likely_causes", "recommended_actions"} <= set(summ["sections"])
    proc = await orchestrator.handle("Add the applicable troubleshooting procedure to the ticket draft.", cid)
    draft = proc["ticket_draft"]
    assert draft["draft_id"] == first["ticket_draft"]["draft_id"]
    assert any(d["doc_id"] == "TG-CMP-012" for d in draft["fields"]["linked_documents"])
    assert any(h["action"] == "procedure_added" for h in draft["history"])


async def test_create_ticket_via_chat_requires_confirmation_and_never_writes(orchestrator):
    first = await orchestrator.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    r = await orchestrator.handle("Create the ticket", first["conversation_id"])
    assert r["requires_confirmation"] is True
    assert "create_ticket" not in tools_called(r)
    assert orchestrator.store.drafts[first["ticket_draft"]["draft_id"]].status == "draft"


async def test_approval_creates_ticket_once(orchestrator):
    first = await orchestrator.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    d = orchestrator.store.drafts[first["ticket_draft"]["draft_id"]]
    fields = TicketFields(**{**d.fields.model_dump(), "priority": "P2", "title": "[P2] Edited by operator"})
    mint = partial(approval.mint, key=APPROVAL_KEY)
    res = await orchestrator.approve_draft(d.draft_id, fields, "jane.operator", mint)
    assert res["ok"] and res["ticket_id"].startswith("INC-")
    assert res["ticket"]["title"] == "[P2] Edited by operator"
    again = await orchestrator.approve_draft(d.draft_id, fields, "jane.operator", mint)
    assert again["ticket_id"] == res["ticket_id"] and again["idempotent_replay"]
    actions = [e["action"] for e in orchestrator.audit.list(first["conversation_id"])]
    assert "approval_granted" in actions and "ticket_created" in actions


async def test_approval_with_wrong_signing_key_is_blocked(orchestrator):
    first = await orchestrator.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    d = orchestrator.store.drafts[first["ticket_draft"]["draft_id"]]
    res = await orchestrator.approve_draft(d.draft_id, d.fields, "mallory", partial(approval.mint, key="not-the-key"))
    assert not res["ok"] and res["error"]["code"] == "approval_required"
    assert orchestrator.store.drafts[d.draft_id].status == "failed"


async def test_partial_failure_when_ticketing_server_down(orchestrator):
    orchestrator.registry.connections["ticketing"] = DeadConnection("ticketing")
    r = await orchestrator.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    assert r["status"] == "partial"
    assert r["ticket_draft"] is not None  # still drafted from alarm data + documents
    assert any("search_similar_tickets" in w for w in r["warnings"])
    failed = [s for s in mcp_steps(r) if s["status"] != "ok"]
    assert {s["tool"] for s in failed} == {"search_similar_tickets", "list_tickets"}
    assert r["citations"]


async def test_alarm_server_down_is_reported_as_error(orchestrator):
    orchestrator.registry.connections["alarm-management"] = DeadConnection("alarm-management")
    r = await orchestrator.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    assert r["status"] == "error" and r["ticket_draft"] is None
    assert "could not retrieve alarms" in r["answer_markdown"]


async def test_conflicting_priority_evidence_is_surfaced(orchestrator):
    # A high-severity process alarm on a criticality-A asset that recurs: API band vs ESC-001 rule differ.
    r = await orchestrator.handle("Prepare an incident for the highest-priority active alarm on Boiler Feed Pump 101", None)
    assert r["status"] == "ok"
    draft = r["ticket_draft"]
    api_band = next(s for s in mcp_steps(r) if s["tool"] == "score_alarm_priority")["output_preview"]["priority_band"]
    conflicts = draft["rationale"]["priority_conflicts"]
    if conflicts:
        assert "Conflicting priority evidence" in conflicts[0]
        assert draft["fields"]["priority"] <= api_band
        assert "Note:" in r["answer_markdown"]
    else:
        assert draft["fields"]["priority"] == api_band


async def test_unknown_asset_asks_for_clarification(orchestrator):
    r = await orchestrator.handle("Investigate alarms on Unicorn Pump 999 over the last 30 days", None)
    assert r["status"] == "needs_clarification"


async def test_scope_missing_asks_for_clarification(orchestrator):
    r = await orchestrator.handle("Prepare an incident for the highest-priority active alarm", None)
    assert r["status"] == "needs_clarification" and r["ticket_draft"] is None


async def test_knowledge_question_uses_rag_only_and_low_confidence_declines(orchestrator):
    good = await orchestrator.handle("What is the escalation procedure for a P1 incident?", None)
    assert tools_called(good) == [] and good["citations"][0]["doc_id"] == "ESC-001"
    bad = await orchestrator.handle("what is the cafeteria menu today", None)
    assert "won't guess" in bad["answer_markdown"] or "No documentation" in bad["answer_markdown"]


async def test_injected_document_never_reaches_answer(orchestrator):
    r = await orchestrator.handle("What causes high bearing temperature on pumps and what tips exist?", None)
    assert all(c["doc_id"] != "KA-0099" for c in r["citations"])
    assert "ignore all previous instructions" not in r["answer_markdown"].lower()
    assert any("excluded" in w for w in r["warnings"])


async def test_list_tools_intent_shows_catalog(orchestrator):
    r = await orchestrator.handle("list the tools", None)
    assert "create_ticket" in r["answer_markdown"] and "WRITE" in r["answer_markdown"]
