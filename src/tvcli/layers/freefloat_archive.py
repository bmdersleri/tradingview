"""Persistent archive and report builder for VAP free-float data."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from ..cache import SQLiteTTLCache
from ..config import default_archive_path, default_cache_path
from ..errors import NotFoundError, RateLimitedError, UsageError
from ..ratelimit import SQLiteTokenBucket
from . import freefloat

_SOURCE = "VAP / MKK"
_LOW_FLOAT_RISK = 20.0
_SEVERE_LOW_FLOAT_RISK = 10.0
_RATIO_JUMP = 5.0
_FLOAT_SHARES_JUMP_PCT = 10.0
_EXTREME_LOOKBACK = 252
_SYNC_SOURCE_VAP = "vap"
_MIN_SYNC_INTERVAL = timedelta(minutes=10)
# Default delay between per-day requests during a backfill. Keeps a single
# resumable session polite to VAP without the 10-minute per-day cooldown that
# only guards separate sync sessions.
_DEFAULT_RATE_SECONDS = 20.0


def _sleep(seconds: float) -> None:
    """Indirection seam so backfill throttle is instant under tests."""
    if seconds > 0:
        time.sleep(seconds)


def _iso_day(value: date) -> str:
    return value.isoformat()


def _label_to_iso(label: str) -> str:
    return datetime.strptime(label, "%d.%m.%Y").date().isoformat()


def _pct_delta(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def _trend_direction(values: list[float]) -> str:
    if len(values) < 2:
        return "flat"
    deltas = [cur - prev for prev, cur in zip(values[:-1], values[1:], strict=True)]
    avg_abs = fmean(abs(delta) for delta in deltas)
    net = values[-1] - values[0]
    if avg_abs > 0 and abs(net) < avg_abs:
        return "volatile"
    if net > 0:
        return "rising"
    if net < 0:
        return "falling"
    return "flat"


class ArchiveStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path or default_archive_path()
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=UTC)
        return current.astimezone(UTC)

    def _now_iso(self) -> str:
        return self._now().isoformat()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that commits on clean exit and always closes.

        sqlite3's own ``with conn`` only manages the transaction, leaving the
        connection open (ResourceWarning). This wraps both concerns.
        """
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS freefloat_reports (
                    report_date TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    published_label TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS freefloat_snapshots (
                    report_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    isin TEXT NOT NULL,
                    name TEXT NOT NULL,
                    float_shares REAL NOT NULL,
                    capital REAL NOT NULL,
                    ratio REAL NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (report_date, code)
                );

                CREATE INDEX IF NOT EXISTS idx_freefloat_snapshots_code_date
                ON freefloat_snapshots(code, report_date);

                CREATE INDEX IF NOT EXISTS idx_freefloat_snapshots_isin_date
                ON freefloat_snapshots(isin, report_date);

                CREATE TABLE IF NOT EXISTS freefloat_changes (
                    report_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    previous_report_date TEXT,
                    ratio_delta REAL,
                    ratio_delta_pct REAL,
                    float_shares_delta REAL,
                    float_shares_delta_pct REAL,
                    capital_delta REAL,
                    capital_delta_pct REAL,
                    PRIMARY KEY (report_date, code)
                );

                CREATE TABLE IF NOT EXISTS freefloat_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    metric_value REAL,
                    threshold_value REAL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_freefloat_events_code_date
                ON freefloat_events(code, report_date);

                CREATE TABLE IF NOT EXISTS freefloat_symbol_summary (
                    code TEXT PRIMARY KEY,
                    isin TEXT NOT NULL,
                    name TEXT NOT NULL,
                    first_report_date TEXT NOT NULL,
                    last_report_date TEXT NOT NULL,
                    last_ratio REAL NOT NULL,
                    last_float_shares REAL NOT NULL,
                    last_capital REAL NOT NULL,
                    report_count INTEGER NOT NULL,
                    min_ratio REAL NOT NULL,
                    max_ratio REAL NOT NULL,
                    avg_ratio REAL NOT NULL,
                    ratio_volatility REAL NOT NULL,
                    last_change_direction TEXT NOT NULL,
                    risk_flags_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    source TEXT PRIMARY KEY,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    last_report_date TEXT,
                    cooldown_until TEXT,
                    last_status TEXT NOT NULL,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS freefloat_missing (
                    report_date TEXT PRIMARY KEY,
                    checked_at TEXT NOT NULL
                );
                """
            )

    def _parse_ts(self, value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def has_report_date(self, report_date: date) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM freefloat_reports WHERE report_date = ?",
                (_iso_day(report_date),),
            ).fetchone()
        return row is not None

    def is_known_empty(self, report_date: date) -> bool:
        """True if this date was already checked and had no published report."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM freefloat_missing WHERE report_date = ?",
                (_iso_day(report_date),),
            ).fetchone()
        return row is not None

    def mark_empty(self, report_date: date) -> None:
        """Record that a date has no published report (weekend/holiday)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO freefloat_missing(report_date, checked_at)
                VALUES(?, ?)
                ON CONFLICT(report_date) DO UPDATE SET checked_at = excluded.checked_at
                """,
                (_iso_day(report_date), self._now_iso()),
            )

    def sync_records(
        self,
        records: tuple[freefloat.FloatRecord, ...],
        *,
        fetched_at: str | None = None,
    ) -> dict[str, Any]:
        if not records:
            raise UsageError(
                "Cannot sync an empty free-float report.",
                hint="Fetch a published VAP report first.",
            )
        fetched_at = fetched_at or self._now_iso()
        report_date = _label_to_iso(records[0].date)
        payload = json.dumps([asdict(row) for row in records], sort_keys=True).encode(
            "utf-8"
        )
        digest = sha256(payload).hexdigest()
        touched = {row.code for row in records}

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO freefloat_reports(
                    report_date, source, published_label, row_count,
                    content_sha256, fetched_at, synced_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_date) DO UPDATE SET
                    source = excluded.source,
                    published_label = excluded.published_label,
                    row_count = excluded.row_count,
                    content_sha256 = excluded.content_sha256,
                    fetched_at = excluded.fetched_at,
                    synced_at = excluded.synced_at
                """,
                (
                    report_date,
                    _SOURCE,
                    records[0].date,
                    len(records),
                    digest,
                    fetched_at,
                    self._now_iso(),
                ),
            )
            conn.execute(
                "DELETE FROM freefloat_snapshots WHERE report_date = ?",
                (report_date,),
            )
            conn.executemany(
                """
                INSERT INTO freefloat_snapshots(
                    report_date, code, isin, name, float_shares, capital, ratio, source
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        report_date,
                        row.code,
                        row.isin,
                        row.name,
                        row.float_shares,
                        row.capital,
                        row.ratio,
                        _SOURCE,
                    )
                    for row in records
                ],
            )
            for code in touched:
                self._rebuild_symbol(conn, code)

        return {
            "report_date": report_date,
            "published_label": records[0].date,
            "row_count": len(records),
            "touched_symbols": len(touched),
            "content_sha256": digest,
        }

    def _rebuild_symbol(self, conn: sqlite3.Connection, code: str) -> None:
        rows = conn.execute(
            """
            SELECT report_date, code, isin, name, float_shares, capital, ratio, source
            FROM freefloat_snapshots
            WHERE code = ?
            ORDER BY report_date
            """,
            (code,),
        ).fetchall()
        conn.execute("DELETE FROM freefloat_changes WHERE code = ?", (code,))
        conn.execute("DELETE FROM freefloat_events WHERE code = ?", (code,))
        conn.execute("DELETE FROM freefloat_symbol_summary WHERE code = ?", (code,))
        if not rows:
            return

        previous: sqlite3.Row | None = None
        for row in rows:
            ratio_delta = float_delta = capital_delta = None
            ratio_delta_pct = float_delta_pct = capital_delta_pct = None
            previous_date = None
            if previous is not None:
                previous_date = str(previous["report_date"])
                ratio_delta = float(row["ratio"]) - float(previous["ratio"])
                ratio_delta_pct = _pct_delta(
                    float(row["ratio"]), float(previous["ratio"])
                )
                float_delta = float(row["float_shares"]) - float(
                    previous["float_shares"]
                )
                float_delta_pct = _pct_delta(
                    float(row["float_shares"]), float(previous["float_shares"])
                )
                capital_delta = float(row["capital"]) - float(previous["capital"])
                capital_delta_pct = _pct_delta(
                    float(row["capital"]), float(previous["capital"])
                )
            conn.execute(
                """
                INSERT INTO freefloat_changes(
                    report_date, code, previous_report_date,
                    ratio_delta, ratio_delta_pct,
                    float_shares_delta, float_shares_delta_pct,
                    capital_delta, capital_delta_pct
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["report_date"],
                    code,
                    previous_date,
                    ratio_delta,
                    ratio_delta_pct,
                    float_delta,
                    float_delta_pct,
                    capital_delta,
                    capital_delta_pct,
                ),
            )
            for event in self._build_events(rows, row, previous):
                conn.execute(
                    """
                    INSERT INTO freefloat_events(
                        report_date, code, event_type, severity,
                        metric_value, threshold_value, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["report_date"],
                        code,
                        event["event_type"],
                        event["severity"],
                        event["metric_value"],
                        event["threshold_value"],
                        json.dumps(event["payload"], sort_keys=True),
                    ),
                )
            previous = row

        ratios = [float(row["ratio"]) for row in rows]
        last = rows[-1]
        flags = []
        if float(last["ratio"]) < _LOW_FLOAT_RISK:
            flags.append("low_float")
        if float(last["ratio"]) < _SEVERE_LOW_FLOAT_RISK:
            flags.append("severe_low_float")
        last_direction = _trend_direction(ratios[-20:])
        conn.execute(
            """
            INSERT INTO freefloat_symbol_summary(
                code, isin, name, first_report_date, last_report_date, last_ratio,
                last_float_shares, last_capital, report_count, min_ratio, max_ratio,
                avg_ratio, ratio_volatility, last_change_direction, risk_flags_json,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                last["isin"],
                last["name"],
                rows[0]["report_date"],
                last["report_date"],
                last["ratio"],
                last["float_shares"],
                last["capital"],
                len(rows),
                min(ratios),
                max(ratios),
                fmean(ratios),
                0.0 if len(ratios) < 2 else pstdev(ratios),
                last_direction,
                json.dumps(flags),
                self._now_iso(),
            ),
        )

    def _build_events(
        self,
        rows: list[sqlite3.Row],
        current: sqlite3.Row,
        previous: sqlite3.Row | None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        ratio = float(current["ratio"])
        if ratio < _LOW_FLOAT_RISK:
            severity = "high" if ratio < _SEVERE_LOW_FLOAT_RISK else "medium"
            events.append(
                {
                    "event_type": "liquidity_risk_low_float",
                    "severity": severity,
                    "metric_value": ratio,
                    "threshold_value": _LOW_FLOAT_RISK,
                    "payload": {"ratio": ratio},
                }
            )
        recent = [
            float(row["ratio"])
            for row in rows
            if row["report_date"] <= current["report_date"]
        ][-_EXTREME_LOOKBACK:]
        if ratio >= max(recent):
            events.append(
                {
                    "event_type": "new_52w_high_ratio",
                    "severity": "medium",
                    "metric_value": ratio,
                    "threshold_value": max(recent),
                    "payload": {"ratio": ratio, "window": len(recent)},
                }
            )
        if ratio <= min(recent):
            events.append(
                {
                    "event_type": "new_52w_low_ratio",
                    "severity": "medium",
                    "metric_value": ratio,
                    "threshold_value": min(recent),
                    "payload": {"ratio": ratio, "window": len(recent)},
                }
            )
        if previous is None:
            return events

        previous_ratio = float(previous["ratio"])
        previous_float = float(previous["float_shares"])
        previous_capital = float(previous["capital"])
        delta = ratio - previous_ratio
        if abs(delta) >= _RATIO_JUMP:
            events.append(
                {
                    "event_type": ("ratio_jump_up" if delta > 0 else "ratio_jump_down"),
                    "severity": "high",
                    "metric_value": delta,
                    "threshold_value": _RATIO_JUMP,
                    "payload": {"delta": delta, "ratio": ratio},
                }
            )
        if previous_ratio >= _LOW_FLOAT_RISK > ratio:
            events.append(
                {
                    "event_type": "ratio_threshold_cross_down",
                    "severity": "high",
                    "metric_value": ratio,
                    "threshold_value": _LOW_FLOAT_RISK,
                    "payload": {"from": previous_ratio, "to": ratio},
                }
            )
        if previous_ratio < _LOW_FLOAT_RISK <= ratio:
            events.append(
                {
                    "event_type": "ratio_threshold_cross_up",
                    "severity": "medium",
                    "metric_value": ratio,
                    "threshold_value": _LOW_FLOAT_RISK,
                    "payload": {"from": previous_ratio, "to": ratio},
                }
            )
        float_pct = _pct_delta(float(current["float_shares"]), previous_float)
        if float_pct is not None and abs(float_pct) >= _FLOAT_SHARES_JUMP_PCT:
            events.append(
                {
                    "event_type": (
                        "float_shares_jump_up"
                        if float(current["float_shares"]) > previous_float
                        else "float_shares_jump_down"
                    ),
                    "severity": "high",
                    "metric_value": float_pct,
                    "threshold_value": _FLOAT_SHARES_JUMP_PCT,
                    "payload": {"pct": float_pct},
                }
            )
        if not math.isclose(float(current["capital"]), previous_capital):
            events.append(
                {
                    "event_type": "capital_change_detected",
                    "severity": "medium",
                    "metric_value": float(current["capital"]) - previous_capital,
                    "threshold_value": 0.0,
                    "payload": {
                        "from": previous_capital,
                        "to": float(current["capital"]),
                    },
                }
            )
        return events

    def archive_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            reports = conn.execute(
                "SELECT COUNT(*) AS count FROM freefloat_reports"
            ).fetchone()
            snapshots = conn.execute(
                "SELECT COUNT(*) AS count FROM freefloat_snapshots"
            ).fetchone()
            symbols = conn.execute(
                "SELECT COUNT(*) AS count FROM freefloat_symbol_summary"
            ).fetchone()
            bounds = conn.execute(
                """
                SELECT MIN(report_date) AS min_date, MAX(report_date) AS max_date
                FROM freefloat_reports
                """
            ).fetchone()
            sync = conn.execute(
                """
                SELECT source, last_attempt_at, last_success_at, last_report_date,
                       cooldown_until, last_status, last_error
                FROM sync_state
                ORDER BY source
                """
            ).fetchall()
        return {
            "path": str(self.path),
            "reports": int(reports["count"]),
            "snapshots": int(snapshots["count"]),
            "symbols": int(symbols["count"]),
            "first_report_date": bounds["min_date"],
            "last_report_date": bounds["max_date"],
            "sync_state": [dict(row) for row in sync],
        }

    def claim_sync_slot(
        self,
        source: str,
        *,
        min_interval: timedelta = _MIN_SYNC_INTERVAL,
    ) -> dict[str, Any]:
        now = self._now()
        cooldown_until = now + min_interval
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_state WHERE source = ?",
                (source,),
            ).fetchone()
            active_until = self._parse_ts(
                None if row is None else str(row["cooldown_until"])
            )
            if active_until is not None and active_until > now:
                raise RateLimitedError(
                    f"{source.upper()} sync cooldown is active until "
                    f"{active_until.isoformat()}.",
                    hint="Wait at least 10 minutes between upstream sync attempts.",
                )
            conn.execute(
                """
                INSERT INTO sync_state(
                    source, last_attempt_at, last_success_at, last_report_date,
                    cooldown_until, last_status, last_error
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    cooldown_until = excluded.cooldown_until,
                    last_status = excluded.last_status,
                    last_error = excluded.last_error
                """,
                (
                    source,
                    now.isoformat(),
                    None if row is None else row["last_success_at"],
                    None if row is None else row["last_report_date"],
                    cooldown_until.isoformat(),
                    "running",
                    None,
                ),
            )
        return {
            "source": source,
            "last_attempt_at": now.isoformat(),
            "cooldown_until": cooldown_until.isoformat(),
        }

    def complete_sync_slot(
        self,
        source: str,
        *,
        status: str,
        report_date: str | None = None,
        error: str | None = None,
    ) -> None:
        now = self._now_iso()
        with self._connect() as conn:
            current = conn.execute(
                "SELECT last_report_date FROM sync_state WHERE source = ?",
                (source,),
            ).fetchone()
            last_report_date = report_date
            if last_report_date is None and current is not None:
                last_report_date = current["last_report_date"]
            conn.execute(
                """
                UPDATE sync_state
                SET last_success_at = CASE
                        WHEN ? = 'success' THEN ?
                        ELSE last_success_at
                    END,
                    last_report_date = ?,
                    last_status = ?,
                    last_error = ?
                WHERE source = ?
                """,
                (
                    status,
                    now,
                    last_report_date,
                    status,
                    error,
                    source,
                ),
            )

    def read_snapshot(
        self, code: str, report_date: date | None = None
    ) -> freefloat.FloatRecord | None:
        """One symbol's archived record for a date (or its latest), or None."""
        normalized = freefloat.normalize_code(code)
        with self._connect() as conn:
            if report_date is None:
                row = conn.execute(
                    """
                    SELECT report_date, code, isin, name, float_shares, capital, ratio
                    FROM freefloat_snapshots
                    WHERE code = ?
                    ORDER BY report_date DESC
                    LIMIT 1
                    """,
                    (normalized,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT report_date, code, isin, name, float_shares, capital, ratio
                    FROM freefloat_snapshots
                    WHERE code = ? AND report_date = ?
                    """,
                    (normalized, _iso_day(report_date)),
                ).fetchone()
        if row is None:
            return None
        # FloatRecord.date is the published label (DD.MM.YYYY); reconstruct it
        # from the ISO report_date so the shape matches a freshly fetched record.
        label = datetime.fromisoformat(str(row["report_date"])).strftime("%d.%m.%Y")
        return freefloat.FloatRecord(
            code=str(row["code"]),
            isin=str(row["isin"]),
            name=str(row["name"]),
            float_shares=float(row["float_shares"]),
            capital=float(row["capital"]),
            ratio=float(row["ratio"]),
            date=label,
        )

    def symbol_history(self, symbol: str, *, limit: int = 100) -> list[dict[str, Any]]:
        code = freefloat.normalize_code(symbol)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.report_date, s.code, s.isin, s.name, s.float_shares, s.capital,
                       s.ratio, c.previous_report_date, c.ratio_delta,
                       c.ratio_delta_pct, c.float_shares_delta, c.float_shares_delta_pct
                FROM freefloat_snapshots s
                LEFT JOIN freefloat_changes c
                  ON c.report_date = s.report_date AND c.code = s.code
                WHERE s.code = ?
                ORDER BY s.report_date DESC
                LIMIT ?
                """,
                (code, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def symbol_events(
        self,
        symbol: str | None = None,
        *,
        limit: int = 100,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        args: list[Any] = []
        where: list[str] = []
        if symbol is not None:
            where.append("code = ?")
            args.append(freefloat.normalize_code(symbol))
        if severity is not None:
            where.append("severity = ?")
            args.append(severity)
        sql = """
            SELECT report_date, code, event_type, severity,
                   metric_value, threshold_value, payload_json
            FROM freefloat_events
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY report_date DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(str(event.pop("payload_json")))
            events.append(event)
        return events

    def latest_risk_events(self, symbol: str) -> list[dict[str, Any]]:
        """Events recorded at the symbol's most recent archived report (local only).

        Used by the signal layer as float-side risk context. Returns an empty list
        when the symbol is unknown or its latest report carried no events.
        """
        code = freefloat.normalize_code(symbol)
        with self._connect() as conn:
            latest = conn.execute(
                "SELECT MAX(report_date) AS d FROM freefloat_snapshots WHERE code = ?",
                (code,),
            ).fetchone()
            if latest is None or latest["d"] is None:
                return []
            rows = conn.execute(
                """
                SELECT report_date, code, event_type, severity,
                       metric_value, threshold_value, payload_json
                FROM freefloat_events
                WHERE code = ? AND report_date = ?
                ORDER BY id DESC
                """,
                (code, str(latest["d"])),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            # Replace the raw JSON column with its parsed form; callers consume
            # `payload`, not the stringified column.
            event["payload"] = json.loads(str(event.pop("payload_json")))
            events.append(event)
        return events

    def build_symbol_report(self, symbol: str, *, limit: int = 20) -> dict[str, Any]:
        code = freefloat.normalize_code(symbol)
        with self._connect() as conn:
            summary = conn.execute(
                "SELECT * FROM freefloat_symbol_summary WHERE code = ?",
                (code,),
            ).fetchone()
            latest = conn.execute(
                """
                SELECT
                    report_date, code, isin, name, float_shares, capital, ratio, source
                FROM freefloat_snapshots
                WHERE code = ?
                ORDER BY report_date DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        if summary is None or latest is None:
            raise NotFoundError(
                f"No archived free-float history for '{code}'.",
                hint="Run `tvcli data float sync` first.",
            )
        history = self.symbol_history(code, limit=limit)
        events = self.symbol_events(code, limit=limit)
        ratios = [row["ratio"] for row in reversed(history)]
        total_change = None
        total_change_pct = None
        if len(history) >= 2:
            oldest = history[-1]
            newest = history[0]
            total_change = float(newest["ratio"]) - float(oldest["ratio"])
            total_change_pct = _pct_delta(
                float(newest["ratio"]), float(oldest["ratio"])
            )
        return {
            "symbol": code,
            "identity": {
                "code": latest["code"],
                "isin": latest["isin"],
                "name": latest["name"],
            },
            "latest": {
                "report_date": latest["report_date"],
                "ratio": latest["ratio"],
                "float_shares": latest["float_shares"],
                "capital": latest["capital"],
                "source": latest["source"],
            },
            "summary": {
                "report_count": summary["report_count"],
                "first_report_date": summary["first_report_date"],
                "last_report_date": summary["last_report_date"],
                "min_ratio": summary["min_ratio"],
                "max_ratio": summary["max_ratio"],
                "avg_ratio": summary["avg_ratio"],
                "ratio_volatility": summary["ratio_volatility"],
                "last_change_direction": summary["last_change_direction"],
                "risk_flags": json.loads(str(summary["risk_flags_json"])),
            },
            "trend": {
                "direction": _trend_direction([float(v) for v in ratios]),
                "lookback_reports": len(history),
                "ratio_change_total": total_change,
                "ratio_change_pct_total": total_change_pct,
                "rolling_5_report_avg": None
                if len(ratios) < 5
                else fmean(float(v) for v in ratios[-5:]),
                "rolling_20_report_avg": None
                if len(ratios) < 20
                else fmean(float(v) for v in ratios[-20:]),
            },
            "recent_changes": history[:limit],
            "events": events[:limit],
            "risk": {
                "low_float": float(latest["ratio"]) < _LOW_FLOAT_RISK,
                "severe_low_float": float(latest["ratio"]) < _SEVERE_LOW_FLOAT_RISK,
                "note": (
                    "Low free-float can increase liquidity and manipulation risk."
                    if float(latest["ratio"]) < _LOW_FLOAT_RISK
                    else None
                ),
            },
        }


def _iter_days(
    *,
    since: date | None,
    until: date,
    max_days: int | None,
) -> Iterable[date]:
    current = until
    emitted = 0
    while True:
        if since is not None and current < since:
            return
        if max_days is not None and emitted >= max_days:
            return
        yield current
        current -= timedelta(days=1)
        emitted += 1


def sync_archive(
    *,
    latest: bool,
    since: date | None,
    until: date | None,
    max_days: int | None,
    resume: bool,
    rate_seconds: float = _DEFAULT_RATE_SECONDS,
    store: ArchiveStore | None = None,
) -> dict[str, Any]:
    store = store or ArchiveStore()
    synced: list[dict[str, Any]] = []
    skipped = 0
    missing = 0

    if latest:
        claim = store.claim_sync_slot(_SYNC_SOURCE_VAP)
        try:
            records = freefloat.fetch_report(None)
            result = store.sync_records(records)
        except NotFoundError as error:
            store.complete_sync_slot(
                _SYNC_SOURCE_VAP,
                status="not_found",
                error=error.message,
            )
            raise
        except Exception as error:
            message = getattr(error, "message", str(error))
            store.complete_sync_slot(
                _SYNC_SOURCE_VAP,
                status="error",
                error=message,
            )
            raise
        store.complete_sync_slot(
            _SYNC_SOURCE_VAP,
            status="success",
            report_date=result["report_date"],
        )
        return {
            "mode": "latest",
            "synced_reports": 1,
            "skipped_reports": 0,
            "missing_dates": 0,
            "reports": [result],
            "sync": claim,
        }

    if since is None and max_days is None:
        max_days = 30
    stop = until or (datetime.now(tz=UTC).date() - timedelta(days=1))

    # One cooldown claim per backfill session (not per day); inside the session a
    # gentle inter-request throttle keeps us polite while staying resumable.
    claim = store.claim_sync_slot(_SYNC_SOURCE_VAP)

    # A backfill walks hundreds of days at its own ``--rate-seconds`` pace, so it
    # must NOT ride the interactive 5-per-10-minute bucket that guards ad-hoc
    # `data float` lookups — that bucket would starve after 5 fetches and abort
    # the whole run. Give the backfill its own bucket whose refill matches the
    # request cadence (one token per ``rate_seconds``), with headroom so the
    # paced loop never trips it. ``_sleep`` remains the real throttle.
    cache = SQLiteTTLCache(default_cache_path())
    backfill_throttle = SQLiteTokenBucket(
        default_cache_path(),
        capacity=max(8.0, rate_seconds),
        refill_per_second=(1.0 / rate_seconds) if rate_seconds > 0 else 1.0,
        clock=lambda: store._now().timestamp(),
    )

    attempted = False
    last_report_date: str | None = None
    fetched_in_session = 0
    try:
        for candidate in _iter_days(since=since, until=stop, max_days=max_days):
            if resume and (
                store.has_report_date(candidate) or store.is_known_empty(candidate)
            ):
                skipped += 1
                continue
            # Throttle only between actual network fetches within the session.
            if fetched_in_session:
                _sleep(rate_seconds)
            attempted = True
            try:
                records = freefloat.fetch_report(
                    candidate, cache=cache, throttle=backfill_throttle
                )
            except NotFoundError:
                # Weekend/holiday: stamp it so a future --resume run skips it
                # instead of re-downloading the same empty day.
                store.mark_empty(candidate)
                missing += 1
                fetched_in_session += 1
                continue
            result = store.sync_records(records)
            synced.append(result)
            last_report_date = result["report_date"]
            fetched_in_session += 1
    except Exception as error:
        message = getattr(error, "message", str(error))
        store.complete_sync_slot(_SYNC_SOURCE_VAP, status="error", error=message)
        raise

    store.complete_sync_slot(
        _SYNC_SOURCE_VAP,
        status="success" if synced else "no_data",
        report_date=last_report_date,
    )

    return {
        "mode": "range",
        "synced_reports": len(synced),
        "skipped_reports": skipped,
        "missing_dates": missing,
        "reports": synced,
        "sync": claim,
        "attempted_fetch": attempted,
    }
