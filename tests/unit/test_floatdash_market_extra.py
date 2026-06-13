from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from tvcli.floatdash.app import create_app
from tvcli.layers import freefloat as ff
from tvcli.layers.freefloat_archive import ArchiveStore
from tvcli.layers.ohlcv import OhlcvBar


def test_floatdash_market_extra_endpoints(monkeypatch, tmp_path: Path) -> None:
    # Setup paths and ArchiveStore
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    archive_file = tmp_path / "archive.sqlite3"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
    monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

    store = ArchiveStore(archive_file)
    app = create_app(store=store)

    # Seed initial test data
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    today_str = today.strftime("%d.%m.%Y")
    yesterday_str = yesterday.strftime("%d.%m.%Y")

    records_yesterday = [
        ff.FloatRecord(
            code="THYAO",
            isin="TR000001",
            name="THYAO",
            float_shares=150.0,
            capital=1000.0,
            ratio=15.0,
            date=yesterday_str,
        )
    ]
    records_today = [
        ff.FloatRecord(
            code="THYAO",
            isin="TR000001",
            name="THYAO",
            float_shares=175.0,
            capital=1000.0,
            ratio=17.5,
            date=today_str,
        )
    ]

    store.sync_records(tuple(records_yesterday))
    store.sync_records(tuple(records_today))

    with store._connect() as conn:
        # Seed metadata
        conn.execute(
            """
            INSERT INTO freefloat_symbol_metadata(code, sector, industry, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            ("THYAO", "Ulasim", "Havayolu", "2026-06-10T12:00:00"),
        )

        # Seed sync_state
        conn.execute(
            """
            INSERT INTO sync_state(
                source, last_attempt_at, last_success_at, last_report_date,
                cooldown_until, last_status, last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "vap",
                "2026-06-10T12:00:00",
                "2026-06-10T12:00:00",
                today.isoformat(),
                None,
                "success",
                None,
            ),
        )

        # Seed missing days
        conn.execute(
            """
            INSERT INTO freefloat_missing(report_date, checked_at)
            VALUES (?, ?)
            """,
            ((today - timedelta(days=5)).isoformat(), "2026-06-10T12:00:00"),
        )

    # Mock sync_archive and fetch_history
    sync_archive_called = False

    def mock_sync_archive(*args, **kwargs) -> None:
        nonlocal sync_archive_called
        sync_archive_called = True
        cb = kwargs.get("on_progress")
        if cb:
            cb({"event": "progress", "percent": 50})

    def mock_fetch_history(req: Any) -> list[OhlcvBar]:
        return [
            OhlcvBar(
                time=1718064000,
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.5,
                volume=1000.0,
            )
        ]

    # Mock synchronous httpx.post for Telegram / Webhook connection tests
    class MockResponse:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text

    def mock_httpx_post(url: str, *args: Any, **kwargs: Any) -> MockResponse:
        if "telegram.org" in str(url):
            return MockResponse(200, '{"ok": true}')
        if "example.com/webhook" in str(url):
            return MockResponse(200, "ok")
        return MockResponse(404, "not found")

    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.sync_archive", mock_sync_archive
    )
    monkeypatch.setattr("tvcli.layers.ohlcv.fetch_history", mock_fetch_history)
    monkeypatch.setattr("httpx.post", mock_httpx_post)

    async def run_tests() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # 1. Test GET /api/market/sectors
            resp_sec = await client.get("/api/market/sectors")
            assert resp_sec.status_code == 200
            sec_data = resp_sec.json()
            assert len(sec_data) == 1
            assert sec_data[0]["sector"] == "Ulasim"
            assert sec_data[0]["symbols"][0]["code"] == "THYAO"

            # 2. Test GET /api/sync/status
            resp_stat = await client.get("/api/sync/status")
            assert resp_stat.status_code == 200
            stat_data = resp_stat.json()
            assert stat_data["sync_state"]["source"] == "vap"
            assert stat_data["health"]["gap_count"] >= 1

            # 3. Test POST /api/sync/run (successful trigger)
            resp_run = await client.post("/api/sync/run", json={"latest": True})
            assert resp_run.status_code == 200
            assert resp_run.json()["success"] is True

            # Let the background task run (wait a bit)
            await anyio.sleep(0.5)
            assert sync_archive_called is True

            # 4. Test POST /api/sync/run with dates
            resp_run_dates = await client.post(
                "/api/sync/run",
                json={
                    "latest": False,
                    "since": yesterday.isoformat(),
                    "until": today.isoformat(),
                },
            )
            assert resp_run_dates.status_code == 200
            assert resp_run_dates.json()["success"] is True

            # 5. Test POST /api/sync/run with invalid dates
            try:
                resp_invalid_since = await client.post(
                    "/api/sync/run",
                    json={"latest": False, "since": "invalid-date"},
                )
                assert resp_invalid_since.status_code == 400
            except Exception as exc:
                assert "Invalid since date format" in str(exc)

            try:
                resp_invalid_until = await client.post(
                    "/api/sync/run",
                    json={"latest": False, "until": "invalid-date"},
                )
                assert resp_invalid_until.status_code == 400
            except Exception as exc:
                assert "Invalid until date format" in str(exc)

            # 6. Test GET /api/symbol/{code}/ohlcv
            resp_ohlcv = await client.get("/api/symbol/THYAO/ohlcv")
            assert resp_ohlcv.status_code == 200
            ohlcv_data = resp_ohlcv.json()
            assert len(ohlcv_data) == 1
            assert ohlcv_data[0]["close"] == 10.5

            # 7. Test cooldown rejection
            # Put sync in active cooldown
            future_cooldown = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
            with store._connect() as conn:
                conn.execute(
                    "UPDATE sync_state SET cooldown_until = ? WHERE source = ?",
                    (future_cooldown, "vap"),
                )

            resp_stat_cooldown = await client.get("/api/sync/status")
            assert resp_stat_cooldown.status_code == 200
            assert resp_stat_cooldown.json()["cooldown_active"] is True

            # POST /api/sync/run should get rejected now
            resp_run_rejected = await client.post(
                "/api/sync/run", json={"latest": True}
            )
            assert resp_run_rejected.status_code == 200
            assert resp_run_rejected.json()["success"] is False
            assert resp_run_rejected.json()["cooldown_active"] is True

            # 8. Test Settings Backup Download
            resp_backup_download = await client.get("/api/settings/backup/download")
            assert resp_backup_download.status_code == 200
            assert (
                resp_backup_download.headers["content-type"] == "application/x-sqlite3"
            )

            # 9. Test Settings Backup Restore
            backup_bytes = resp_backup_download.content
            resp_backup_restore = await client.post(
                "/api/settings/backup/restore",
                files={
                    "file": (
                        "restore.sqlite3",
                        backup_bytes,
                        "application/x-sqlite3",
                    )
                },
            )
            assert resp_backup_restore.status_code == 200
            assert resp_backup_restore.json()["status"] == "success"

            # 10. Test Settings Test Endpoint (Telegram Bot)
            resp_test_tg = await client.post(
                "/api/settings/test",
                json={
                    "telegram_token": "123456789:ABC_DEF_GHI",
                    "telegram_chat_id": "-100111",
                },
            )
            assert resp_test_tg.status_code == 200
            assert resp_test_tg.json()["status"] == "success"

            # 11. Test Settings Test Endpoint (Webhook URL)
            resp_test_webhook = await client.post(
                "/api/settings/test",
                json={"webhook_url": "http://example.com/webhook"},
            )
            assert resp_test_webhook.status_code == 200
            assert resp_test_webhook.json()["status"] == "success"

            # 12. Test Settings Test Endpoint (Empty/No channel)
            resp_test_empty = await client.post("/api/settings/test", json={})
            assert resp_test_empty.status_code == 200
            assert resp_test_empty.json()["status"] == "error"

    anyio.run(run_tests)


