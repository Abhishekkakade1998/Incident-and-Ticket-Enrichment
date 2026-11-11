"""Structured JSON logging with secret redaction.

Every service logs one JSON object per line. Values whose key looks like a secret
(token, password, api_key, authorization, ...) are masked, and bearer tokens that
appear inside free text are scrubbed as well, so a stray f-string can never leak
credentials into the log stream.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from common.tracing import current_trace

SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|signing[_-]?key|credential)",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-~+/=]+", re.IGNORECASE)
REDACTED = "***REDACTED***"


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively mask secret-looking keys and bearer tokens in strings."""
    if _depth > 8:
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and SECRET_KEY_PATTERN.search(k) and not k.endswith("_present"):
                out[k] = REDACTED
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return BEARER_PATTERN.sub(r"\1" + REDACTED, value)
    return value


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON with trace context attached."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        trace = current_trace()
        if trace:
            payload.update({k: v for k, v in trace.as_log_fields().items() if v})
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Uvicorn access logs would print query strings; keep them but at WARNING to reduce noise.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log a structured event: ``log_event(log, "mcp.tool.call", tool="x", duration_ms=3)``."""
    logger.log(level, msg, extra={"fields": fields})
