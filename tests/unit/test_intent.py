import pytest

from copilot.intent import classify, extract_entities


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Prepare an incident for the highest-priority active alarm in EastRefinery.", "prepare_incident"),
        ("Find similar historical tickets for this compressor alarm.", "similar_tickets"),
        ("Summarize the issue, likely cause, affected asset, and recommended action.", "summarize_issue"),
        ("Add the applicable troubleshooting procedure to the ticket draft.", "add_procedure"),
        ("Show open tickets linked to correlated assets.", "linked_tickets"),
        ("Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days", "investigate_asset"),
        ("Create the ticket", "create_ticket"),
        ("What is the escalation procedure for a P1 incident?", "knowledge_question"),
        ("list the tools", "list_tools"),
        ("hello", "help"),
    ],
)
def test_intent_classification(text, intent):
    assert classify(text).intent == intent


def test_unrelated_long_text_defaults_to_knowledge_question():
    p = classify("tell me about pump seals and leaks please")
    assert p.intent == "knowledge_question"


def test_entities_acceptance_scenario():
    e = extract_entities("Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days")
    assert e.asset_ref == "Boiler Feed Pump 101"
    assert e.asset_type == "pump"
    assert e.lookback_days == 90
    assert e.severity == ["high", "critical"]


@pytest.mark.parametrize(
    ("text", "field", "value"),
    [
        ("alarms in EastRefinery", "site", "EastRefinery"),
        ("alarms in east refinery", "site", "EastRefinery"),
        ("what happened in unit 5", "unit", "Unit 5"),
        ("past 2 weeks on K-501", "lookback_days", 14),
        ("last 3 months", "lookback_days", 90),
        ("check K-501", "asset_ref", "K-501"),
        ("details for ALM-000123", "alarm_id", "ALM-000123"),
        ("status of INC-1005", "ticket_id", "INC-1005"),
        ("asset AST-BFP-101 now", "asset_id", "AST-BFP-101"),
        ("only critical alarms", "severity", ["critical"]),
        ("active alarms", "status", ["active", "acknowledged"]),
    ],
)
def test_entity_extraction(text, field, value):
    assert getattr(extract_entities(text), field) == value


def test_ids_are_not_mistaken_for_tags():
    e = extract_entities("look at ALM-000123")
    assert e.asset_ref is None


def test_context_reference_detected():
    assert extract_entities("similar tickets for this alarm").refers_to_context
    assert not extract_entities("similar tickets for Boiler Feed Pump 101").refers_to_context
