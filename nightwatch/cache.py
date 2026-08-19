"""SQLite response cache, keyed on a hash of the request body.

Exists from the first line of code for one reason: you will re-render this
map several hundred times while building the UI, and every one of those must
cost zero credits. It also makes the demo deterministic and instant, which
matters more than it sounds -- a judge watching a spinner forms a different
impression from one clicking a map that responds immediately.

The cache stores the full result plus the activity_id, so every number on
the final map can be traced back to the call that produced it. That
provenance is what makes the citation table in the README possible.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_PATH = Path("data/cache.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key           TEXT PRIMARY KEY,
    endpoint      TEXT NOT NULL,
    request_json  TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    activity_id   TEXT,
    fetched_at    REAL NOT NULL,
    latency_s     REAL
);
CREATE INDEX IF NOT EXISTS idx_endpoint ON responses(endpoint);
"""


def request_key(endpoint: str, payload: dict) -> str:
    blob = f"{endpoint}:{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
    return hashlib.sha256(blob.encode()).hexdigest()


class ResponseCache:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The harvester fans out across a thread pool, so the connection is
        # shared between threads. SQLite forbids that by default; allowing
        # it is safe here only because every access is serialised by the
        # lock below. Contention is irrelevant -- a cache lookup is
        # microseconds against a ~20s API call.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(_SCHEMA)
            # WAL lets readers proceed during a write, which matters once
            # several workers finish at once.
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.commit()

    def get(self, endpoint: str, payload: dict) -> Optional[Any]:
        key = request_key(endpoint, payload)
        with self._lock:
            row = self.conn.execute(
                "SELECT result_json FROM responses WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(
        self,
        endpoint: str,
        payload: dict,
        result: Any,
        activity_id: Optional[str] = None,
        latency_s: Optional[float] = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO responses "
                "(key, endpoint, request_json, result_json, activity_id, fetched_at, latency_s) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    request_key(endpoint, payload),
                    endpoint,
                    json.dumps(payload, sort_keys=True),
                    json.dumps(result),
                    activity_id,
                    time.time(),
                    latency_s,
                ),
            )
            self.conn.commit()

    def provenance(self) -> list:
        """Every call the project has made -- the citation table for the README."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT endpoint, activity_id, request_json, fetched_at, latency_s "
                "FROM responses ORDER BY fetched_at"
            ).fetchall()
        return [
            {
                "endpoint": r[0],
                "activity_id": r[1],
                "request": json.loads(r[2]),
                "fetched_at": r[3],
                "latency_s": r[4],
            }
            for r in rows
        ]

    def stats(self) -> dict:
        with self._lock:
            n, = self.conn.execute("SELECT COUNT(*) FROM responses").fetchone()
            by_ep = dict(
                self.conn.execute(
                    "SELECT endpoint, COUNT(*) FROM responses GROUP BY endpoint"
                ).fetchall()
            )
        return {"total_calls_cached": n, "by_endpoint": by_ep}

    def close(self) -> None:
        with self._lock:
            self.conn.close()