def test_market_router_additional_coverage(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    archive_file = tmp_path / "archive.sqlite3"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
    monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

    store = ArchiveStore(archive_file)
    app = create_app(store=store)

    # 1. Seed two dates to test gainers and losers
    yesterday_str = "10.06.2026"
    today_str = "11.06.2026"

    # THYAO rises from 10 to 15 (gainer), GARAN drops from 20 to 12 (loser)
    records_yesterday = [
        ff.FloatRecord(
            code="THYAO",
            isin="TR1",
            name="THYAO",
            float_shares=100.0,
            capital=1000.0,
            ratio=10.0,
            date=yesterday_str,
        ),
        ff.FloatRecord(
            code="GARAN",
            isin="TR2",
            name="GARAN",
            float_shares=200.0,
            capital=1000.0,
            ratio=20.0,
            date=yesterday_str,
        ),
    ]
    records_today = [
        ff.FloatRecord(
            code="THYAO",
            isin="TR1",
            name="THYAO",
            float_shares=150.0,
            capital=1000.0,
            ratio=15.0,
            date=today_str,
        ),
        ff.FloatRecord(
            code="GARAN",
            isin="TR2",
            name="GARAN",
            float_shares=120.0,
            capital=1000.0,
            ratio=12.0,
            date=today_str,
        ),
    ]

    store.sync_records(tuple(records_yesterday))
    store.sync_records(tuple(records_today))

    # Add sector metadata
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO freefloat_symbol_metadata"
            "(code, sector, industry, updated_at) VALUES (?, ?, ?, ?)",
            ("THYAO", "Ulasim", "Havayolu", "2026-06-10T12:00:00"),
        )
        conn.execute(
            "INSERT INTO freefloat_symbol_metadata"
            "(code, sector, industry, updated_at) VALUES (?, ?, ?, ?)",
            ("GARAN", "Finans", "Banka", "2026-06-10T12:00:00"),
        )

    async def run_tests() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # A. GET /api/market should return top gainers & losers
            resp_m = await client.get("/api/market")
            assert resp_m.status_code == 200
            data_m = resp_m.json()
            assert len(data_m["top_gainers"]) > 0
            assert data_m["top_gainers"][0]["code"] == "THYAO"
            assert len(data_m["top_losers"]) > 0
            assert data_m["top_losers"][0]["code"] == "GARAN"

            # B. GET /api/market raises exception
            def mock_stats_raise(*args, **kwargs):
                raise ValueError("Stats crash")

            monkeypatch.setattr(store, "archive_stats", mock_stats_raise)

            with pytest.raises(ValueError, match="Stats crash"):
                await client.get("/api/market")

            # Undo mocks
            monkeypatch.undo()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
            monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

            # C. GET /api/market/sectors raises exception
            def mock_heatmap_raise(*args, **kwargs):
                raise ValueError("Heatmap crash")

            monkeypatch.setattr(store, "get_sector_heatmap", mock_heatmap_raise)

            resp_sec_err = await client.get("/api/market/sectors")
            assert resp_sec_err.status_code == 500

            # Undo mocks
            monkeypatch.undo()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
            monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

            # D. GET /api/sync/status with empty sync_state table (never_run)
            with store._connect() as conn:
                conn.execute("DELETE FROM sync_state")

            resp_never = await client.get("/api/sync/status")
            assert resp_never.status_code == 200
            assert resp_never.json()["sync_state"]["last_status"] == "never_run"

            # E. GET /api/sync/status with invalid cooldown format in db
            with store._connect() as conn:
                conn.execute(
                    "INSERT INTO sync_state("
                    "source, last_attempt_at, last_success_at, "
                    "last_report_date, cooldown_until, last_status, last_error"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "vap",
                        None,
                        None,
                        None,
                        "invalid-datetime-format",
                        "success",
                        None,
                    ),
                )

            resp_invalid_cool = await client.get("/api/sync/status")
            assert resp_invalid_cool.status_code == 200
            assert resp_invalid_cool.json()["cooldown_active"] is False

            # F. GET /api/sync/status raises exception
            monkeypatch.setattr(store, "archive_stats", mock_stats_raise)
            resp_stat_err = await client.get("/api/sync/status")
            assert resp_stat_err.status_code == 500

            # G. POST /api/sync/run raises exception (e.g. cooldown check fails)
            monkeypatch.undo()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
            monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

            def mock_connect_raise(*args, **kwargs):
                raise RuntimeError("DB failure during cooldown check")

            monkeypatch.setattr(store, "_connect", mock_connect_raise)

            resp_run_err = await client.post("/api/sync/run", json={"latest": True})
            assert resp_run_err.status_code == 500

    anyio.run(run_tests)

    # H. Test background _bg_sync thread exception handling
    from tvcli.floatdash.routers.market import _bg_sync

    # We create a mock ConnectionManager to see what events are broadcasted
    class MockConnectionManager:
        def __init__(self):
            self.broadcasts = []

        async def broadcast(self, msg):
            self.broadcasts.append(msg)

    # Mock sync_archive to raise exception
    def mock_sync_archive_raise(*args, **kwargs):
        raise RuntimeError("Sync sync_archive failed")

    monkeypatch.undo()
    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.sync_archive", mock_sync_archive_raise
    )

    mgr = MockConnectionManager()
    # Call _bg_sync via anyio.to_thread.run_sync to simulate AnyIO background thread
    anyio.run(anyio.to_thread.run_sync, _bg_sync, store, mgr)

    assert any(b.get("event") == "sync_failed" for b in mgr.broadcasts)

    # I. Test background _bg_sync thread progress callback broadcast exception
    class BadConnectionManager:
        async def broadcast(self, msg):
            if msg.get("event") == "progress":
                raise RuntimeError("WS broadcast failure")

    # Mock sync_archive to trigger progress callback
    def mock_sync_archive_cb(*args, **kwargs):
        cb = kwargs.get("on_progress")
        if cb:
            cb({"event": "progress", "percent": 50})

    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.sync_archive", mock_sync_archive_cb
    )
    bad_mgr = BadConnectionManager()
    # Should run successfully and swallow progress callback WS broadcast exceptions
    anyio.run(anyio.to_thread.run_sync, _bg_sync, store, bad_mgr)
