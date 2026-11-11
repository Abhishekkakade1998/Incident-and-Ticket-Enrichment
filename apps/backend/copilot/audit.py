"""Append-only audit trail in SQLite (parameterised queries only)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from common.logging import redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    conversation_id TEXT,
    trace_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_conv ON audit_events(conversation_id, ts);
"""


class AuditLog:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def record(self, action: str, *, actor: str, conversation_id: str | None = None, trace_id: str | None = None, **detail: Any) -> None:
        blob = json.dumps(redact(detail), default=str)[:20000]
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_events (ts, conversation_id, trace_id, actor, action, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), conversation_id, trace_id, actor[:120], action[:80], blob),
            )
            self._conn.commit()

    def list(self, conversation_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            if conversation_id:
                rows = self._conn.execute(
                    "SELECT id, ts, conversation_id, trace_id, actor, action, detail FROM audit_events WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                    (conversation_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, ts, conversation_id, trace_id, actor, action, detail FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [{"id": r[0], "ts": r[1], "conversation_id": r[2], "trace_id": r[3], "actor": r[4], "action": r[5], "detail": json.loads(r[6])} for r in rows]
