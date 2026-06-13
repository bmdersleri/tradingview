"""SQLite-backed token bucket limiter."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class BucketState:
    tokens: float
    updated_at: float


class SQLiteTokenBucket:
    _initialized_paths: set[str] = set()

    def __init__(
        self,
        path: Path,
        *,
        capacity: float = 1.0,
        refill_per_second: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)

        path_str = str(self.path.resolve())
        if path_str not in SQLiteTokenBucket._initialized_paths:
            self._ensure_schema()
            SQLiteTokenBucket._initialized_paths.add(path_str)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    bucket TEXT PRIMARY KEY,
                    tokens REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _load(self, conn: sqlite3.Connection, bucket: str) -> BucketState:
        row = conn.execute(
            "SELECT tokens, updated_at FROM tokens WHERE bucket = ?",
            (bucket,),
        ).fetchone()
        now = self.clock()
        if row is None:
            return BucketState(tokens=self.capacity, updated_at=now)
        elapsed = max(0.0, now - float(row["updated_at"]))
        replenished = min(
            self.capacity,
            float(row["tokens"]) + elapsed * self.refill_per_second,
        )
        return BucketState(tokens=replenished, updated_at=now)

    def allow(self, bucket: str, *, cost: float = 1.0) -> bool:
        with closing(self._connect()) as conn:
            state = self._load(conn, bucket)
            if state.tokens < cost:
                conn.execute(
                    """
                    INSERT INTO tokens(bucket, tokens, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(bucket) DO UPDATE SET
                        tokens = excluded.tokens,
                        updated_at = excluded.updated_at
                    """,
                    (bucket, state.tokens, state.updated_at),
                )
                conn.commit()
                return False
            state.tokens -= cost
            conn.execute(
                """
                INSERT INTO tokens(bucket, tokens, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(bucket) DO UPDATE SET
                    tokens = excluded.tokens,
                    updated_at = excluded.updated_at
                """,
                (bucket, state.tokens, state.updated_at),
            )
            conn.commit()
            return True
