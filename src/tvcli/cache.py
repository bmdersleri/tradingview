"""SQLite-backed TTL cache."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CacheStats:
    entries: int
    hits: int
    misses: int
    expired: int


class SQLiteTTLCache:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()):
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        return conn

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires_at = self.clock() + ttl_seconds
        encoded = json.dumps(value)
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO cache(key, value, expires_at, hits)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    expires_at = excluded.expires_at
                """,
                (key, encoded, expires_at),
            )
            conn.commit()

    def get(self, key: str) -> Any | None:
        now = self.clock()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT value, expires_at, hits FROM cache WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return None
            conn.execute(
                "UPDATE cache SET hits = hits + 1 WHERE key = ?",
                (key,),
            )
            conn.commit()
            return json.loads(row["value"])

    def purge(self) -> int:
        now = self.clock()
        with closing(self._connect()) as conn:
            result = conn.execute(
                "DELETE FROM cache WHERE expires_at <= ?",
                (now,),
            )
            conn.commit()
            return result.rowcount

    def clear(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()

    def stats(self) -> dict[str, int | str]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS entries,
                    COALESCE(SUM(hits), 0) AS hits
                FROM cache
                """
            ).fetchone()
            return {
                "path": str(self.path),
                "entries": int(row["entries"]),
                "hits": int(row["hits"]),
                "misses": 0,
                "expired": self.purge(),
            }
