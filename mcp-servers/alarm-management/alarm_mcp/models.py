"""Typed output contracts for the Alarm Management MCP tools.

Upstream responses are validated against these models before being returned, so a
malformed source-system payload becomes an ``invalid_upstream_response`` tool error
rather than silently corrupt data. ``extra="allow"`` keeps forward compatibility with
fields the simulator may add later.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from common.mcp_support import SourceCall

Severity = Literal["low", "medium", "high", "critical"]
AlarmStatus = Literal["active", "acknowledged", "cleared", "shelved"]


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ToolOutput(Open):
    trace_id: str | None = Field(default=None, description="Trace ID propagated to the source system")
    source_calls: list[SourceCall] = Field(default_factory=list, description="Upstream API calls made by this tool")


class AssetHit(Open):
    asset_id: str
    asset_name: str
    asset_type: str
    site: str
    unit: str
    tag: str | None = None
    criticality: str | None = None
    match_score: float | None = None


class AssetSearchOutput(ToolOutput):
    results: list[AssetHit]
    total: int
    query: str


class AssetMetadataOutput(ToolOutput):
    asset_id: str
    asset_name: str
    asset_type: str
    site: str
    unit: str
    criticality: str
    manufacturer: str | None = None
    model: str | None = None
    parent_system: str | None = None
    connected_assets: list[str] = Field(default_factory=list)
    maintenance: dict[str, Any] = Field(default_factory=dict)
    operating_limits: dict[str, Any] = Field(default_factory=dict)


class Alarm(Open):
    alarm_id: str
    asset_id: str
    asset_name: str
    site: str
    unit: str
    alarm_name: str
    alarm_type: str
    severity: Severity
    priority: int
    status: AlarmStatus
    start_time: str
    end_time: str | None = None
    ack_time: str | None = None
    ack_delay_seconds: int | None = None
    value: float | None = None
    threshold: float | None = None
    unit_of_measure: str | None = None
    description: str | None = None


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool


class AlarmListOutput(ToolOutput):
    alarms: list[Alarm]
    pagination: Pagination
    pages_fetched: int = 1
    truncated: bool = Field(default=False, description="True when more pages exist than max_pages allowed")


class AlarmOutput(ToolOutput):
    alarm: Alarm


class SummaryGroup(Open):
    kpis: dict[str, float | int | None]
    first_seen: str | None = None
    last_seen: str | None = None


class SummaryOutput(ToolOutput):
    groups: list[SummaryGroup]
    totals: dict[str, float | int | None]
    group_by: list[str]
    time_range: dict[str, str]


class TrendPoint(Open):
    bucket_start: str


class TrendsOutput(ToolOutput):
    bucket: str
    series: list[TrendPoint]
    time_range: dict[str, str]


class AlarmKey(BaseModel):
    asset_id: str
    asset_name: str | None = None
    alarm_name: str


class Correlation(BaseModel):
    leading_alarm: AlarmKey
    following_alarm: AlarmKey
    support: int
    confidence: float
    lift: float
    cross_asset: bool


class CorrelatedAsset(BaseModel):
    asset_id: str
    asset_name: str | None = None
    total_support: int


class CorrelationOutput(ToolOutput):
    correlations: list[Correlation]
    correlated_assets: list[CorrelatedAsset]
    anchor_alarm_count: int
    lag_window_minutes: int
    time_range: dict[str, str]


class FloodOutput(ToolOutput):
    flood_windows: list[dict[str, Any]]
    flood_count: int
    flood_time_percentage: float
    alarm_count: int


class RationalizationOutput(ToolOutput):
    candidates: list[dict[str, Any]]
    candidate_count: int


class PriorityOutput(ToolOutput):
    alarm_id: str
    priority_score: int
    priority_band: Literal["P1", "P2", "P3", "P4"]
    factors: dict[str, int]
    explanation: str


class RankedAlarm(BaseModel):
    alarm_id: str
    priority_score: int | None = None
    priority_band: str | None = None
    explanation: str | None = None
    error: str | None = None


class RankOutput(ToolOutput):
    ranked: list[RankedAlarm]
    top_alarm_id: str | None
    failures: int = 0


class Recommendation(BaseModel):
    step: int
    action: str
    confidence: float


class RecommendationsOutput(ToolOutput):
    alarm_id: str
    alarm_name: str
    asset_id: str
    recommendations: list[Recommendation]
    likely_causes: list[str]
    procedure_keywords: list[str] = Field(default_factory=list)
    related_alarms: list[dict[str, Any]] | None = None
    asset_context: dict[str, Any] | None = None
    historical_pattern: dict[str, Any] | None = None


class KpiOutput(ToolOutput):
    calculation_id: str
    calculation_type: str
    description: str
    value: float
    unit: str
    alarm_count: int
    filters: dict[str, Any]


class KpiDefinitionsOutput(ToolOutput):
    kpis: list[dict[str, Any]]
