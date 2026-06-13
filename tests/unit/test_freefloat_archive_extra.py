from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

from tvcli.layers import freefloat as ff
from tvcli.layers.freefloat_archive import ArchiveStore, sync_archive


def test_archive_backup_exception(monkeypatch, tmp_path: Path) -> None:
    # Test backup exception handling
    store = ArchiveStore(tmp_path / "archive.sqlite3")

    def mock_connect(*args, **kwargs):
        raise sqlite3.OperationalError("Mock database error")

    monkeypatch.setattr("sqlite3.connect", mock_connect)
    with pytest.raises(sqlite3.OperationalError):
        store.backup(tmp_path / "backup.sqlite3")


def test_archive_restore_exception(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    with pytest.raises(FileNotFoundError):
        store.restore(tmp_path / "nonexistent_backup.sqlite3")


def test_update_symbol_metadata_pytest_bypass(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")

    class MockQuery:
        def set_markets(self, market):
            return self

        def select(self, *fields):
            return self

        def limit(self, limit):
            return self

        def get_scanner_data(self):
            class MockRow:
                def __init__(self, ticker, sector, industry):
                    self.ticker = ticker
                    self.sector = sector
                    self.industry = industry

            class MockDF:
                def iterrows(self):
                    return iter(
                        [
                            (
                                0,
                                {
                                    "ticker": "BIST:THYAO",
                                    "sector": "Transportation",
                                    "industry": "Airlines",
                                },
                            ),
                            (
                                1,
                                {
                                    "ticker": "BIST:ENPRA",
                                    "sector": None,
                                    "industry": None,
                                },
                            ),
                        ]
                    )

            return ("meta", MockDF())

    original_pytest = sys.modules.get("pytest")
    if "pytest" in sys.modules:
        del sys.modules["pytest"]

    try:
        sys.modules["tradingview_screener"] = type(
            "MockScreener", (), {"Query": MockQuery}
        )  # type: ignore
        store.update_symbol_metadata()

        with store._connect() as conn:
            rows = conn.execute(
                "SELECT code, sector, industry FROM freefloat_symbol_metadata"
            ).fetchall()
            assert len(rows) == 2
            assert rows[0]["code"] == "THYAO"
            assert rows[0]["sector"] == "Transportation"
            assert rows[1]["code"] == "ENPRA"
            assert rows[1]["sector"] == "Bilinmeyen"
    finally:
        if original_pytest:
            sys.modules["pytest"] = original_pytest
        if "tradingview_screener" in sys.modules:
            del sys.modules["tradingview_screener"]


def test_get_sector_heatmap_trigger_metadata(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")

    called = False

    def mock_update_symbol_metadata():
        nonlocal called
        called = True

    monkeypatch.setattr(store, "update_symbol_metadata", mock_update_symbol_metadata)
    store.get_sector_heatmap("2026-06-10")
    assert called is True


def test_send_alerts_and_webhook(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")

    posts = []

    class MockHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def post(self, url, json):
            posts.append((url, json))
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "Client", MockHttpxClient)

    events = [
        {
            "code": "THYAO",
            "event_type": "liquidity_risk_low_float",
            "metric_value": 8.5,
            "payload": {},
        },
        {
            "code": "THYAO",
            "event_type": "new_52w_high_ratio",
            "metric_value": 45.0,
            "payload": {},
        },
        {
            "code": "THYAO",
            "event_type": "new_52w_low_ratio",
            "metric_value": 5.0,
            "payload": {},
        },
        {
            "code": "THYAO",
            "event_type": "ratio_jump_up",
            "metric_value": 6.2,
            "payload": {"ratio": 41.5},
        },
        {
            "code": "THYAO",
            "event_type": "ratio_jump_down",
            "metric_value": 7.1,
            "payload": {"ratio": 15.0},
        },
        {
            "code": "THYAO",
            "event_type": "ratio_threshold_cross_down",
            "metric_value": 0.0,
            "payload": {"from": 21.0, "to": 18.0},
        },
        {
            "code": "THYAO",
            "event_type": "ratio_threshold_cross_up",
            "metric_value": 0.0,
            "payload": {"from": 18.0, "to": 21.0},
        },
        {
            "code": "THYAO",
            "event_type": "float_shares_jump_up",
            "metric_value": 15.0,
            "payload": {},
        },
        {
            "code": "THYAO",
            "event_type": "float_shares_jump_down",
            "metric_value": -10.0,
            "payload": {},
        },
        {
            "code": "THYAO",
            "event_type": "capital_change_detected",
            "metric_value": 0.0,
            "payload": {"from": 1000.0, "to": 1200.0},
        },
        {
            "code": "THYAO",
            "event_type": "some_other_unknown_event",
            "metric_value": 1.2,
            "payload": {},
        },
    ]

    store._send_telegram_alerts("token", "chat_id", "2026-06-10", events)
    assert len(posts) == 1
    assert "https://api.telegram.org/bottoken/sendMessage" == posts[0][0]
    assert "LİKİDİTE RİSKİ" in posts[0][1]["text"]
    assert "SERMAYE DEĞİŞİMİ" in posts[0][1]["text"]

    posts.clear()
    store._send_webhook_alerts("http://example.com/wh", "2026-06-10", events)
    assert len(posts) == 1
    assert posts[0][0] == "http://example.com/wh"
    assert posts[0][1]["report_date"] == "2026-06-10"


def test_sync_archive_callbacks_and_exceptions(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")

    # Clean cooldown sync slot
    with store._connect() as conn:
        conn.execute("DELETE FROM sync_state")

    # 1. Latest sync raise exception and callback failure
    def mock_fetch_report_raise(*args, **kwargs):
        raise RuntimeError("Network failure")

    monkeypatch.setattr("tvcli.layers.freefloat.fetch_report", mock_fetch_report_raise)

    def bad_on_progress(event):
        raise RuntimeError("Callback crash")

    with pytest.raises(RuntimeError):
        sync_archive(
            latest=True,
            since=None,
            until=None,
            max_days=None,
            resume=False,
            store=store,
            on_progress=bad_on_progress,
        )

    stats = store.archive_stats()
    assert stats["sync_state"][0]["last_status"] == "error"
    assert "Network failure" in stats["sync_state"][0]["last_error"]

    # 2. Sync range exception and callback failure
    monkeypatch.undo()
    monkeypatch.setattr("tvcli.layers.freefloat.fetch_report", mock_fetch_report_raise)
    with store._connect() as conn:
        conn.execute("DELETE FROM sync_state")

    with pytest.raises(RuntimeError):
        sync_archive(
            latest=False,
            since=date(2026, 6, 10),
            until=date(2026, 6, 11),
            max_days=None,
            resume=False,
            store=store,
            on_progress=bad_on_progress,
        )

    # 3. Latest sync complete metadata update exception
    monkeypatch.undo()
    with store._connect() as conn:
        conn.execute("DELETE FROM sync_state")

    def mock_fetch_report_success(*args, **kwargs):
        return (
            ff.FloatRecord(
                code="THYAO",
                isin="TR1",
                name="THYAO",
                float_shares=100,
                capital=1000,
                ratio=10,
                date="10.06.2026",
            ),
        )

    monkeypatch.setattr(
        "tvcli.layers.freefloat.fetch_report", mock_fetch_report_success
    )

    def mock_update_metadata_raise():
        raise RuntimeError("Metadata update failed")

    monkeypatch.setattr(store, "update_symbol_metadata", mock_update_metadata_raise)

    res = sync_archive(
        latest=True, since=None, until=None, max_days=None, resume=False, store=store
    )
    assert res["synced_reports"] == 1


def test_auto_backup_rotation_rotation_failures(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")

    data_dir = tmp_path / "data"
    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.default_data_dir", lambda: data_dir
    )

    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for i in range(10):
        f = backup_dir / f"backup_20260610_{i:02d}0000.sqlite3"
        f.write_text("dummy")
        os.utime(f, (1700000000 + i * 1000, 1700000000 + i * 1000))

    from tvcli.layers.freefloat_archive import _run_auto_backup

    _run_auto_backup(store)

    files = list(backup_dir.glob("backup_*.sqlite3"))
    # We keep only 7 most recent backups
    assert len(files) == 7

    def mock_backup_raise(*args, **kwargs):
        raise RuntimeError("Backup failed")

    monkeypatch.setattr(store, "backup", mock_backup_raise)
    _run_auto_backup(store)
