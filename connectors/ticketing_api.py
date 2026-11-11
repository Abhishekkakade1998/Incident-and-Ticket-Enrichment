"""Typed connector for the (mock) ticketing system."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from connectors.http import CallStats, JsonHttpClient, RetryPolicy


class TicketingClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = JsonHttpClient(base_url, token, service="ticketing-api", timeout_s=timeout_s, retry=retry, transport=transport)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def list_tickets(
        self, *, asset_ids: list[str] | None = None, statuses: list[str] | None = None, site: str | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[dict[str, Any], CallStats]:
        params: list[tuple[str, Any]] = [("asset_id", a) for a in asset_ids or []]
        params += [("status", ",".join(statuses) if statuses else None), ("site", site), ("page", page), ("page_size", page_size)]
        return await self.http.request("GET", "/tickets", params=params)

    async def get_ticket(self, ticket_id: str) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("GET", f"/tickets/{quote(ticket_id, safe='')}")

    async def search_similar(self, body: dict[str, Any]) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/tickets/search-similar", json=body)

    async def create_ticket(self, body: dict[str, Any], idempotency_key: str) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", "/tickets", json=body, headers={"Idempotency-Key": idempotency_key})

    async def add_comment(self, ticket_id: str, author: str, body: str) -> tuple[dict[str, Any], CallStats]:
        return await self.http.request("POST", f"/tickets/{quote(ticket_id, safe='')}/comments", json={"author": author, "body": body})
