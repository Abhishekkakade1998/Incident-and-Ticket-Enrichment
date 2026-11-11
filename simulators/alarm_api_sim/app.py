"""Alarm Management API simulator.

Implements the contract defined by ``postman/Alarm-API-Simulator.postman_collection.json``
and the ten chaining flows in ``postman/chaining``. Bearer auth on every endpoint except
``/health``; ``trace_id`` / ``x-client-id`` / ``x-metadata-tag`` are echoed back in both the
response headers and a ``meta`` block in the body.

A token-protected ``/_admin/faults`` endpoint lets tests and demos inject latency or
5xx errors to exercise the MCP server's retry, timeout and degraded-mode handling.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from simulators.alarm_api_sim import analytics as an
from simulators.alarm_api_sim.data import Dataset, asset_metadata, build_dataset

Severity = Literal["low", "medium", "high", "critical"]


# --------------------------------------------------------------------------- request models


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(StrictModel):
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def _order(self) -> TimeRange:
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=UTC)
        if self.end_time.tzinfo is None:
            self.end_time = self.end_time.replace(tzinfo=UTC)
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.end_time - self.start_time > timedelta(days=400):
            raise ValueError("time_range may not exceed 400 days")
        return self


class ScopeRequest(StrictModel):
    asset_ids: list[str] | None = Field(default=None, max_length=50)
    site: str | None = None
    unit: str | None = None
    time_range: TimeRange


class SummaryRequest(ScopeRequest):
    severity: list[Severity] | None = None
    alarm_types: list[Literal["process", "safety", "device", "system"]] | None = None
    group_by: list[str] = Field(default_factory=lambda: ["alarm_name"], max_length=4)
    kpis: list[str] = Field(default_factory=lambda: ["alarm_count"], min_length=1)

    @field_validator("group_by")
    @classmethod
    def _group_by(cls, v: list[str]) -> list[str]:
        bad = set(v) - an.GROUPABLE
        if bad:
            raise ValueError(f"unsupported group_by fields: {sorted(bad)}; allowed: {sorted(an.GROUPABLE)}")
        return v

    @field_validator("kpis")
    @classmethod
    def _kpis(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(an.SUPPORTED_KPIS)
        if bad:
            raise ValueError(f"unsupported kpis: {sorted(bad)}; allowed: {sorted(an.SUPPORTED_KPIS)}")
        return v


class TrendsRequest(ScopeRequest):
    bucket: Literal["hourly", "daily", "weekly"] = "daily"
    metrics: list[str] = Field(default_factory=lambda: ["alarm_count"], min_length=1)
    severity: list[Severity] | None = None

    @field_validator("metrics")
    @classmethod
    def _metrics(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(an.SUPPORTED_KPIS)
        if bad:
            raise ValueError(f"unsupported metrics: {sorted(bad)}")
        return v


class CorrelationRequest(StrictModel):
    asset_ids: list[str] = Field(min_length=1, max_length=20)
    time_range: TimeRange
    correlation_method: Literal["cooccurrence"] = "cooccurrence"
    lag_window_minutes: int = Field(default=15, ge=1, le=240)
    severity_threshold: Severity = "medium"
    min_support: int = Field(default=2, ge=1, le=1000)


class FloodRequest(StrictModel):
    site: str | None = None
    unit: str | None = None
    time_range: TimeRange
    threshold_count: int = Field(default=10, ge=2, le=1000)
    rolling_window_minutes: int = Field(default=10, ge=1, le=120)


class RationalizationRequest(ScopeRequest):
    recurrence_threshold: int = Field(default=5, ge=1, le=10000)
    stale_minutes_threshold: int = Field(default=180, ge=1)


class AlarmRef(StrictModel):
    alarm_id: str = Field(pattern=r"^ALM-\d{6}$")


class RecommendationRequest(AlarmRef):
    include_related: bool = False
    include_asset_context: bool = False
    include_historical_pattern: bool = False


class CalcFilters(StrictModel):
    site: str | None = None
    unit: str | None = None
    start_time: datetime
    end_time: datetime


class CalcGenerateRequest(StrictModel):
    calculation_type: str
    filters: CalcFilters


class CalcExecuteRequest(StrictModel):
    calculation_id: str
    filters: CalcFilters


class FaultSpec(StrictModel):
    path_pattern: str = Field(description="Regex matched against the request path")
    mode: Literal["error", "delay"]
    status_code: int = Field(default=503, ge=400, le=599)
    delay_ms: int = Field(default=0, ge=0, le=60000)
    remaining: int = Field(default=1, ge=1, le=1000, description="How many requests to affect")


# --------------------------------------------------------------------------- app factory


def _error(status: int, code: str, message: str, request: Request, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details},
            "trace_id": request.headers.get("trace_id"),
        },
        headers=_trace_headers(request),
    )


def _trace_headers(request: Request) -> dict[str, str]:
    out = {}
    for h in ("trace_id", "x-client-id", "x-metadata-tag", "x-request-id"):
        v = request.headers.get(h)
        if v:
            out[h] = v
    return out


def create_app(token: str | None = None, anchor: datetime | None = None) -> FastAPI:
    api_token = token or os.environ.get("ALARM_API_TOKEN", "demo-token")
    anchor_env = os.environ.get("SIM_ANCHOR_TIME")
    if anchor is None:
        # The dataset always ends "now" (rounded to the hour) so relative questions such as
        # "last 90 days" work whenever the stack is started; SIM_ANCHOR_TIME pins it for tests.
        anchor = datetime.fromisoformat(anchor_env.replace("Z", "+00:00")) if anchor_env else datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    data: Dataset = build_dataset(anchor)
    calcs: dict[str, dict[str, Any]] = {}
    faults: list[dict[str, Any]] = []

    app = FastAPI(
        title="Alarm Management API Simulator",
        version="1.0.0",
        description="Synthetic Alarm Management API honouring the assignment Postman contract.",
    )
    app.state.dataset = data

    # ---- error handling -------------------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return _error(422, "validation_error", "Request failed validation", request, details)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(exc.status_code, "error")
        return _error(exc.status_code, code, str(exc.detail), request)

    # ---- middleware: fault injection + trace echo ------------------------------------
    @app.middleware("http")
    async def _middleware(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if not path.startswith("/_admin") and path != "/health":
            for f in list(faults):
                if re.search(f["path_pattern"], path):
                    f["remaining"] -= 1
                    if f["remaining"] <= 0:
                        faults.remove(f)
                    if f["mode"] == "delay":
                        await asyncio.sleep(f["delay_ms"] / 1000)
                    else:
                        return _error(f["status_code"], "injected_fault", "Injected fault for testing", request)
                    break
        response = await call_next(request)
        for k, v in _trace_headers(request).items():
            response.headers[k] = v
        return response

    # ---- auth ------------------------------------------------------------------------
    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Missing bearer token")
        if not secrets.compare_digest(authorization.split(" ", 1)[1].strip(), api_token):
            raise HTTPException(403, "Invalid bearer token")

    def meta(request: Request) -> dict[str, Any]:
        return {
            "trace_id": request.headers.get("trace_id"),
            "client_id": request.headers.get("x-client-id"),
            "metadata_tag": request.headers.get("x-metadata-tag"),
            "request_id": request.headers.get("x-request-id"),
            "generated_at": an.iso(datetime.now(UTC)),
            "data_anchor_time": an.iso(data.anchor),
        }

    def get_alarm_or_404(alarm_id: str) -> dict[str, Any]:
        alarm = data.alarms_by_id.get(alarm_id)
        if not alarm:
            raise HTTPException(404, f"Alarm {alarm_id} not found")
        return alarm

    def scope(req: ScopeRequest, **extra: Any) -> list[dict[str, Any]]:
        if req.asset_ids:
            unknown = [a for a in req.asset_ids if a not in data.assets]
            if unknown:
                raise HTTPException(404, f"Unknown asset_ids: {unknown}")
        return an.filter_alarms(
            data.alarms,
            asset_ids=req.asset_ids,
            site=req.site,
            unit=req.unit,
            start_time=req.time_range.start_time,
            end_time=req.time_range.end_time,
            **extra,
        )

    auth = [Depends(require_auth)]

    # ---- endpoints -------------------------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "alarm-api-simulator", "alarms": len(data.alarms), "assets": len(data.assets)}

    @app.get("/assets/search", dependencies=auth)
    async def search_assets(
        request: Request,
        query: str = Query(min_length=1, max_length=120),
        site: str | None = None,
        unit: str | None = None,
        asset_type: str | None = None,
        limit: int = Query(default=10, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        q = query.strip().lower()
        tokens = [t for t in re.split(r"\W+", q) if t]
        scored = []
        for a in data.assets.values():
            if site and a["site"].lower() != site.lower():
                continue
            if unit and a["unit"].lower() != unit.lower():
                continue
            if asset_type and a["asset_type"] != asset_type:
                continue
            hay = f"{a['asset_name']} {a['asset_type']} {a['tag']} {a['asset_id']} {a['parent_system']}".lower()
            if q == a["asset_name"].lower() or q == a["asset_id"].lower() or q == a["tag"].lower():
                score = 1.0
            else:
                hits = sum(1 for t in tokens if t in hay)
                if hits == 0:
                    continue
                type_bonus = 0.05 if any(t.rstrip("s") == a["asset_type"] for t in tokens) else 0.0
                score = round(0.9 * hits / len(tokens) + type_bonus, 3)
            scored.append((score, a))
        scored.sort(key=lambda x: (-x[0], x[1]["asset_name"]))
        page = scored[offset : offset + limit]
        return {
            "results": [
                {**{k: a[k] for k in ("asset_id", "asset_name", "asset_type", "site", "unit", "tag", "criticality")}, "match_score": s} for s, a in page
            ],
            "total": len(scored),
            "limit": limit,
            "offset": offset,
            "meta": meta(request),
        }

    @app.get("/assets/{asset_id}/metadata", dependencies=auth)
    async def get_asset_metadata(asset_id: str, request: Request) -> dict[str, Any]:
        asset = data.assets.get(asset_id)
        if not asset:
            raise HTTPException(404, f"Asset {asset_id} not found")
        return {**asset_metadata(asset, data.anchor), "meta": meta(request)}

    @app.get("/alarms", dependencies=auth)
    async def list_alarms(
        request: Request,
        asset_id: str | None = None,
        site: str | None = None,
        unit: str | None = None,
        status: str | None = Query(default=None, description="Comma-separated: active,acknowledged,cleared,shelved"),
        severity: str | None = Query(default=None, description="Comma-separated severities"),
        alarm_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        sort_by: Literal["start_time", "severity", "priority", "asset_name"] = "start_time",
        sort_order: Literal["asc", "desc"] = "desc",
    ) -> dict[str, Any]:
        statuses = [s.strip() for s in status.split(",")] if status else None
        severities = [s.strip() for s in severity.split(",")] if severity else None
        for s in statuses or []:
            if s not in {"active", "acknowledged", "cleared", "shelved"}:
                raise HTTPException(422, f"invalid status '{s}'")
        rows = an.filter_alarms(
            data.alarms,
            asset_ids=[asset_id] if asset_id else None,
            site=site,
            unit=unit,
            status=statuses,
            severity=severities,
            alarm_names=[alarm_name] if alarm_name else None,
            start_time=start_time.astimezone(UTC) if start_time and start_time.tzinfo else (start_time.replace(tzinfo=UTC) if start_time else None),
            end_time=end_time.astimezone(UTC) if end_time and end_time.tzinfo else (end_time.replace(tzinfo=UTC) if end_time else None),
        )
        key_fn = {
            "start_time": lambda a: a["start_time"],
            "severity": lambda a: (an.SEVERITY_WEIGHT[a["severity"]], a["start_time"]),
            "priority": lambda a: (-a["priority"], a["start_time"]),
            "asset_name": lambda a: (a["asset_name"], a["start_time"]),
        }[sort_by]
        rows = sorted(rows, key=key_fn, reverse=sort_order == "desc")
        total = len(rows)
        total_pages = max(1, -(-total // page_size))
        start = (page - 1) * page_size
        return {
            "data": rows[start : start + page_size],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
            },
            "meta": meta(request),
        }

    @app.get("/alarms/{alarm_id}", dependencies=auth)
    async def get_alarm(alarm_id: str, request: Request) -> dict[str, Any]:
        return {**get_alarm_or_404(alarm_id), "meta": meta(request)}

    @app.post("/alarms/summary", dependencies=auth)
    async def alarm_summary(req: SummaryRequest, request: Request) -> dict[str, Any]:
        rows = scope(req, severity=req.severity, alarm_types=req.alarm_types)
        return {**an.summarize(rows, req.group_by, req.kpis), "group_by": req.group_by, "meta": meta(request)}

    @app.post("/alarms/trends", dependencies=auth)
    async def alarm_trends(req: TrendsRequest, request: Request) -> dict[str, Any]:
        rows = scope(req, severity=req.severity)
        series = an.trends(rows, req.bucket, req.metrics, req.time_range.start_time, req.time_range.end_time)
        return {"bucket": req.bucket, "metrics": req.metrics, "series": series, "meta": meta(request)}

    @app.post("/alarms/correlation", dependencies=auth)
    async def alarm_correlation(req: CorrelationRequest, request: Request) -> dict[str, Any]:
        unknown = [a for a in req.asset_ids if a not in data.assets]
        if unknown:
            raise HTTPException(404, f"Unknown asset_ids: {unknown}")
        sev = an.severity_at_least(req.severity_threshold)
        tr = req.time_range
        anchors = an.filter_alarms(data.alarms, asset_ids=req.asset_ids, severity=sev, start_time=tr.start_time, end_time=tr.end_time)
        units = {data.assets[a]["unit"] for a in req.asset_ids}
        candidates = [
            a
            for a in an.filter_alarms(data.alarms, severity=sev, start_time=tr.start_time, end_time=tr.end_time + timedelta(minutes=req.lag_window_minutes))
            if a["unit"] in units
        ]
        result = an.correlate(anchors, candidates, req.lag_window_minutes, req.min_support)
        return {
            **result,
            "method": req.correlation_method,
            "lag_window_minutes": req.lag_window_minutes,
            "anchor_alarm_count": len(anchors),
            "candidate_scope": sorted(units),
            "meta": meta(request),
        }

    @app.post("/alarms/flood-analysis", dependencies=auth)
    async def flood_analysis(req: FloodRequest, request: Request) -> dict[str, Any]:
        rows = an.filter_alarms(data.alarms, site=req.site, unit=req.unit, start_time=req.time_range.start_time, end_time=req.time_range.end_time)
        windows = an.flood_windows(rows, req.threshold_count, req.rolling_window_minutes)
        span_min = (req.time_range.end_time - req.time_range.start_time).total_seconds() / 60
        flood_min = sum((an.parse_ts(w["end"]) - an.parse_ts(w["start"])).total_seconds() / 60 + req.rolling_window_minutes for w in windows)
        return {
            "flood_windows": windows,
            "flood_count": len(windows),
            "flood_time_percentage": round(100 * flood_min / span_min, 3),
            "alarm_count": len(rows),
            "meta": meta(request),
        }

    @app.post("/alarms/rationalization-candidates", dependencies=auth)
    async def rationalization(req: RationalizationRequest, request: Request) -> dict[str, Any]:
        rows = scope(req)
        cands = an.rationalization_candidates(rows, req.recurrence_threshold, req.stale_minutes_threshold, data.anchor)
        return {"candidates": cands, "candidate_count": len(cands), "meta": meta(request)}

    @app.post("/alarms/priority-score", dependencies=auth)
    async def priority(req: AlarmRef, request: Request) -> dict[str, Any]:
        alarm = get_alarm_or_404(req.alarm_id)
        asset = data.assets[alarm["asset_id"]]
        history = [a for a in data.alarms if a["asset_id"] == alarm["asset_id"]]
        return {**an.priority_score(alarm, asset, history, data.anchor), "meta": meta(request)}

    @app.post("/recommendations/operator-actions", dependencies=auth)
    async def operator_actions(req: RecommendationRequest, request: Request) -> dict[str, Any]:
        alarm = get_alarm_or_404(req.alarm_id)
        asset = data.assets.get(alarm["asset_id"])
        asset_alarms = [a for a in data.alarms if a["asset_id"] == alarm["asset_id"]]
        body = an.recommendations(
            alarm,
            asset,
            asset_alarms,
            include_related=req.include_related,
            include_asset_context=req.include_asset_context,
            include_historical_pattern=req.include_historical_pattern,
        )
        return {**body, "meta": meta(request)}

    @app.post("/calculation-code/generate", dependencies=auth)
    async def calc_generate(req: CalcGenerateRequest, request: Request) -> dict[str, Any]:
        spec = an.CALCULATIONS.get(req.calculation_type)
        if not spec:
            raise HTTPException(422, f"unsupported calculation_type; allowed: {sorted(an.CALCULATIONS)}")
        filters = req.filters.model_dump(mode="json")
        cid = an.calculation_id(req.calculation_type, filters)
        calcs[cid] = {"calculation_type": req.calculation_type, "filters": filters}
        return {
            "calculation_id": cid,
            "calculation_type": req.calculation_type,
            "description": spec["description"],
            "code": spec["code"],
            "language": "pseudo-python",
            "unit": spec["unit"],
            "meta": meta(request),
        }

    @app.post("/calculation-code/execute", dependencies=auth)
    async def calc_execute(req: CalcExecuteRequest, request: Request) -> dict[str, Any]:
        calc = calcs.get(req.calculation_id)
        if not calc:
            raise HTTPException(404, f"Calculation {req.calculation_id} not found; generate it first")
        f = req.filters
        start = f.start_time if f.start_time.tzinfo else f.start_time.replace(tzinfo=UTC)
        end = f.end_time if f.end_time.tzinfo else f.end_time.replace(tzinfo=UTC)
        rows = an.filter_alarms(data.alarms, site=f.site, unit=f.unit, start_time=start, end_time=end)
        value = an.run_calculation(calc["calculation_type"], rows, start, end)
        return {
            "calculation_id": req.calculation_id,
            "calculation_type": calc["calculation_type"],
            "result": {"value": value, "unit": an.CALCULATIONS[calc["calculation_type"]]["unit"], "alarm_count": len(rows)},
            "filters": f.model_dump(mode="json"),
            "meta": meta(request),
        }

    @app.get("/analytics/kpi-definitions", dependencies=auth)
    async def kpi_definitions(request: Request) -> dict[str, Any]:
        return {"kpis": an.KPI_DEFINITIONS, "meta": meta(request)}

    # ---- admin: fault injection --------------------------------------------------------
    @app.post("/_admin/faults", dependencies=auth)
    async def add_fault(spec: FaultSpec) -> dict[str, Any]:
        re.compile(spec.path_pattern)
        faults.append(spec.model_dump())
        return {"faults": faults}

    @app.delete("/_admin/faults", dependencies=auth)
    async def clear_faults() -> dict[str, Any]:
        faults.clear()
        return {"faults": faults}

    return app


app = create_app()
