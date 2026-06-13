from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anyio
import httpx

from tvcli.errors import NotFoundError
from tvcli.floatdash.app import create_app
from tvcli.floatdash.dependencies import ConnectionManager
from tvcli.layers.freefloat_archive import ArchiveStore


def test_ws_connection_manager_broadcast_error() -> None:
    # Test that ConnectionManager.broadcast removes a websocket if it fails to send_json
    class MockWebSocket:
        def __init__(self):
            self.accepted = False
            self.closed = False

        async def accept(self):
            self.accepted = True

        async def send_json(self, message: Any):
            raise RuntimeError("Send failed")

    manager = ConnectionManager()
    ws = MockWebSocket()
    # Mocking WebSocket behavior/type for ConnectionManager
    anyio.run(manager.connect, ws)  # type: ignore
    assert ws in manager.active_connections

    anyio.run(manager.broadcast, {"test": "data"})
    assert ws not in manager.active_connections


def test_settings_router_boost(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    archive_file = tmp_path / "archive.sqlite3"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
    monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

    store = ArchiveStore(archive_file)
    app = create_app(store=store)

    # 1. Short token masking test (token <= 8 chars)
    # Write config with a short token
    with open(config_file, "w") as f:
        f.write('[alerts]\ntelegram-token = "short"\n')

    async def run_settings_tests() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # Get settings (should mask short token to "****************")
            resp = await client.get("/api/settings")
            assert resp.status_code == 200
            assert resp.json()["telegram_token"] == "****************"

            # 2. Get settings exception handling (mock load_config to raise)
            def mock_load_config_error(*args, **kwargs):
                raise RuntimeError("Failed to load")

            monkeypatch.setattr(
                "tvcli.floatdash.routers.settings.load_config", mock_load_config_error
            )

            resp_err = await client.get("/api/settings")
            assert resp_err.status_code == 500

            # Undo load_config mock
            monkeypatch.undo()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
            monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

            # 3. Update settings exception handling (mock save_config to raise)
            def mock_save_config_error(*args, **kwargs):
                raise RuntimeError("Failed to save")

            monkeypatch.setattr(
                "tvcli.floatdash.routers.settings.save_config", mock_save_config_error
            )

            resp_up_err = await client.post(
                "/api/settings/update",
                json={
                    "telegram_token": "token",
                    "telegram_chat_id": "123",
                    "webhook_url": "",
                    "low_float_threshold": 20.0,
                    "severe_low_float_threshold": 10.0,
                    "ratio_jump_threshold": 5.0,
                },
            )
            assert resp_up_err.status_code == 500

            # Undo save_config mock
            monkeypatch.undo()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
            monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

            # 4. Settings test endpoint with masked token resolve
            # Write config with real token
            with open(config_file, "w") as f:
                f.write('[alerts]\ntelegram-token = "my_secret_token"\n')

            # Mock httpx.post for telegram to return 200
            class MockResponse:
                def __init__(self, status_code: int, text: str):
                    self.status_code = status_code
                    self.text = text

            telegram_url_called = ""

            def mock_httpx_post(url: str, *args, **kwargs):
                nonlocal telegram_url_called
                if "telegram.org" in url:
                    telegram_url_called = url
                    return MockResponse(200, '{"ok":true}')
                return MockResponse(500, "error")

            monkeypatch.setattr("httpx.post", mock_httpx_post)

            resp_test_masked = await client.post(
                "/api/settings/test",
                json={
                    "telegram_token": "my_s****************oken",
                    "telegram_chat_id": "12345",
                },
            )
            assert resp_test_masked.status_code == 200
            assert "my_secret_token" in telegram_url_called

            # 5. Settings test endpoint telegram API failure (non-200)
            def mock_httpx_post_tg_fail(url: str, *args, **kwargs):
                if "telegram.org" in url:
                    return MockResponse(400, "Bad Token")
                return MockResponse(500, "error")

            monkeypatch.setattr("httpx.post", mock_httpx_post_tg_fail)
            resp_tg_fail = await client.post(
                "/api/settings/test",
                json={
                    "telegram_token": "wrong",
                    "telegram_chat_id": "12345",
                },
            )
            assert resp_tg_fail.status_code == 200
            assert resp_tg_fail.json()["status"] == "error"
            assert "Telegram API returned status 400" in resp_tg_fail.json()["message"]

            # 6. Settings test endpoint telegram connection exception
            def mock_httpx_post_tg_exception(url: str, *args, **kwargs):
                if "telegram.org" in url:
                    raise httpx.ConnectError("Connection timed out")
                return MockResponse(500, "error")

            monkeypatch.setattr("httpx.post", mock_httpx_post_tg_exception)
            resp_tg_exc = await client.post(
                "/api/settings/test",
                json={
                    "telegram_token": "wrong",
                    "telegram_chat_id": "12345",
                },
            )
            assert resp_tg_exc.status_code == 200
            assert resp_tg_exc.json()["status"] == "error"
            assert "Telegram connection error" in resp_tg_exc.json()["message"]

            # 7. Settings test endpoint webhook failure (non-200)
            def mock_httpx_post_wh_fail(url: str, *args, **kwargs):
                if "webhook" in url:
                    return MockResponse(500, "Internal Server Error")
                return MockResponse(500, "error")

            monkeypatch.setattr("httpx.post", mock_httpx_post_wh_fail)
            resp_wh_fail = await client.post(
                "/api/settings/test",
                json={
                    "webhook_url": "http://example.com/webhook",
                },
            )
            assert resp_wh_fail.status_code == 200
            assert resp_wh_fail.json()["status"] == "error"
            assert "Webhook returned status 500" in resp_wh_fail.json()["message"]

            # 8. Settings test endpoint webhook connection exception
            def mock_httpx_post_wh_exc(url: str, *args, **kwargs):
                if "webhook" in url:
                    raise httpx.ConnectError("Connection refused")
                return MockResponse(500, "error")

            monkeypatch.setattr("httpx.post", mock_httpx_post_wh_exc)
            resp_wh_exc = await client.post(
                "/api/settings/test",
                json={
                    "webhook_url": "http://example.com/webhook",
                },
            )
            assert resp_wh_exc.status_code == 200
            assert resp_wh_exc.json()["status"] == "error"
            assert "Webhook connection error" in resp_wh_exc.json()["message"]

            # 9. Backup download exception
            def mock_backup_raise(*args, **kwargs):
                raise RuntimeError("Backup failed")

            monkeypatch.setattr(store, "backup", mock_backup_raise)

            resp_back_err = await client.get("/api/settings/backup/download")
            assert resp_back_err.status_code == 500

            # 10. Restore exception
            def mock_restore_raise(*args, **kwargs):
                raise RuntimeError("Restore failed")

            monkeypatch.setattr(store, "restore", mock_restore_raise)

            resp_rest_err = await client.post(
                "/api/settings/backup/restore",
                files={
                    "file": (
                        "restore.sqlite3",
                        b"dummy content",
                        "application/x-sqlite3",
                    )
                },
            )
            assert resp_rest_err.status_code == 500

            # 11. Restore finally block remove file exception
            # We want to trigger the Exception block inside finally of restore_backup
            # Mock os.remove to raise exception
            monkeypatch.undo()
            monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
            monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)
            # Make sure restore succeeds so we hit the finally block without early 500
            monkeypatch.setattr(store, "restore", lambda path: None)

            def mock_os_remove_raise(path):
                raise OSError("Permission denied")

            monkeypatch.setattr(os, "remove", mock_os_remove_raise)

            resp_rest_cleanup_err = await client.post(
                "/api/settings/backup/restore",
                files={
                    "file": (
                        "restore.sqlite3",
                        b"dummy content",
                        "application/x-sqlite3",
                    )
                },
            )
            assert (
                resp_rest_cleanup_err.status_code == 200
            )  # Since restore completes successfully but cleanup fails silently

    anyio.run(run_settings_tests)


