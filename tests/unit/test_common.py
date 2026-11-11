import json
import logging

import pytest

from common import approval
from common.logging import JsonFormatter, redact
from common.tracing import TraceContext, current_trace, reset_trace, set_trace


def test_redact_masks_secret_keys_and_bearer_tokens():
    data = {"api_key": "abc", "nested": {"Authorization": "Bearer xyz"}, "note": "sent Bearer s3cr3t.tok to server", "token_present": True}
    out = redact(data)
    assert out["api_key"] == "***REDACTED***"
    assert out["nested"]["Authorization"] == "***REDACTED***"
    assert "s3cr3t" not in out["note"]
    assert out["token_present"] is True


def test_json_formatter_includes_trace_and_redacts():
    token = set_trace(TraceContext(trace_id="trace-1", conversation_id="conv-1"))
    try:
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "calling with Bearer abc.def", None, None)
        rec.fields = {"password": "p", "mcp_tool": "search_assets"}
        line = json.loads(JsonFormatter("svc").format(rec))
    finally:
        reset_trace(token)
    assert line["trace_id"] == "trace-1"
    assert line["conversation_id"] == "conv-1"
    assert line["password"] == "***REDACTED***"
    assert "abc.def" not in line["msg"]
    assert line["mcp_tool"] == "search_assets"


def test_trace_context_headers_roundtrip():
    ctx = TraceContext(trace_id="t-1", request_id="r-1", conversation_id="c-1", client_id="gui", metadata_tag="demo")
    h = ctx.to_headers()
    assert h == {"trace_id": "t-1", "x-request-id": "r-1", "x-client-id": "gui", "x-metadata-tag": "demo", "x-conversation-id": "c-1"}
    back = TraceContext.from_mapping({k.upper(): v for k, v in h.items()})
    assert back.trace_id == "t-1" and back.conversation_id == "c-1"


def test_trace_context_generates_ids_when_missing():
    ctx = TraceContext.from_mapping({})
    assert ctx.trace_id.startswith("trace-") and ctx.request_id.startswith("req-")
    assert current_trace() is None


def test_approval_token_roundtrip_and_tamper_detection():
    payload = {"title": "x", "priority": "P1", "tags": ["a"]}
    tok = approval.mint(payload, "key", now=1000)
    assert approval.verify(tok, dict(reversed(list(payload.items()))), "key", now=1001) == approval.canonical_hash(payload)
    with pytest.raises(approval.ApprovalError, match="does not match"):
        approval.verify(tok, {**payload, "priority": "P4"}, "key", now=1001)
    with pytest.raises(approval.ApprovalError, match="does not match"):
        approval.verify(tok, payload, "other-key", now=1001)
    with pytest.raises(approval.ApprovalError, match="expired"):
        approval.verify(tok, payload, "key", now=1000 + 601)
    with pytest.raises(approval.ApprovalError, match="malformed"):
        approval.verify("garbage", payload, "key")
