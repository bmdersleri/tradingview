# ruff: noqa: E501
"""Persistent archive and report builder for VAP free-float data."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
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
from ..config import default_archive_path, default_cache_path, default_data_dir
from ..errors import NotFoundError, RateLimitedError, UsageError
from ..logging_utils import setup_logger
from ..ratelimit import SQLiteTokenBucket
from . import freefloat

logger = setup_logger("tvcli.archive")

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
    _initialized_paths: set[str] = set()
    _write_lock = threading.Lock()

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path or default_archive_path()
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self.path.parent.mkdir(parents=True, exist_ok=True)

        path_str = str(self.path.resolve())
        if path_str not in ArchiveStore._initialized_paths:
            self._ensure_schema()
            ArchiveStore._initialized_paths.add(path_str)

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=UTC)
        return current.astimezone(UTC)

    def _now_iso(self) -> str:
        return self._now().isoformat()

    @contextmanager
    def _connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection that commits on clean exit and always closes.

        sqlite3's own ``with conn`` only manages the transaction, leaving the
        connection open (ResourceWarning). This wraps both concerns.
        """
        if write:
            ArchiveStore._write_lock.acquire()
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE;")
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            conn.close()
            if write:
                ArchiveStore._write_lock.release()

    def _ensure_schema(self) -> None:
        with self._connect(write=True) as conn:
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
                    payload_json TEXT NOT NULL,
                    status TEXT DEFAULT 'sent'
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

                CREATE TABLE IF NOT EXISTS freefloat_symbol_metadata (
                    code TEXT PRIMARY KEY,
                    sector TEXT,
                    industry TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kap_disclosures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    disclosure_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    url TEXT,
                    fetched_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_kap_disclosures_code_date
                ON kap_disclosures(code, disclosure_date);
                """
            )
            try:
                conn.execute(
                    "ALTER TABLE freefloat_events ADD COLUMN status TEXT DEFAULT 'sent'"
                )
            except sqlite3.OperationalError:
                pass

    def backup(self, target_path: Path) -> None:
        """Safely backup the SQLite database to target_path using SQLite Backup API."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as src_conn:
            dst_conn = sqlite3.connect(target_path)
            try:
                with dst_conn:
                    src_conn.backup(dst_conn)
            finally:
                dst_conn.close()

    def restore(self, source_path: Path) -> None:
        """Safely restore the SQLite database from source_path using SQLite Backup API."""
        if not source_path.exists():
            raise FileNotFoundError(f"Backup file not found: {source_path}")
        src_conn = sqlite3.connect(source_path)
        try:
            with self._connect() as dst_conn:
                src_conn.backup(dst_conn)
        finally:
            src_conn.close()

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
        with self._connect(write=True) as conn:
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

        with self._connect(write=True) as conn:
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

        # Dispatch real-time alerts if this synced date is the latest one
        try:
            with self._connect() as conn:
                max_row = conn.execute(
                    "SELECT MAX(report_date) FROM freefloat_reports"
                ).fetchone()
                max_date = max_row[0] if max_row else None
            if max_date and report_date >= max_date:
                self.dispatch_alerts(report_date)
        except Exception:
            pass

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
        # Compare against everything STRICTLY BEFORE this report, so a "new
        # extreme" means the ratio broke past every prior value — not merely
        # equalled itself. The earlier `ratio >= max` / `ratio <= min` over a
        # window that *included* the current point fired both branches at once
        # whenever the series was flat (max == min == ratio), spamming a
        # high+low pair on every report (e.g. ENPRA's constant 0.122%). The
        # strict `>`/`<` plus `elif` make the two mutually exclusive, and an
        # empty prior window (first-ever observation) emits neither.
        prior = [
            float(row["ratio"])
            for row in rows
            if row["report_date"] < current["report_date"]
        ][-_EXTREME_LOOKBACK:]
        if prior and ratio > max(prior):
            events.append(
                {
                    "event_type": "new_52w_high_ratio",
                    "severity": "medium",
                    "metric_value": ratio,
                    "threshold_value": max(prior),
                    "payload": {"ratio": ratio, "window": len(prior)},
                }
            )
        elif prior and ratio < min(prior):
            events.append(
                {
                    "event_type": "new_52w_low_ratio",
                    "severity": "medium",
                    "metric_value": ratio,
                    "threshold_value": min(prior),
                    "payload": {"ratio": ratio, "window": len(prior)},
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

    def ratio_percentile(
        self, code: str, report_date: date | None = None
    ) -> dict[str, Any]:
        """Where a symbol's free-float ratio ranks among all symbols on one report.

        A raw ratio (e.g. 0.12%) is meaningless without peers; this places it in
        the cross-section of every symbol present on the same report. ``rank`` is
        1-based ascending (1 = lowest float = thinnest), ``percentile`` is the
        fraction of symbols at or below this ratio (0..100).
        """
        normalized = freefloat.normalize_code(code)
        with self._connect() as conn:
            if report_date is None:
                row = conn.execute(
                    """
                    SELECT report_date, ratio FROM freefloat_snapshots
                    WHERE code = ? ORDER BY report_date DESC LIMIT 1
                    """,
                    (normalized,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT report_date, ratio FROM freefloat_snapshots "
                    "WHERE code = ? AND report_date = ?",
                    (normalized, _iso_day(report_date)),
                ).fetchone()
            if row is None:
                raise NotFoundError(
                    f"No archived free-float snapshot for '{normalized}'.",
                    hint="Run `tvcli data float-sync` first.",
                )
            day = str(row["report_date"])
            ratio = float(row["ratio"])
            agg = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN ratio < ? THEN 1 ELSE 0 END) AS lower,
                    SUM(CASE WHEN ratio <= ? THEN 1 ELSE 0 END) AS at_or_below
                FROM freefloat_snapshots WHERE report_date = ?
                """,
                (ratio, ratio, day),
            ).fetchone()
        total = int(agg["total"])
        lower = int(agg["lower"] or 0)
        at_or_below = int(agg["at_or_below"] or 0)
        return {
            "report_date": day,
            "ratio": ratio,
            "rank": lower + 1,  # 1-based ascending; 1 = thinnest float
            "total": total,
            "lower_count": lower,
            "percentile": round(100.0 * at_or_below / total, 2) if total else None,
        }

    def missing_business_days(self, since: date, until: date) -> list[date]:
        """Business days in [since, until] not covered by the archive.

        A day is covered if it has a stored report OR a known-empty stamp.
        Weekends are skipped. Only true gaps (never attempted) are returned.
        """
        gaps: list[date] = []
        current = since
        while current <= until:
            if current.weekday() < 5:  # Mon–Fri
                if not self.has_report_date(current) and not self.is_known_empty(
                    current
                ):
                    gaps.append(current)
            current = date.fromordinal(current.toordinal() + 1)
        return gaps

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
            "percentile": self.ratio_percentile(code),
            "liquidity": freefloat.liquidity_score(
                freefloat.FloatRecord(
                    code=str(latest["code"]),
                    isin=str(latest["isin"]),
                    name=str(latest["name"]),
                    float_shares=float(latest["float_shares"]),
                    capital=float(latest["capital"]),
                    ratio=float(latest["ratio"]),
                    date=str(latest["report_date"]),
                )
            ),
        }

    def update_symbol_metadata(self) -> None:
        import sys

        if "pytest" in sys.modules:
            return
        try:
            from tradingview_screener import Query  # type: ignore[attr-defined]

            res = (
                Query()
                .set_markets("turkey")
                .select("name", "sector", "industry")
                .limit(1000)
                .get_scanner_data()
            )
            if not res or len(res) < 2:
                return
            df = res[1]
            now_iso = self._now_iso()
            with self._connect() as conn:
                for _, row in df.iterrows():
                    ticker = str(row["ticker"])
                    code = ticker.split(":")[-1] if ":" in ticker else ticker
                    sector = str(row["sector"]) if row["sector"] else "Bilinmeyen"
                    industry = str(row["industry"]) if row["industry"] else "Bilinmeyen"
                    conn.execute(
                        """
                        INSERT INTO freefloat_symbol_metadata(code, sector, industry, updated_at)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(code) DO UPDATE SET
                            sector = excluded.sector,
                            industry = excluded.industry,
                            updated_at = excluded.updated_at
                        """,
                        (code, sector, industry, now_iso),
                    )
        except Exception:
            pass

    def get_sector_heatmap(self, report_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM freefloat_symbol_metadata"
            ).fetchone()[0]
        if count == 0:
            self.update_symbol_metadata()

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.code, s.name, s.ratio, s.capital, s.float_shares,
                       COALESCE(m.sector, 'Diğer') AS sector,
                       COALESCE(m.industry, 'Diğer') AS industry
                FROM freefloat_snapshots s
                LEFT JOIN freefloat_symbol_metadata m ON s.code = m.code
                WHERE s.report_date = ?
                """,
                (report_date,),
            ).fetchall()

        by_sector: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            sec = r["sector"]
            if sec not in by_sector:
                by_sector[sec] = []
            by_sector[sec].append(
                {
                    "code": r["code"],
                    "name": r["name"],
                    "ratio": float(r["ratio"]),
                    "capital": float(r["capital"]),
                    "float_shares": float(r["float_shares"]),
                    "industry": r["industry"],
                }
            )

        import statistics

        heatmap = []
        for sec, symbols in by_sector.items():
            ratios = [s["ratio"] for s in symbols]
            median_ratio = statistics.median(ratios) if ratios else 0.0
            avg_ratio = statistics.mean(ratios) if ratios else 0.0
            for s in symbols:
                s["weight"] = s["capital"] * (s["ratio"] / 100.0)
            symbols.sort(key=lambda x: x["weight"], reverse=True)
            heatmap.append(
                {
                    "sector": sec,
                    "median_ratio": round(median_ratio, 2),
                    "avg_ratio": round(avg_ratio, 2),
                    "symbol_count": len(symbols),
                    "symbols": symbols,
                }
            )
        heatmap.sort(key=lambda x: x["sector"])
        return heatmap

    def dispatch_alerts(self, report_date: str) -> None:
        import sys

        if "pytest" in sys.modules:
            return
        from ..config import load_config, resolve_setting

        try:
            cfg = load_config()
        except Exception:
            cfg = {}

        telegram_token = resolve_setting("alerts", "telegram-token", cfg)
        telegram_chat_id = resolve_setting("alerts", "telegram-chat-id", cfg)
        webhook_url = resolve_setting("alerts", "webhook-url", cfg)

        if not (telegram_token and telegram_chat_id) and not webhook_url:
            return

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT report_date, code, event_type, severity, metric_value, threshold_value, payload_json
                FROM freefloat_events
                WHERE report_date = ? AND severity IN ('high', 'medium')
                """,
                (report_date,),
            ).fetchall()

        if not rows:
            return

        events = []
        for r in rows:
            ev = dict(r)
            ev["payload"] = json.loads(str(ev.pop("payload_json")))
            events.append(ev)

        if telegram_token and telegram_chat_id:
            try:
                self._send_telegram_alerts(
                    telegram_token, telegram_chat_id, report_date, events
                )
            except Exception:
                pass

        if webhook_url:
            try:
                self._send_webhook_alerts(webhook_url, report_date, events)
            except Exception:
                pass

    def _send_telegram_alerts(
        self, token: str, chat_id: str, report_date: str, events: list[dict[str, Any]]
    ) -> None:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        messages = []
        for ev in events:
            code = ev["code"]
            etype = ev["event_type"]
            val = ev["metric_value"]
            payload = ev["payload"]

            if etype == "liquidity_risk_low_float":
                msg = f"⚠️ <b>LİKİDİTE RİSKİ ({code}):</b> Fiili dolaşım oranı %{val:.2f} ile kritik eşik altında!"
            elif etype == "new_52w_high_ratio":
                msg = f"📈 <b>52 HAFTALIK ZİRVE ({code}):</b> Fiili dolaşım oranı %{val:.2f} ile zirveye ulaştı."
            elif etype == "new_52w_low_ratio":
                msg = f"📉 <b>52 HAFTALIK DİP ({code}):</b> Fiili dolaşım oranı %{val:.2f} ile en düşük seviyede."
            elif etype == "ratio_jump_up":
                msg = f"⚡ <b>SERT DOLAŞIM ARTIŞI ({code}):</b> Dolaşım oranı %{payload.get('ratio', val):.2f} (%+{val:.2f} değişim) seviyesine sıçradı!"
            elif etype == "ratio_jump_down":
                msg = f"⚡ <b>SERT DOLAŞIM AZALIŞI ({code}):</b> Dolaşım oranı %{payload.get('ratio', val):.2f} (%{val:.2f} değişim) seviyesine düştü!"
            elif etype == "ratio_threshold_cross_down":
                msg = f"🚨 <b>KRİTİK EŞİK AŞILDI ({code}):</b> Dolaşım oranı %{payload.get('from', 0):.2f} -> %{payload.get('to', 0):.2f} düşerek kritik %10/%20 sınırını aşağı kırdı!"
            elif etype == "ratio_threshold_cross_up":
                msg = f"✅ <b>EŞİK AŞILDI ({code}):</b> Dolaşım oranı %{payload.get('from', 0):.2f} -> %{payload.get('to', 0):.2f} yükselerek güvenli bölgeye geçti."
            elif etype == "float_shares_jump_up":
                msg = f"🔄 <b>FİİLİ HİSSE ARTIŞI ({code}):</b> Dolaşımdaki pay adedi %+{val:.2f} arttı."
            elif etype == "float_shares_jump_down":
                msg = f"🔄 <b>FİİLİ HİSSE AZALIŞI ({code}):</b> Dolaşımdaki pay adedi %{val:.2f} azaldı."
            elif etype == "capital_change_detected":
                msg = f"🏢 <b>SERMAYE DEĞİŞİMİ ({code}):</b> Ödenmiş sermaye {payload.get('from', 0):,.0f} -> {payload.get('to', 0):,.0f} TRY olarak güncellendi."
            else:
                msg = f"🔔 <b>BİLDİRİM ({code}):</b> {etype} (Değer: {val:.2f})"
            messages.append(msg)

        header = f"📢 <b>BIST Serbest Dolaşım Bildirimleri ({report_date})</b>\n\n"
        chunks = []
        current_chunk = header
        for msg in messages:
            if len(current_chunk) + len(msg) + 2 > 4000:
                chunks.append(current_chunk)
                current_chunk = header + msg + "\n"
            else:
                current_chunk += msg + "\n"
        chunks.append(current_chunk)

        with httpx.Client(timeout=10.0) as client:
            for chunk in chunks:
                client.post(
                    url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
                )

    def _send_webhook_alerts(
        self, url: str, report_date: str, events: list[dict[str, Any]]
    ) -> None:
        import httpx

        payload = {"report_date": report_date, "events": events}
        with httpx.Client(timeout=10.0) as client:
            client.post(url, json=payload)


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
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    store = store or ArchiveStore()
    synced: list[dict[str, Any]] = []
    skipped = 0
    missing = 0

    logger.info(
        "Starting sync archive operation", extra={"latest": latest, "resume": resume}
    )

    if latest:
        if on_progress:
            try:
                on_progress(
                    {
                        "event": "sync_progress",
                        "status": "started",
                        "mode": "latest",
                    }
                )
            except Exception:
                pass
        claim = store.claim_sync_slot(_SYNC_SOURCE_VAP)
        try:
            logger.info("Fetching latest VAP free-float report")
            records = freefloat.fetch_report(None)
            result = store.sync_records(records)
            logger.info(
                "Successfully synced latest VAP free-float report",
                extra={"date": result["report_date"], "records": len(records)},
            )
        except NotFoundError as error:
            logger.warning(
                "No latest VAP free-float report found", extra={"error": error.message}
            )
            store.complete_sync_slot(
                _SYNC_SOURCE_VAP,
                status="not_found",
                error=error.message,
            )
            if on_progress:
                try:
                    on_progress(
                        {
                            "event": "sync_progress",
                            "status": "not_found",
                            "error": error.message,
                        }
                    )
                except Exception:
                    pass
            raise
        except Exception as error:
            message = getattr(error, "message", str(error))
            logger.error("Failed to sync latest VAP free-float report", exc_info=True)
            store.complete_sync_slot(
                _SYNC_SOURCE_VAP,
                status="error",
                error=message,
            )
            if on_progress:
                try:
                    on_progress(
                        {
                            "event": "sync_progress",
                            "status": "failed",
                            "error": message,
                        }
                    )
                except Exception:
                    pass
            raise
        store.complete_sync_slot(
            _SYNC_SOURCE_VAP,
            status="success",
            report_date=result["report_date"],
        )
        try:
            store.update_symbol_metadata()
        except Exception:
            pass
        _run_auto_backup(store)
        if on_progress:
            try:
                on_progress(
                    {
                        "event": "sync_progress",
                        "status": "completed",
                        "synced_count": 1,
                        "report_date": result["report_date"],
                    }
                )
            except Exception:
                pass
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

    if on_progress:
        try:
            on_progress(
                {
                    "event": "sync_progress",
                    "status": "started",
                    "mode": "range",
                }
            )
        except Exception:
            pass

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
            if on_progress:
                try:
                    on_progress(
                        {
                            "event": "sync_progress",
                            "status": "processing",
                            "date": candidate.isoformat(),
                            "skipped": skipped,
                            "synced": len(synced),
                            "missing": missing,
                        }
                    )
                except Exception:
                    pass
            try:
                logger.info(
                    "Fetching free-float report for date",
                    extra={"date": candidate.isoformat()},
                )
                records = freefloat.fetch_report(
                    candidate, cache=cache, throttle=backfill_throttle
                )
            except NotFoundError:
                # Weekend/holiday: stamp it so a future --resume run skips it
                # instead of re-downloading the same empty day.
                logger.info(
                    "No report found for date (holiday/weekend)",
                    extra={"date": candidate.isoformat()},
                )
                store.mark_empty(candidate)
                missing += 1
                fetched_in_session += 1
                continue
            result = store.sync_records(records)
            logger.info(
                "Successfully synced report for date",
                extra={"date": candidate.isoformat(), "records": len(records)},
            )
            synced.append(result)
            last_report_date = result["report_date"]
            fetched_in_session += 1
    except Exception as error:
        message = getattr(error, "message", str(error))
        logger.error("Backfill sync failed", exc_info=True)
        store.complete_sync_slot(_SYNC_SOURCE_VAP, status="error", error=message)
        if on_progress:
            try:
                on_progress(
                    {
                        "event": "sync_progress",
                        "status": "failed",
                        "error": message,
                    }
                )
            except Exception:
                pass
        raise

    store.complete_sync_slot(
        _SYNC_SOURCE_VAP,
        status="success" if synced else "no_data",
        report_date=last_report_date,
    )
    if synced:
        try:
            store.update_symbol_metadata()
        except Exception:
            pass
        _run_auto_backup(store)

    if on_progress:
        try:
            on_progress(
                {
                    "event": "sync_progress",
                    "status": "completed",
                    "synced_count": len(synced),
                    "report_date": last_report_date,
                }
            )
        except Exception:
            pass

    return {
        "mode": "range",
        "synced_reports": len(synced),
        "skipped_reports": skipped,
        "missing_dates": missing,
        "reports": synced,
        "sync": claim,
        "attempted_fetch": attempted,
    }


def _run_auto_backup(store: ArchiveStore) -> None:
    """Run rotation backup keeping the last 7 files."""
    try:
        backup_dir = default_data_dir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"backup_{timestamp}.sqlite3"

        logger.info(f"Running auto-backup to {backup_path}")
        store.backup(backup_path)

        # Auto-rotate: keep only the 7 most recent backups
        backups = sorted(
            [f for f in backup_dir.glob("backup_*.sqlite3") if f.is_file()],
            key=lambda x: x.stat().st_mtime,
        )
        if len(backups) > 7:
            for old_backup in backups[:-7]:
                try:
                    old_backup.unlink()
                    logger.info(f"Deleted old auto-backup: {old_backup}")
                except Exception as e:
                    logger.warning(f"Failed to delete old backup {old_backup}: {e}")
    except Exception as e:
        logger.error(f"Auto-backup failed: {e}", exc_info=True)
