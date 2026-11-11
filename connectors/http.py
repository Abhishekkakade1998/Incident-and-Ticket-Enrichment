"""Resilient JSON HTTP client shared by all source-system connectors.

Responsibilities:
* bearer authentication (token never logged; see ``common.logging.redact``)
* trace header propagation from the active ``TraceContext``
* per-request timeout
* bounded retries with exponential backoff + jitter on transient failures
  (connect errors, timeouts, 429, 502, 503, 504)
* mapping of HTTP failures onto a small, stable ``SourceError`` taxonomy
* per-call telemetry (status code, attempts, duration) for the MCP execution trace
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from common.logging import log_event
from common.tracing import current_trace

log = logging.getLogger("connectors.http")

RETRYABLE_STATUS = {429, 502, 503, 504}


class SourceError(Exception):
    """Error raised by a connector, safe to surface to MCP clients (no secrets)."""

    code = "upstream_error"
    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None, details: Any = None, attempts: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details
        self.attempts = attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "details": self.details,
        }


class AuthError(SourceError):
    code = "auth_failed"


class NotFoundError(SourceError):
    code = "not_found"


class ValidationFailed(SourceError):
    code = "invalid_request"


class RateLimited(SourceError):
    code = "rate_limited"
    retryable = True


class UpstreamUnavailable(SourceError):
    code = "upstream_unavailable"
    retryable = True


class UpstreamTimeout(SourceError):
    code = "timeout"
    retryable = True


def map_status(status: int, message: str, details: Any, attempts: int) -> SourceError:
    cls: type[SourceError]
    if status in (401, 403):
        cls = AuthError
        message = "Authentication with the source system failed" + (" (forbidden)" if status == 403 else "")
    elif status == 404:
        cls = NotFoundError
    elif status in (400, 409, 422):
        cls = ValidationFailed
    elif status == 429:
        cls = RateLimited
    elif status >= 500:
        cls = UpstreamUnavailable
    else:
        cls = SourceError
    return cls(message, status_code=status, details=details, attempts=attempts)


@dataclass
class CallStats:
    method: str
    path: str
    status_code: int | None = None
    attempts: int = 0
    duration_ms: float = 0.0
    retries: list[str] = field(default_factory=list)

    @property
    def retry_count(self) -> int:
        return max(0, self.attempts - 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "attempts": self.attempts,
            "retry_count": self.retry_count,
            "duration_ms": round(self.duration_ms, 1),
            "retry_reasons": self.retries,
        }


@dataclass
class RetryPolicy:
    max_retries: int = 2
    backoff_base_s: float = 0.2
    backoff_max_s: float = 2.0

    def delay(self, attempt: int) -> float:
        return min(self.backoff_max_s, self.backoff_base_s * (2 ** (attempt - 1))) * (0.5 + random.random() / 2)


class JsonHttpClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        service: str,
        timeout_s: float = 10.0,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.service = service
        self._token = token
        self.timeout_s = timeout_s
        self.retry = retry or RetryPolicy()
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)
        self.last_stats: CallStats | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        trace = current_trace()
        if trace:
            headers.update(trace.to_headers())
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> tuple[Any, CallStats]:
        stats = CallStats(method=method, path=path)
        self.last_stats = stats
        started = time.perf_counter()
        attempt = 0
        clean_params = _clean_params(params)
        while True:
            attempt += 1
            stats.attempts = attempt
            try:
                resp = await self._client.request(
                    method,
                    path,
                    params=clean_params,
                    json=json,
                    headers=self._headers(headers),
                    timeout=timeout_s or self.timeout_s,
                )
            except httpx.TimeoutException:
                err: SourceError = UpstreamTimeout(f"{self.service} did not respond within {timeout_s or self.timeout_s:.1f}s", attempts=attempt)
            except httpx.TransportError as exc:
                err = UpstreamUnavailable(f"{self.service} is unreachable ({type(exc).__name__})", attempts=attempt)
            else:
                stats.status_code = resp.status_code
                if resp.status_code < 400:
                    stats.duration_ms = (time.perf_counter() - started) * 1000
                    self._log(stats, "ok")
                    return (resp.json() if resp.content else None), stats
                message, details = _error_body(resp)
                err = map_status(resp.status_code, message, details, attempt)
                if resp.status_code not in RETRYABLE_STATUS:
                    stats.duration_ms = (time.perf_counter() - started) * 1000
                    self._log(stats, err.code)
                    raise err
            if attempt > self.retry.max_retries:
                stats.duration_ms = (time.perf_counter() - started) * 1000
                err.attempts = attempt
                self._log(stats, err.code)
                raise err
            stats.retries.append(err.code)
            await asyncio.sleep(self.retry.delay(attempt))

    def _log(self, stats: CallStats, outcome: str) -> None:
        log_event(
            log,
            "source.call",
            level=logging.INFO if outcome == "ok" else logging.WARNING,
            source=self.service,
            outcome=outcome,
            **stats.as_dict(),
        )


def _clean_params(params: dict[str, Any] | list[tuple[str, Any]] | None) -> list[tuple[str, Any]] | None:
    if params is None:
        return None
    items = params.items() if isinstance(params, dict) else params
    out: list[tuple[str, Any]] = []
    for k, v in items:
        if v is None:
            continue
        if isinstance(v, bool):
            v = str(v).lower()
        out.append((k, v))
    return out


def _error_body(resp: httpx.Response) -> tuple[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}", None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message") or f"HTTP {resp.status_code}"), body["error"].get("details")
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"]), None
    return f"HTTP {resp.status_code}", None
