"""Typed connector for the Alarm Management API (contract: postman collections)."""

from __future__ import annotations

from typing import Any

import httpx

from connectors.http import CallStats, JsonHttpClient, RetryPolicy


class AlarmApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = JsonHttpClient(base_url, token, service="alarm-api", timeout_s=timeout_s, retry=retry, transport=transport)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def search_assets(
        self, query: str, *, site: str | None = None, unit: str | None = None, asset_type: str | None = None, limit: int = 10, offset: int = 0
    ) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request(
            "GET", "/assets/search", params={"query": query, "site": site, "unit": unit, "asset_type": asset_type, "limit": limit, "offset": offset}
        )

    async def asset_metadata(self, asset_id: str) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("GET", f"/assets/{_seg(asset_id)}/metadata")

    async def list_alarms(self, **params: Any) -> tuple[dict[str, Any], CallStats]:
        for key in ("status", "severity"):
            if isinstance(params.get(key), list):
                params[key] = ",".join(params[key])
        return await self.http.request("GET", "/alarms", params=params)

    async def get_alarm(self, alarm_id: str) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("GET", f"/alarms/{_seg(alarm_id)}")

    async def summary(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/alarms/summary", json=body)

    async def trends(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/alarms/trends", json=body)

    async def correlation(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/alarms/correlation", json=body)

    async def flood_analysis(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/alarms/flood-analysis", json=body)

    async def rationalization_candidates(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/alarms/rationalization-candidates", json=body)

    async def priority_score(self, alarm_id: str) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/alarms/priority-score", json={"alarm_id": alarm_id})

    async def operator_recommendations(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/recommendations/operator-actions", json=body)

    async def generate_calculation(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/calculation-code/generate", json=body)

    async def execute_calculation(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/calculation-code/execute", json=body)

    async def kpi_definitions(self) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("GET", "/analytics/kpi-definitions")


def _seg(value: str) -> str:
    """Encode a path segment so user-provided IDs cannot alter the request path."""
    from urllib.parse import quote

    return quote(value, safe="")
