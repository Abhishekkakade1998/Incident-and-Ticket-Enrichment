"""Alarm Management MCP server.

Exposes the Alarm Management API as typed MCP tools over Streamable HTTP. The copilot
never sees the source-system token: it authenticates to this server with its own MCP
bearer token, and this server authenticates to the Alarm API with ``ALARM_API_TOKEN``.

Run standalone:  ``python -m alarm_mcp``  (see README "Run the MCP server independently").
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from alarm_mcp import models as m
from common.mcp_support import CallRecorder, run_tool, tool_error
from common.tracing import current_trace
from connectors.alarm_api import AlarmApiClient
from connectors.http import RetryPolicy

SERVER_NAME = "alarm-management"

AssetId = Annotated[str, Field(pattern=r"^AST-[A-Z]{3}-\d{3}$", description="Asset identifier, e.g. AST-BFP-101")]
AlarmId = Annotated[str, Field(pattern=r"^ALM-\d{6}$", description="Alarm identifier, e.g. ALM-000123")]
IsoTime = Annotated[str | None, Field(default=None, description="ISO-8601 UTC timestamp, e.g. 2026-08-01T00:00:00Z")]
Lookback = Annotated[int, Field(default=90, ge=1, le=365, description="Used when start_time is omitted: window = now - lookback_days")]
SeverityT = Literal["low", "medium", "high", "critical"]
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)


class AlarmMcpSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alarm_api_base_url: str = "http://localhost:8000"
    alarm_api_token: str = "demo-token"
    alarm_mcp_token: str | None = None  # bearer token clients must present to this MCP server
    alarm_mcp_host: str = "127.0.0.1"
    alarm_mcp_port: int = 9001
    source_timeout_s: float = 8.0
    source_max_retries: int = 2
    tool_timeout_s: float = 25.0
    max_pages: int = 10
    log_level: str = "INFO"


def resolve_range(start_time: str | None, end_time: str | None, lookback_days: int) -> dict[str, str]:
    def parse(v: str, name: str) -> datetime:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise tool_error("invalid_arguments", f"{name} must be ISO-8601, got '{v}'") from exc
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    end = parse(end_time, "end_time") if end_time else datetime.now(UTC)
    start = parse(start_time, "start_time") if start_time else end - timedelta(days=lookback_days)
    if end <= start:
        raise tool_error("invalid_arguments", "end_time must be after start_time")
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {"start_time": start.strftime(fmt), "end_time": end.strftime(fmt)}


def _scope(asset_ids: list[str] | None, site: str | None, unit: str | None) -> dict[str, Any]:
    if not (asset_ids or site or unit):
        raise tool_error("invalid_arguments", "Provide at least one of asset_ids, site or unit to scope the query")
    out: dict[str, Any] = {}
    if asset_ids:
        out["asset_ids"] = asset_ids
    if site:
        out["site"] = site
    if unit:
        out["unit"] = unit
    return out


def _tid() -> str | None:
    t = current_trace()
    return t.trace_id if t else None


def build_server(settings: AlarmMcpSettings | None = None, client: AlarmApiClient | None = None) -> FastMCP:
    s = settings or AlarmMcpSettings()
    api = client or AlarmApiClient(
        s.alarm_api_base_url,
        s.alarm_api_token,
        timeout_s=s.source_timeout_s,
        retry=RetryPolicy(max_retries=s.source_max_retries),
    )
    mcp = FastMCP(
        SERVER_NAME,
        instructions=(
            "Tools for the Alarm Management system: resolve assets, retrieve and page alarms, "
            "summarise/trend/correlate alarms, score priority and fetch operator recommendations. "
            "All tools are read-only."
        ),
        host=s.alarm_mcp_host,
        port=s.alarm_mcp_port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def run(tool: str, ctx: Context | None, body: Any) -> Any:
        return await run_tool(SERVER_NAME, tool, ctx, body, timeout_s=s.tool_timeout_s)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": SERVER_NAME})

    # ------------------------------------------------------------------ assets
    @mcp.tool(title="Search assets", annotations=READ_ONLY)
    async def search_assets(
        query: Annotated[str, Field(min_length=1, max_length=120, description="Asset name, tag, type or ID fragment")],
        site: Annotated[str | None, Field(description="Filter by site, e.g. EastRefinery")] = None,
        unit: Annotated[str | None, Field(description="Filter by unit, e.g. 'Unit 5'")] = None,
        asset_type: Annotated[str | None, Field(description="pump, compressor, motor, ...")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        ctx: Context | None = None,
    ) -> m.AssetSearchOutput:
        """Resolve a natural-language asset reference (name, tag, type) to asset IDs. Use this first when the user names equipment."""

        async def body(rec: CallRecorder) -> m.AssetSearchOutput:
            data = await rec.call(api.search_assets(query, site=site, unit=unit, asset_type=asset_type, limit=limit))
            return m.AssetSearchOutput(results=data["results"], total=data["total"], query=query, trace_id=_tid(), source_calls=rec.calls)

        return await run("search_assets", ctx, body)

    @mcp.tool(title="Get asset metadata", annotations=READ_ONLY)
    async def get_asset_metadata(asset_id: AssetId, ctx: Context | None = None) -> m.AssetMetadataOutput:
        """Asset context: criticality, manufacturer/model, parent system, connected assets, maintenance and operating limits."""

        async def body(rec: CallRecorder) -> m.AssetMetadataOutput:
            data = await rec.call(api.asset_metadata(asset_id))
            data.pop("meta", None)
            return m.AssetMetadataOutput(**data, trace_id=_tid(), source_calls=rec.calls)

        return await run("get_asset_metadata", ctx, body)

    # ------------------------------------------------------------------ alarms
    @mcp.tool(title="List alarms", annotations=READ_ONLY)
    async def list_alarms(
        asset_id: Annotated[str | None, Field(pattern=r"^AST-[A-Z]{3}-\d{3}$")] = None,
        site: str | None = None,
        unit: str | None = None,
        status: Annotated[list[Literal["active", "acknowledged", "cleared", "shelved"]] | None, Field(description="Filter statuses")] = None,
        severity: list[SeverityT] | None = None,
        alarm_name: str | None = None,
        start_time: IsoTime = None,
        end_time: IsoTime = None,
        sort_by: Literal["start_time", "severity", "priority", "asset_name"] = "start_time",
        sort_order: Literal["asc", "desc"] = "desc",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=200)] = 50,
        fetch_all_pages: Annotated[bool, Field(description="Follow pagination until exhausted or max_pages reached")] = False,
        max_pages: Annotated[int, Field(ge=1, le=20)] = 5,
        ctx: Context | None = None,
    ) -> m.AlarmListOutput:
        """Retrieve alarm activations with filters, sorting and pagination. Set fetch_all_pages to aggregate multiple pages."""
        if not (asset_id or site or unit):
            raise tool_error("invalid_arguments", "Provide asset_id, site or unit")

        async def body(rec: CallRecorder) -> m.AlarmListOutput:
            params: dict[str, Any] = {
                "asset_id": asset_id,
                "site": site,
                "unit": unit,
                "status": status,
                "severity": severity,
                "alarm_name": alarm_name,
                "start_time": start_time,
                "end_time": end_time,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
                "page_size": page_size,
            }
            data = await rec.call(api.list_alarms(**params))
            alarms = list(data["data"])
            pagination = data["pagination"]
            pages = 1
            limit_pages = min(max_pages, s.max_pages)
            while fetch_all_pages and pagination["has_next"] and pages < limit_pages:
                params["page"] = pagination["page"] + 1
                data = await rec.call(api.list_alarms(**dict(params)))
                alarms.extend(data["data"])
                pagination = data["pagination"]
                pages += 1
            return m.AlarmListOutput(
                alarms=alarms,
                pagination=pagination,
                pages_fetched=pages,
                truncated=bool(pagination["has_next"] and fetch_all_pages),
                trace_id=_tid(),
                source_calls=rec.calls,
            )

        return await run("list_alarms", ctx, body)

    @mcp.tool(title="Get alarm", annotations=READ_ONLY)
    async def get_alarm(alarm_id: AlarmId, ctx: Context | None = None) -> m.AlarmOutput:
        """Fetch a single alarm activation by ID."""

        async def body(rec: CallRecorder) -> m.AlarmOutput:
            data = await rec.call(api.get_alarm(alarm_id))
            data.pop("meta", None)
            return m.AlarmOutput(alarm=data, trace_id=_tid(), source_calls=rec.calls)

        return await run("get_alarm", ctx, body)

    @mcp.tool(title="Alarm summary", annotations=READ_ONLY)
    async def get_alarm_summary(
        asset_ids: Annotated[list[str] | None, Field(max_length=50)] = None,
        site: str | None = None,
        unit: str | None = None,
        start_time: IsoTime = None,
        end_time: IsoTime = None,
        lookback_days: Lookback = 90,
        severity: list[SeverityT] | None = None,
        alarm_types: list[Literal["process", "safety", "device", "system"]] | None = None,
        group_by: Annotated[
            list[Literal["alarm_name", "asset_id", "asset_name", "severity", "alarm_type", "site", "unit", "status", "priority"]],
            Field(min_length=1, max_length=4),
        ] = ["alarm_name"],  # noqa: B006
        kpis: Annotated[
            list[Literal["alarm_count", "critical_count", "recurring_rate", "avg_ack_delay", "avg_duration", "suppression_candidate_rate", "active_count"]],
            Field(min_length=1),
        ] = ["alarm_count", "recurring_rate", "avg_ack_delay"],  # noqa: B006
        ctx: Context | None = None,
    ) -> m.SummaryOutput:
        """Aggregate alarms into groups with KPIs (counts, recurrence rate, acknowledgement delay). Use to find recurring alarms."""
        scope = _scope(asset_ids, site, unit)
        tr = resolve_range(start_time, end_time, lookback_days)

        async def body(rec: CallRecorder) -> m.SummaryOutput:
            req = {**scope, "time_range": tr, "group_by": group_by, "kpis": kpis}
            if severity:
                req["severity"] = severity
            if alarm_types:
                req["alarm_types"] = alarm_types
            data = await rec.call(api.summary(req))
            return m.SummaryOutput(
                groups=data["groups"], totals=data["totals"], group_by=[str(g) for g in group_by], time_range=tr, trace_id=_tid(), source_calls=rec.calls
            )

        return await run("get_alarm_summary", ctx, body)

    @mcp.tool(title="Alarm trends", annotations=READ_ONLY)
    async def get_alarm_trends(
        asset_ids: Annotated[list[str] | None, Field(max_length=50)] = None,
        site: str | None = None,
        unit: str | None = None,
        start_time: IsoTime = None,
        end_time: IsoTime = None,
        lookback_days: Lookback = 90,
        bucket: Literal["hourly", "daily", "weekly"] = "weekly",
        metrics: list[Literal["alarm_count", "critical_count", "avg_ack_delay", "recurring_rate"]] = ["alarm_count"],  # noqa: B006
        severity: list[SeverityT] | None = None,
        ctx: Context | None = None,
    ) -> m.TrendsOutput:
        """Time-bucketed alarm metrics to show whether a problem is getting better or worse."""
        scope = _scope(asset_ids, site, unit)
        tr = resolve_range(start_time, end_time, lookback_days)

        async def body(rec: CallRecorder) -> m.TrendsOutput:
            req = {**scope, "time_range": tr, "bucket": bucket, "metrics": metrics}
            if severity:
                req["severity"] = severity
            data = await rec.call(api.trends(req))
            return m.TrendsOutput(bucket=bucket, series=data["series"], time_range=tr, trace_id=_tid(), source_calls=rec.calls)

        return await run("get_alarm_trends", ctx, body)

    # ------------------------------------------------------------------ advanced analytics
    @mcp.tool(title="Correlate alarms", annotations=READ_ONLY)
    async def correlate_alarms(
        asset_ids: Annotated[list[str], Field(min_length=1, max_length=20)],
        start_time: IsoTime = None,
        end_time: IsoTime = None,
        lookback_days: Lookback = 90,
        lag_window_minutes: Annotated[int, Field(ge=1, le=240)] = 15,
        severity_threshold: SeverityT = "medium",
        min_support: Annotated[int, Field(ge=1, le=1000)] = 2,
        ctx: Context | None = None,
    ) -> m.CorrelationOutput:
        """Find alarms that co-occur (B follows A within the lag window), including on other assets in the same unit. Returns correlated assets."""
        tr = resolve_range(start_time, end_time, lookback_days)

        async def body(rec: CallRecorder) -> m.CorrelationOutput:
            req = {
                "asset_ids": asset_ids,
                "time_range": tr,
                "correlation_method": "cooccurrence",
                "lag_window_minutes": lag_window_minutes,
                "severity_threshold": severity_threshold,
                "min_support": min_support,
            }
            data = await rec.call(api.correlation(req))
            return m.CorrelationOutput(
                correlations=data["correlations"],
                correlated_assets=data["correlated_assets"],
                anchor_alarm_count=data["anchor_alarm_count"],
                lag_window_minutes=lag_window_minutes,
                time_range=tr,
                trace_id=_tid(),
                source_calls=rec.calls,
            )

        return await run("correlate_alarms", ctx, body)

    @mcp.tool(title="Flood analysis", annotations=READ_ONLY)
    async def analyze_alarm_floods(
        site: str | None = None,
        unit: str | None = None,
        start_time: IsoTime = None,
        end_time: IsoTime = None,
        lookback_days: Lookback = 90,
        threshold_count: Annotated[int, Field(ge=2, le=1000)] = 10,
        rolling_window_minutes: Annotated[int, Field(ge=1, le=120)] = 10,
        ctx: Context | None = None,
    ) -> m.FloodOutput:
        """Detect alarm floods (>= threshold_count alarms in a rolling window) for a site or unit."""
        if not (site or unit):
            raise tool_error("invalid_arguments", "Provide site or unit")
        tr = resolve_range(start_time, end_time, lookback_days)

        async def body(rec: CallRecorder) -> m.FloodOutput:
            req: dict[str, Any] = {"time_range": tr, "threshold_count": threshold_count, "rolling_window_minutes": rolling_window_minutes}
            if site:
                req["site"] = site
            if unit:
                req["unit"] = unit
            data = await rec.call(api.flood_analysis(req))
            data.pop("meta", None)
            return m.FloodOutput(**data, trace_id=_tid(), source_calls=rec.calls)

        return await run("analyze_alarm_floods", ctx, body)

    @mcp.tool(title="Rationalization candidates", annotations=READ_ONLY)
    async def get_rationalization_candidates(
        asset_ids: list[str] | None = None,
        site: str | None = None,
        unit: str | None = None,
        start_time: IsoTime = None,
        end_time: IsoTime = None,
        lookback_days: Lookback = 90,
        recurrence_threshold: Annotated[int, Field(ge=1)] = 5,
        stale_minutes_threshold: Annotated[int, Field(ge=1)] = 180,
        ctx: Context | None = None,
    ) -> m.RationalizationOutput:
        """List frequent, chattering or stale alarms that are candidates for rationalization."""
        scope = _scope(asset_ids, site, unit)
        tr = resolve_range(start_time, end_time, lookback_days)

        async def body(rec: CallRecorder) -> m.RationalizationOutput:
            req = {**scope, "time_range": tr, "recurrence_threshold": recurrence_threshold, "stale_minutes_threshold": stale_minutes_threshold}
            data = await rec.call(api.rationalization_candidates(req))
            return m.RationalizationOutput(candidates=data["candidates"], candidate_count=data["candidate_count"], trace_id=_tid(), source_calls=rec.calls)

        return await run("get_rationalization_candidates", ctx, body)

    @mcp.tool(title="Score alarm priority", annotations=READ_ONLY)
    async def score_alarm_priority(alarm_id: AlarmId, ctx: Context | None = None) -> m.PriorityOutput:
        """Compute a 0-100 priority score and P1-P4 band for one alarm, with the contributing factors."""

        async def body(rec: CallRecorder) -> m.PriorityOutput:
            data = await rec.call(api.priority_score(alarm_id))
            data.pop("meta", None)
            return m.PriorityOutput(**data, trace_id=_tid(), source_calls=rec.calls)

        return await run("score_alarm_priority", ctx, body)

    @mcp.tool(title="Rank alarms by priority", annotations=READ_ONLY)
    async def rank_alarms_by_priority(
        alarm_ids: Annotated[list[Annotated[str, Field(pattern=r"^ALM-\d{6}$")]], Field(min_length=1, max_length=25)],
        ctx: Context | None = None,
    ) -> m.RankOutput:
        """Score several alarms concurrently and return them ranked (highest priority first). Partial failures are reported per alarm."""

        async def body(rec: CallRecorder) -> m.RankOutput:
            async def one(aid: str) -> m.RankedAlarm:
                try:
                    d = await rec.call(api.priority_score(aid))
                    return m.RankedAlarm(alarm_id=aid, priority_score=d["priority_score"], priority_band=d["priority_band"], explanation=d.get("explanation"))
                except Exception as exc:  # noqa: BLE001 - partial failure is reported, not raised
                    return m.RankedAlarm(alarm_id=aid, error=getattr(exc, "code", type(exc).__name__))

            ranked = await asyncio.gather(*(one(a) for a in dict.fromkeys(alarm_ids)))
            ok = sorted([r for r in ranked if r.priority_score is not None], key=lambda r: -(r.priority_score or 0))
            failed = [r for r in ranked if r.priority_score is None]
            if not ok:
                raise tool_error("upstream_unavailable", "Priority scoring failed for every alarm", retryable=True)
            return m.RankOutput(ranked=ok + failed, top_alarm_id=ok[0].alarm_id, failures=len(failed), trace_id=_tid(), source_calls=rec.calls)

        return await run("rank_alarms_by_priority", ctx, body)

    @mcp.tool(title="Operator recommendations", annotations=READ_ONLY)
    async def get_operator_recommendations(
        alarm_id: AlarmId,
        include_related: bool = True,
        include_asset_context: bool = True,
        include_historical_pattern: bool = True,
        ctx: Context | None = None,
    ) -> m.RecommendationsOutput:
        """Recommended operator actions, likely causes and procedure keywords for an alarm, plus related alarms and history."""

        async def body(rec: CallRecorder) -> m.RecommendationsOutput:
            data = await rec.call(
                api.operator_recommendations(
                    {
                        "alarm_id": alarm_id,
                        "include_related": include_related,
                        "include_asset_context": include_asset_context,
                        "include_historical_pattern": include_historical_pattern,
                    }
                )
            )
            data.pop("meta", None)
            return m.RecommendationsOutput(**data, trace_id=_tid(), source_calls=rec.calls)

        return await run("get_operator_recommendations", ctx, body)

    @mcp.tool(title="Compute KPI", annotations=READ_ONLY)
    async def compute_alarm_kpi(
        calculation_type: Literal["alarm_flood_index", "critical_alarm_density", "operator_response_efficiency", "nuisance_alarm_score"],
        site: str | None = None,
        unit: str | None = None,
        start_time: IsoTime = None,
        end_time: IsoTime = None,
        lookback_days: Lookback = 90,
        ctx: Context | None = None,
    ) -> m.KpiOutput:
        """Generate and execute a KPI calculation (two chained API calls: generate -> execute)."""
        if not (site or unit):
            raise tool_error("invalid_arguments", "Provide site or unit")
        tr = resolve_range(start_time, end_time, lookback_days)

        async def body(rec: CallRecorder) -> m.KpiOutput:
            filters = {k: v for k, v in {"site": site, "unit": unit, **tr}.items() if v}
            gen = await rec.call(api.generate_calculation({"calculation_type": calculation_type, "filters": filters}))
            res = await rec.call(api.execute_calculation({"calculation_id": gen["calculation_id"], "filters": filters}))
            return m.KpiOutput(
                calculation_id=gen["calculation_id"],
                calculation_type=calculation_type,
                description=gen["description"],
                value=res["result"]["value"],
                unit=res["result"]["unit"],
                alarm_count=res["result"]["alarm_count"],
                filters=filters,
                trace_id=_tid(),
                source_calls=rec.calls,
            )

        return await run("compute_alarm_kpi", ctx, body)

    @mcp.tool(title="KPI definitions", annotations=READ_ONLY)
    async def list_kpi_definitions(ctx: Context | None = None) -> m.KpiDefinitionsOutput:
        """List supported alarm KPIs and calculations with definitions."""

        async def body(rec: CallRecorder) -> m.KpiDefinitionsOutput:
            data = await rec.call(api.kpi_definitions())
            return m.KpiDefinitionsOutput(kpis=data["kpis"], trace_id=_tid(), source_calls=rec.calls)

        return await run("list_kpi_definitions", ctx, body)

    return mcp
