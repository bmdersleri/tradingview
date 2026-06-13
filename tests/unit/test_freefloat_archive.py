from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tvcli.errors import NotFoundError, RateLimitedError
from tvcli.layers import freefloat as ff
from tvcli.layers.freefloat_archive import ArchiveStore, sync_archive


def _record(
    code: str,
    ratio: float,
    *,
    label: str,
    float_shares: float,
    capital: float = 1000.0,
) -> ff.FloatRecord:
    return ff.FloatRecord(
        code=code,
        isin=f"TR{code}000001",
        name=f"{code} NAME",
        float_shares=float_shares,
        capital=capital,
        ratio=ratio,
        date=label,
    )


def test_archive_store_sync_and_report(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    store.sync_records(
        (
            _record("THYAO", 35.0, label="10.06.2026", float_shares=350.0),
            _record("ENPRA", 8.0, label="10.06.2026", float_shares=80.0),
        )
    )
    store.sync_records(
        (
            _record("THYAO", 41.5, label="11.06.2026", float_shares=430.0),
            _record("ENPRA", 7.5, label="11.06.2026", float_shares=75.0),
        )
    )

    report = store.build_symbol_report("BIST:THYAO", limit=10)

    assert report["symbol"] == "THYAO"
    assert report["latest"]["ratio"] == pytest.approx(41.5)
    assert report["trend"]["direction"] == "rising"
    assert report["summary"]["report_count"] == 2
    assert report["recent_changes"][0]["ratio_delta"] == pytest.approx(6.5)
    event_types = {event["event_type"] for event in report["events"]}
    assert "ratio_jump_up" in event_types
    assert "new_52w_high_ratio" in event_types


def test_archive_store_low_float_event_and_stats(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    store.sync_records((_record("ENPRA", 9.0, label="11.06.2026", float_shares=90.0),))

    report = store.build_symbol_report("ENPRA")
    stats = store.archive_stats()

    assert report["risk"]["low_float"] is True
    assert report["risk"]["severe_low_float"] is True
    assert stats["reports"] == 1
    assert stats["symbols"] == 1


def test_build_symbol_report_requires_sync(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")

    with pytest.raises(NotFoundError):
        store.build_symbol_report("THYAO")


def test_latest_risk_events_returns_latest_report_events(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    # A sharp ratio drop across two reports => a high-severity ratio_jump_down at
    # the latest date; the earlier date's events must not leak in.
    store.sync_records(
        (_record("THYAO", 40.0, label="10.06.2026", float_shares=400.0),)
    )
    store.sync_records(
        (_record("THYAO", 22.0, label="11.06.2026", float_shares=220.0),)
    )

    events = store.latest_risk_events("BIST:THYAO")

    assert events  # non-empty
    assert all(e["report_date"] == "2026-06-11" for e in events)
    assert "ratio_jump_down" in {e["event_type"] for e in events}


def test_latest_risk_events_unknown_symbol_is_empty(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    assert store.latest_risk_events("NOPE") == []


def test_sync_archive_range_walks_full_window(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
    store = ArchiveStore(tmp_path / "archive.sqlite3", clock=lambda: now)
    published = {
        date(2026, 6, 11): (
            _record("THYAO", 35.0, label="11.06.2026", float_shares=350.0),
        ),
        date(2026, 6, 9): (
            _record("THYAO", 32.0, label="09.06.2026", float_shares=320.0),
        ),
    }
    calls: list[date | None] = []

    def fake_fetch(
        report_date: date | None, **_kw: object
    ) -> tuple[ff.FloatRecord, ...]:
        calls.append(report_date)
        if report_date not in published:
            raise NotFoundError("missing", hint="x")
        return published[report_date]

    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.freefloat.fetch_report", fake_fetch
    )
    monkeypatch.setattr("tvcli.layers.freefloat_archive._sleep", lambda _s: None)

    result = sync_archive(
        latest=False,
        since=date(2026, 6, 9),
        until=date(2026, 6, 11),
        max_days=None,
        resume=False,
        store=store,
    )

    # The whole 3-day window is walked: 2 published + 1 empty (06-10), no stop.
    assert result["synced_reports"] == 2
    assert result["missing_dates"] == 1
    assert calls == [date(2026, 6, 11), date(2026, 6, 10), date(2026, 6, 9)]
    assert store.archive_stats()["reports"] == 2
    assert store.is_known_empty(date(2026, 6, 10)) is True


def test_backfill_survives_more_than_five_days(monkeypatch, tmp_path: Path) -> None:
    """Regression: a backfill must not die on the interactive 5-per-10min bucket.

    Earlier the range loop called the live ``fetch_report`` with no throttle, so
    it rode the default 5-token/120s interactive bucket and aborted on the 6th
    day. Here we drive the *real* ``freefloat.fetch_report`` (only the network
    download and cache/throttle DB are redirected into tmp) over 8 published
    days and assert all 8 land in the archive.
    """
    from tvcli.layers import freefloat as ff

    now = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
    store = ArchiveStore(tmp_path / "archive.sqlite3", clock=lambda: now)
    # Eight consecutive published business days (06-02 .. 06-09).
    published = {
        date(2026, 6, day): (
            _record(
                "THYAO", 30.0 + day, label=f"{day:02d}.06.2026", float_shares=300.0
            ),
        )
        for day in range(2, 10)
    }

    def fake_download(report_date: date) -> tuple[ff.FloatRecord, ...]:
        if report_date not in published:
            raise NotFoundError("missing", hint="x")
        return published[report_date]

    # Patch only the network seam + the cache/ratelimit DB path; the real
    # fetch_report (with its token bucket) runs unmodified.
    monkeypatch.setattr(ff, "_download_report", fake_download)
    monkeypatch.setattr(
        "tvcli.layers.freefloat.default_cache_path", lambda: tmp_path / "cache.sqlite3"
    )
    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.default_cache_path",
        lambda: tmp_path / "cache.sqlite3",
    )
    monkeypatch.setattr("tvcli.layers.freefloat_archive._sleep", lambda _s: None)

    result = sync_archive(
        latest=False,
        since=date(2026, 6, 2),
        until=date(2026, 6, 9),
        max_days=None,
        resume=False,
        rate_seconds=20.0,
        store=store,
    )

    assert result["synced_reports"] == 8  # all 8 days, not capped at 5
    assert store.archive_stats()["reports"] == 8


def test_backfill_resume_skips_synced_and_empty(monkeypatch, tmp_path: Path) -> None:
    clock = [datetime(2026, 6, 12, 9, 0, tzinfo=UTC)]
    store = ArchiveStore(tmp_path / "archive.sqlite3", clock=lambda: clock[0])
    published = {
        date(2026, 6, 11): (
            _record("THYAO", 35.0, label="11.06.2026", float_shares=350.0),
        ),
    }

    def fake_fetch(
        report_date: date | None, **_kw: object
    ) -> tuple[ff.FloatRecord, ...]:
        if report_date not in published:
            raise NotFoundError("missing", hint="x")
        return published[report_date]

    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.freefloat.fetch_report", fake_fetch
    )
    monkeypatch.setattr("tvcli.layers.freefloat_archive._sleep", lambda _s: None)

    window = {
        "since": date(2026, 6, 10),
        "until": date(2026, 6, 11),
        "max_days": None,
    }
    first = sync_archive(latest=False, resume=True, store=store, **window)
    assert first["synced_reports"] == 1  # 06-11 published
    assert first["missing_dates"] == 1  # 06-10 empty, stamped

    # A later session (past the per-session cooldown) must not re-fetch:
    # 06-11 already stored, 06-10 known empty.
    clock[0] = clock[0] + timedelta(minutes=11)
    calls: list[date | None] = []

    def tracking_fetch(
        report_date: date | None, **_kw: object
    ) -> tuple[ff.FloatRecord, ...]:
        calls.append(report_date)
        return fake_fetch(report_date)

    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.freefloat.fetch_report", tracking_fetch
    )
    second = sync_archive(latest=False, resume=True, store=store, **window)
    assert second["synced_reports"] == 0
    assert second["skipped_reports"] == 2
    assert calls == []  # nothing re-fetched


def test_sync_archive_enforces_cooldown(monkeypatch, tmp_path: Path) -> None:
    current = [datetime(2026, 6, 12, 9, 0, tzinfo=UTC)]
    store = ArchiveStore(tmp_path / "archive.sqlite3", clock=lambda: current[0])

    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.freefloat.fetch_report",
        lambda _report_date: (
            _record("THYAO", 35.0, label="11.06.2026", float_shares=350.0),
        ),
    )

    first = sync_archive(
        latest=True,
        since=None,
        until=None,
        max_days=None,
        resume=False,
        store=store,
    )

    assert first["synced_reports"] == 1

    with pytest.raises(RateLimitedError):
        sync_archive(
            latest=True,
            since=None,
            until=None,
            max_days=None,
            resume=False,
            store=store,
        )

    current[0] = current[0] + timedelta(minutes=11)

    second = sync_archive(
        latest=True,
        since=None,
        until=None,
        max_days=None,
        resume=False,
        store=store,
    )

    assert second["synced_reports"] == 1


def test_archive_stats_exposes_sync_state(monkeypatch, tmp_path: Path) -> None:
    current = [datetime(2026, 6, 12, 9, 0, tzinfo=UTC)]
    store = ArchiveStore(tmp_path / "archive.sqlite3", clock=lambda: current[0])

    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.freefloat.fetch_report",
        lambda _report_date: (
            _record("THYAO", 35.0, label="11.06.2026", float_shares=350.0),
        ),
    )
    sync_archive(
        latest=True,
        since=None,
        until=None,
        max_days=None,
        resume=False,
        store=store,
    )

    stats = store.archive_stats()

    assert stats["sync_state"][0]["source"] == "vap"
    assert stats["sync_state"][0]["last_status"] == "success"
