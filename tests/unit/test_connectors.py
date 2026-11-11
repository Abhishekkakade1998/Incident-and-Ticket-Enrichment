"""API client tests: payload construction, auth + trace headers, retries, timeouts, error mapping."""

import json

import httpx
import pytest

from common.tracing import TraceContext, reset_trace, set_trace
from connectors.alarm_api import AlarmApiClient
from connectors.http import AuthError, NotFoundError, RetryPolicy, UpstreamTimeout, UpstreamUnavailable, ValidationFailed
from connectors.ticketing_api import TicketingClient

FAST = RetryPolicy(max_retries=2, backoff_base_s=0.001, backoff_max_s=0.002)


def client_with(handler, cls=AlarmApiClient):
    return cls("http://api", "secret-token", transport=httpx.MockTransport(handler), retry=FAST)


async def test_auth_and_trace_headers_are_sent():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, json={"results": [], "total": 0})

    token = set_trace(TraceContext(trace_id="trace-xyz", client_id="copilot", metadata_tag="demo"))
    try:
        await client_with(handler).search_assets("pump")
    finally:
        reset_trace(token)
    assert seen["authorization"] == "Bearer secret-token"
    assert seen["trace_id"] == "trace-xyz"
    assert seen["x-client-id"] == "copilot"
    assert seen["x-metadata-tag"] == "demo"


async def test_query_params_drop_none_and_join_lists():
    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"data": [], "pagination": {}})

    await client_with(handler).list_alarms(site="EastRefinery", status=["active", "acknowledged"], severity=None, page=2)
    assert "status=active%2Cacknowledged" in captured["url"]
    assert "severity" not in captured["url"]
    assert "page=2" in captured["url"]


async def test_path_segments_are_encoded():
    captured = {}

    def handler(req):
        captured["path"] = req.url.raw_path.decode()
        return httpx.Response(200, json={})

    await client_with(handler).get_alarm("../admin")
    assert captured["path"] == "/alarms/..%2Fadmin"


async def test_post_body_is_json():
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    await client_with(handler).priority_score("ALM-000001")
    assert captured["body"] == {"alarm_id": "ALM-000001"}


async def test_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503, json={"error": {"message": "busy"}}) if calls["n"] < 3 else httpx.Response(200, json={"ok": 1})

    body, stats = await client_with(handler).kpi_definitions()
    assert body == {"ok": 1}
    assert stats.attempts == 3 and stats.retry_count == 2
    assert stats.retries == ["upstream_unavailable", "upstream_unavailable"]


async def test_gives_up_after_max_retries():
    def handler(req):
        return httpx.Response(502, json={"error": {"message": "bad gateway"}})

    with pytest.raises(UpstreamUnavailable) as exc:
        await client_with(handler).kpi_definitions()
    assert exc.value.attempts == 3 and exc.value.retryable


@pytest.mark.parametrize(("status", "exc_type"), [(401, AuthError), (403, AuthError), (404, NotFoundError), (422, ValidationFailed)])
async def test_non_retryable_errors_are_mapped_and_not_retried(status, exc_type):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(status, json={"error": {"message": "nope", "details": [1]}})

    with pytest.raises(exc_type) as exc:
        await client_with(handler).get_alarm("ALM-000001")
    assert calls["n"] == 1
    assert exc.value.status_code == status
    if status in (401, 403):
        assert "secret-token" not in exc.value.message


async def test_timeout_is_mapped_and_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(UpstreamTimeout):
        await client_with(handler).kpi_definitions()
    assert calls["n"] == 3


async def test_connection_error_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(UpstreamUnavailable, match="unreachable"):
        await client_with(handler).kpi_definitions()


async def test_ticketing_create_sends_idempotency_key_and_repeated_asset_params():
    captured = {}

    def handler(req):
        captured.setdefault("reqs", []).append(req)
        return httpx.Response(201 if req.method == "POST" else 200, json={"data": [], "pagination": {}})

    c = client_with(handler, TicketingClient)
    await c.create_ticket({"title": "t"}, "DRAFT-123456")
    await c.list_tickets(asset_ids=["A", "B"], statuses=["open"])
    post, get = captured["reqs"]
    assert post.headers["idempotency-key"] == "DRAFT-123456"
    assert get.url.params.get_list("asset_id") == ["A", "B"]
    assert get.url.params["status"] == "open"
