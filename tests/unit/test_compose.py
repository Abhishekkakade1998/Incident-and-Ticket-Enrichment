"""Unit tests for evidence fusion: cause ranking, priority reconciliation, citation handling, drafts."""

from copilot import compose
from rag.models import Citation


def cite(ref, doc_id, doc_type, snippet, asset_types=("pump",)):
    return Citation(
        ref=ref,
        doc_id=doc_id,
        title=doc_id,
        section="s",
        chunk_id=f"{doc_id}#001",
        doc_type=doc_type,
        source_path=f"{doc_id}.md",
        score=0.9,
        snippet=snippet,
        asset_types=list(asset_types),
    )


def base_evidence(**kw):
    ev = compose.Evidence(intent="prepare_incident", question="q")
    ev.asset = {
        "asset_id": "AST-BFP-101",
        "asset_name": "Boiler Feed Pump 101",
        "asset_type": "pump",
        "site": "NorthPlant",
        "unit": "Unit 1",
        "criticality": "A",
        "parent_system": "BFW",
        "connected_assets": [],
    }
    ev.alarm = {
        "alarm_id": "ALM-000001",
        "asset_id": "AST-BFP-101",
        "asset_name": "Boiler Feed Pump 101",
        "alarm_name": "High Vibration",
        "alarm_type": "device",
        "severity": "high",
        "status": "active",
        "start_time": "2026-11-10T10:00:00Z",
        "value": 8,
        "threshold": 7.1,
    }
    for k, v in kw.items():
        setattr(ev, k, v)
    return ev


def test_causes_ranked_by_number_of_agreeing_sources():
    ev = base_evidence(
        recommendations={"likely_causes": ["Cavitation", "Misalignment"]},
        recommendations_ref="[T3]",
        similar_tickets=[
            {"ticket_id": "INC-1", "asset_type": "pump", "similarity": 0.8, "description": "", "resolution_notes": "Suction strainer blocked; cavitation"}
        ],
        citations=[cite("[D1]", "KA-1", "knowledge_article", "Partially blocked suction strainer reduces NPSH")],
    )
    causes = compose.analyse_causes(ev)
    assert causes[0]["cause"] in ("Cavitation", "Blocked suction strainer reducing NPSH margin")
    assert {c["cause"] for c in causes} >= {"Cavitation", "Blocked suction strainer reducing NPSH margin", "Coupling misalignment"}
    strainer = next(c for c in causes if "strainer" in c["cause"])
    assert strainer["sources"] == ["[D1]", "[INC-1]"]


def test_evidence_for_other_asset_types_is_ignored():
    ev = base_evidence(
        citations=[cite("[D1]", "TG-CMP", "troubleshooting_guide", "anti-surge valve stroke time", asset_types=("compressor",))],
        similar_tickets=[{"ticket_id": "INC-9", "asset_type": "compressor", "similarity": 0.3, "description": "fouled cooler", "resolution_notes": None}],
    )
    assert compose.analyse_causes(ev) == []


def test_priority_conflict_is_reported_and_more_urgent_band_wins():
    ev = base_evidence(priority={"priority_band": "P3", "recurrence_count_30d": 6}, priority_ref="[T2]")
    final, notes = compose.reconcile_priority(ev)
    assert final == "P2"
    assert notes and "Conflicting priority evidence" in notes[0] and "[T2]" in notes[0]


def test_no_conflict_when_sources_agree():
    ev = base_evidence(priority={"priority_band": "P2", "recurrence_count_30d": 6})
    assert compose.reconcile_priority(ev) == ("P2", [])


def test_safety_alarm_on_critical_asset_is_p1():
    ev = base_evidence()
    ev.alarm.update(alarm_type="safety", severity="critical")
    assert compose.escalation_priority(ev)[0] == "P1"


def test_validate_citations_rejects_unknown_markers_and_uncited_text():
    allowed = {"[T1]", "[D1]", "[INC-1001]"}
    assert compose.validate_citations("Pump vibrates [T1] per SOP [D1].", allowed) == (True, [])
    assert compose.validate_citations("Made up [D7].", allowed) == (False, ["[D7]"])
    assert compose.validate_citations("No citations at all.", allowed) == (False, [])


def test_draft_fields_follow_required_ticket_content():
    ev = base_evidence(
        recommendations={
            "recommendations": [{"step": 1, "action": "Check suction", "confidence": 0.8}],
            "likely_causes": ["Cavitation"],
            "historical_pattern": {"occurrences_90d": 30, "mean_interval_hours": 70},
        },
        recommendations_ref="[T3]",
        similar_tickets=[
            {"ticket_id": "INC-1001", "title": "t", "status": "closed", "resolution_notes": "strainer cleaned", "asset_type": "pump", "similarity": 0.7}
        ],
        citations=[cite("[D1]", "SOP-PUMP-004", "operating_procedure", "1. Check level\n2. Check strainer")],
    )
    f = compose.draft_fields(ev, "P2")
    assert f["title"] == "[P2] High Vibration on Boiler Feed Pump 101"
    assert f["assignee_group"] == "Rotating Equipment"
    assert f["similar_ticket_ids"] == ["INC-1001"]
    assert f["linked_documents"][0]["doc_id"] == "SOP-PUMP-004"
    for needle in ("Affected asset:", "Triggering alarm:", "Alarm history:", "Likely cause", "Recommended actions:", "Similar historical tickets:"):
        assert needle in f["description"]


def test_markdown_render_cites_every_section():
    ev = base_evidence(asset_ref="[T1]", alarm_ref="[T2]")
    md = compose.render_markdown(ev, compose.build_sections(ev), "Heading")
    assert "[T1]" in md and "[T2]" in md and md.startswith("### Heading")


def test_chunk_steps_extracts_numbered_and_bulleted_steps():
    assert compose.chunk_steps("Intro\n1. First\n2) Second\n- Third\nfree text") == ["First", "Second", "Third"]