def test_symbol_router_boost(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    app = create_app(store=store)

    async def run_symbol_tests() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # 1. GET /api/symbol/{code} raises non-NotFoundError
            def mock_build_report_raise(*args, **kwargs):
                raise ValueError("Database crash")

            monkeypatch.setattr(store, "build_symbol_report", mock_build_report_raise)

            resp_err = await client.get("/api/symbol/THYAO")
            assert resp_err.status_code == 500

            # 2. GET /api/symbol/{code}/kap raises exception
            def mock_connect_raise(*args, **kwargs):
                raise RuntimeError("Connection lost")

            monkeypatch.setattr(store, "_connect", mock_connect_raise)

            resp_kap_err = await client.get("/api/symbol/THYAO/kap")
            assert resp_kap_err.status_code == 500

            # 3. GET /api/symbol/{code}/ohlcv raises exception (returns [])
            def mock_fetch_history_raise(*args, **kwargs):
                raise RuntimeError("WebSocket failed")

            monkeypatch.setattr(
                "tvcli.layers.ohlcv.fetch_history", mock_fetch_history_raise
            )

            resp_ohlcv_err = await client.get("/api/symbol/THYAO/ohlcv")
            assert resp_ohlcv_err.status_code == 200
            assert resp_ohlcv_err.json() == []

    anyio.run(run_symbol_tests)


def test_alerts_router_boost(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    app = create_app(store=store)

    # Seed an event
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO freefloat_events(
                report_date, code, event_type, severity,
                metric_value, threshold_value, payload_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-10",
                "THYAO",
                "liquidity_risk_low_float",
                "high",
                8.5,
                10.0,
                '{"ratio": 8.5}',
                "sent",
            ),
        )

    async def run_alerts_tests() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # 1. Test query with severity, status_filter, event_type parameters
            resp = await client.get(
                "/api/alerts/history?severity=high&status_filter=sent&event_type=liquidity_risk_low_float"
            )
            assert resp.status_code == 200
            assert len(resp.json()) == 1

            # 2. Exception handling (mock store._connect to raise exception)
            def mock_connect_raise(*args, **kwargs):
                raise RuntimeError("DB failed")

            monkeypatch.setattr(store, "_connect", mock_connect_raise)

            resp_err = await client.get("/api/alerts/history")
            assert resp_err.status_code == 500

    anyio.run(run_alerts_tests)


def test_images_router_boost(monkeypatch, tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    app = create_app(store=store)

    # Seed mock stats to return a valid latest date
    def mock_stats(*args, **kwargs):
        return {"last_report_date": "2026-06-10"}

    monkeypatch.setattr(store, "archive_stats", mock_stats)

    async def run_images_tests() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # Mock run_dashboard to write a dummy png file
            def mock_run_dashboard(req: Any, store: Any):
                req.out.write_bytes(b"\x89PNG\r\n\x1a\n")

            monkeypatch.setattr(
                "tvcli.floatdash.routers.images.run_dashboard", mock_run_dashboard
            )

            # 1. Generate market image
            resp1 = await client.get("/img/market.png")
            assert resp1.status_code == 200
            etag = resp1.headers.get("ETag")
            assert etag is not None

            # 2. Test ETag 304 Not Modified
            resp2 = await client.get("/img/market.png", headers={"if-none-match": etag})
            assert resp2.status_code == 304

            # 3. Test serving from memory cache (call again without ETag header)
            resp3 = await client.get("/img/market.png")
            assert resp3.status_code == 200

            # 4. Test generate raises NotFoundError
            def mock_run_dashboard_nf(*args, **kwargs):
                raise NotFoundError("Symbols not found")

            monkeypatch.setattr(
                "tvcli.floatdash.routers.images.run_dashboard", mock_run_dashboard_nf
            )

            # Clear cache for key first
            from tvcli.floatdash.routers.images import cache

            cache.clear()

            resp_nf = await client.get("/img/market.png")
            assert resp_nf.status_code == 404

            # 5. Test generate raises other Exception
            def mock_run_dashboard_err(*args, **kwargs):
                raise RuntimeError("Internal crash")

            monkeypatch.setattr(
                "tvcli.floatdash.routers.images.run_dashboard", mock_run_dashboard_err
            )

            resp_err = await client.get("/img/market.png")
            assert resp_err.status_code == 500

            # 6. Generate symbol image
            monkeypatch.setattr(
                "tvcli.floatdash.routers.images.run_dashboard", mock_run_dashboard
            )
            resp_sym1 = await client.get("/img/symbol/THYAO.png")
            assert resp_sym1.status_code == 200
            etag_sym = resp_sym1.headers.get("ETag")
            assert etag_sym is not None

            # 7. Test ETag 304 for symbol image
            resp_sym2 = await client.get(
                "/img/symbol/THYAO.png", headers={"if-none-match": etag_sym}
            )
            assert resp_sym2.status_code == 304

            # 8. Test serve symbol image from memory cache
            resp_sym3 = await client.get("/img/symbol/THYAO.png")
            assert resp_sym3.status_code == 200

            # 9. Test symbol image raises NotFoundError
            monkeypatch.setattr(
                "tvcli.floatdash.routers.images.run_dashboard", mock_run_dashboard_nf
            )
            cache.clear()
            resp_sym_nf = await client.get("/img/symbol/THYAO.png")
            assert resp_sym_nf.status_code == 404

            # 10. Test symbol image raises other Exception
            monkeypatch.setattr(
                "tvcli.floatdash.routers.images.run_dashboard", mock_run_dashboard_err
            )
            resp_sym_err = await client.get("/img/symbol/THYAO.png")
            assert resp_sym_err.status_code == 500

            # 11. Run cleanup function directly
            from tvcli.floatdash.routers.images import cleanup

            cleanup()

    anyio.run(run_images_tests)


def test_ws_router_exception(tmp_path: Path) -> None:
    # Test that WebSocket handler handles general exceptions gracefully

    store = ArchiveStore(tmp_path / "archive.sqlite3")
    app = create_app(store=store)

    class MockWebSocket:
        def __init__(self, app_state):
            self.app = type("State", (), {"state": app_state})
            self.accepted = False
            self.closed = False

        async def accept(self):
            self.accepted = True

        async def receive_text(self):
            # Raise an unexpected exception
            raise RuntimeError("WebSocket unexpected disconnect")

    # Call the endpoint directly or use TestClient with a mock receive_text
    # Since we want to cover the `except Exception as e` block, let's call
    # the endpoint directly
    from tvcli.floatdash.routers.ws import websocket_endpoint

    ws = MockWebSocket(app.state)
    # Use anyio to run the async websocket_endpoint
    anyio.run(websocket_endpoint, ws)  # type: ignore
