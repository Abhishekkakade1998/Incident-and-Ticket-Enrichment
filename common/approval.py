"""Human-approval tokens for write operations.

The copilot backend mints a token only when a user explicitly confirms a ticket draft in
the GUI. The token is an HMAC over the *exact* payload being written plus an expiry, so:

* the planner / LLM cannot create tickets on its own (it never holds the signing key path),
* a token cannot be replayed for a different or edited payload,
* tokens expire (default 10 minutes).

The ticketing MCP server verifies the token before calling the ticketing API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

VERSION = "v1"


def canonical_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of a JSON payload (sorted keys, no whitespace)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def mint(payload: dict[str, Any], key: str, *, ttl_s: int = 600, now: float | None = None) -> str:
    expiry = int((now or time.time()) + ttl_s)
    digest = canonical_hash(payload)
    sig = hmac.new(key.encode(), f"{VERSION}.{expiry}.{digest}".encode(), hashlib.sha256).hexdigest()
    return f"{VERSION}.{expiry}.{sig}"


class ApprovalError(ValueError):
    pass


def verify(token: str, payload: dict[str, Any], key: str, *, now: float | None = None) -> str:
    """Return the payload hash if valid, else raise ``ApprovalError``."""
    try:
        version, expiry_s, sig = token.split(".")
        expiry = int(expiry_s)
    except (ValueError, AttributeError) as exc:
        raise ApprovalError("malformed approval token") from exc
    if version != VERSION:
        raise ApprovalError("unsupported approval token version")
    if (now or time.time()) > expiry:
        raise ApprovalError("approval token expired")
    digest = canonical_hash(payload)
    expected = hmac.new(key.encode(), f"{VERSION}.{expiry}.{digest}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ApprovalError("approval token does not match this payload")
    return digest
