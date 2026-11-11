"""Validates the Alarm API simulator against the reference Postman collections.

A minimal Postman runner: substitutes ``{{variables}}``, sends each request with the
collection's bearer auth, asserts a 2xx, and applies the chaining scripts' variable
extraction (asset_id, alarm_id, calculation_id, window_start/end ...).
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
POSTMAN = ROOT / "postman"
COLLECTIONS = [POSTMAN / "Alarm-API-Simulator.postman_collection.json", POSTMAN / "chaining" / "Alarm-API-Chaining.postman_collection.json"]


@pytest.fixture(scope="module")
def sim(alarm_sim_app):
    return TestClient(alarm_sim_app)


def _walk(items):
    for i in items:
        if "item" in i:
            yield from _walk(i["item"])
        else:
            yield i


def _sub(text: str, v: dict) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(v.get(m.group(1), m.group(0))), text)


def _extract(script: str, body: dict, v: dict) -> None:
    """Interpret the variable assignments used by the collections' test scripts."""
    for var in re.findall(r"collectionVariables\.set\('(\w+)'", script):
        if var == "asset_id":
            rows = body.get("results") or body.get("data") or []
            v[var] = rows[0]["asset_id"]
        elif var in ("asset_id_2", "asset_id_3"):
            rows = body["results"]
            idx = 1 if var.endswith("2") else 2
            v[var] = (rows[idx] if len(rows) > idx else rows[0])["asset_id"]
        elif var == "alarm_id":
            if body.get("data"):
                v[var] = body["data"][0]["alarm_id"]
        elif var == "calculation_id":
            v[var] = body["calculation_id"]
        elif var in ("window_start", "window_end"):
            ws = body.get("flood_windows") or []
            key = "start" if var == "window_start" else "end"
            v[var] = ws[0][key] if ws else v["start_time" if key == "start" else "end_time"]


@pytest.mark.parametrize("collection", COLLECTIONS, ids=lambda p: p.name)
def test_postman_collection_runs_green(sim, collection):
    coll = json.loads(collection.read_text())
    v = {x["key"]: x["value"] for x in coll.get("variable", [])}
    v.setdefault("start_time", "2026-05-01T00:00:00Z")
    v.setdefault("end_time", "2026-07-01T00:00:00Z")
    v["baseUrl"] = ""
    v.setdefault("auth_token", "demo-token")
    ran = 0
    for item in _walk(coll["item"]):
        req = item["request"]
        url = req["url"] if isinstance(req["url"], str) else req["url"]["raw"]
        url = _sub(url, v)
        headers = {h["key"]: _sub(h["value"], v) for h in req.get("header", [])}
        if (req.get("auth") or {}).get("type") != "noauth":
            headers["Authorization"] = f"Bearer {v['auth_token']}"
        body = req.get("body", {}).get("raw")
        resp = sim.request(req["method"], url, headers=headers, content=_sub(body, v) if body else None)
        assert resp.status_code < 300, f"{item['name']}: {resp.status_code} {resp.text[:300]}"
        if "trace_id" in headers:
            assert resp.headers.get("trace_id") == headers["trace_id"]
            assert resp.json()["meta"]["trace_id"] == headers["trace_id"]
        for ev in item.get("event", []):
            _extract("\n".join(ev["script"]["exec"]), resp.json(), v)
        ran += 1
    assert ran >= 15


def test_auth_required(sim):
    assert sim.get("/assets/search", params={"query": "pump"}).status_code == 401
    r = sim.get("/assets/search", params={"query": "pump"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert sim.get("/health").status_code == 200


def test_pagination_metadata(sim):
    h = {"Authorization": "Bearer demo-token"}
    p1 = sim.get("/alarms", params={"asset_id": "AST-BFP-101", "page": 1, "page_size": 10}, headers=h).json()
    p2 = sim.get("/alarms", params={"asset_id": "AST-BFP-101", "page": 2, "page_size": 10}, headers=h).json()
    assert p1["pagination"]["has_next"] and p1["pagination"]["total"] > 20
    assert {a["alarm_id"] for a in p1["data"]}.isdisjoint({a["alarm_id"] for a in p2["data"]})


def test_validation_errors_are_structured(sim):
    h = {"Authorization": "Bearer demo-token"}
    r = sim.post(
        "/alarms/summary",
        json={"asset_ids": ["AST-BFP-101"], "time_range": {"start_time": "2026-07-01T00:00:00Z", "end_time": "2026-05-01T00:00:00Z"}, "kpis": ["bogus"]},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert sim.get("/alarms/ALM-999999", headers=h).status_code == 404


def test_seeded_patterns_exist(sim):
    """The dataset contains the patterns the acceptance scenario depends on."""
    h = {"Authorization": "Bearer demo-token"}
    tr = {"start_time": "2026-08-12T12:00:00Z", "end_time": "2026-11-10T12:00:00Z"}
    s = sim.post(
        "/alarms/summary",
        json={"asset_ids": ["AST-BFP-101"], "time_range": tr, "severity": ["high", "critical"], "kpis": ["alarm_count", "recurring_rate"]},
        headers=h,
    ).json()
    names = [g["alarm_name"] for g in s["groups"][:3]]
    assert set(names) == {"Low Suction Pressure", "High Vibration", "High Bearing Temperature"}
    c = sim.post("/alarms/correlation", json={"asset_ids": ["AST-CMP-501"], "time_range": tr, "min_support": 2}, headers=h).json()
    assert c["correlated_assets"][0]["asset_id"] == "AST-MTR-501"
    active = sim.get("/alarms", params={"site": "EastRefinery", "status": "active"}, headers=h).json()
    assert any(a["severity"] == "critical" for a in active["data"])


def test_fault_injection_admin(sim):
    h = {"Authorization": "Bearer demo-token"}
    sim.post("/_admin/faults", json={"path_pattern": "^/analytics", "mode": "error", "status_code": 503, "remaining": 1}, headers=h)
    assert sim.get("/analytics/kpi-definitions", headers=h).status_code == 503
    assert sim.get("/analytics/kpi-definitions", headers=h).status_code == 200
