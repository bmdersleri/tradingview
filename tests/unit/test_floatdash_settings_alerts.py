from __future__ import annotations

from pathlib import Path

import anyio
import httpx

from tvcli.config import load_config
from tvcli.floatdash.app import create_app
from tvcli.layers.freefloat_archive import ArchiveStore


def test_settings_and_alerts_apis(monkeypatch, tmp_path: Path) -> None:
    # Setup isolated config and archive files
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    archive_file = tmp_path / "archive.sqlite3"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
    monkeypatch.setattr("tvcli.config.default_config_path", lambda: config_file)

    store = ArchiveStore(archive_file)
    app = create_app(store=store)

    # Seed some events into the database
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
        conn.execute(
            """
            INSERT INTO freefloat_events(
                report_date, code, event_type, severity,
                metric_value, threshold_value, payload_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-11",
                "GARAN",
                "ratio_jump_up",
                "medium",
                5.2,
                5.0,
                '{"delta": 5.2}',
                "failed",
            ),
        )
        conn.execute(
            """
            INSERT INTO kap_disclosures (
                code, disclosure_date, title, summary, url, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "THYAO",
                "2026-06-10",
                "Fiili Dolaşım Pay Oranı Değişikliği",
                "Güncelleme",
                "http://kap.org/thyao",
                "2026-06-10T12:00:00",
            ),
        )

    async def run_test() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # Test default settings
            resp = await client.get("/api/settings")
            assert resp.status_code == 200
            data = resp.json()
            assert data["telegram_token"] == ""
            assert data["telegram_chat_id"] == ""
            assert data["webhook_url"] == ""
            assert data["low_float_threshold"] == 20.0

            # Test updating settings
            update_payload = {
                "telegram_token": "123456789:ABC_DEF_GHI",
                "telegram_chat_id": "-100111",
                "webhook_url": "http://example.com/webhook",
                "low_float_threshold": 18.5,
                "severe_low_float_threshold": 8.5,
                "ratio_jump_threshold": 6.2,
            }
            resp_update = await client.post("/api/settings/update", json=update_payload)
            assert resp_update.status_code == 200
            assert resp_update.json()["status"] == "success"

            # Check config file saved correctly
            cfg = load_config(config_file)
            assert cfg["alerts"]["telegram-token"] == "123456789:ABC_DEF_GHI"
            assert cfg["alerts"]["telegram-chat-id"] == "-100111"
            assert cfg["alerts"]["webhook-url"] == "http://example.com/webhook"
            assert cfg["alerts"]["low-float-threshold"] == 18.5

            # Test settings masking on subsequent GET
            resp_get2 = await client.get("/api/settings")
            assert resp_get2.status_code == 200
            data2 = resp_get2.json()
            assert data2["telegram_token"].startswith("1234")
            assert data2["telegram_token"].endswith("_GHI")
            assert "*" in data2["telegram_token"]

            # Test updating other fields while keeping masked token
            update_payload2 = {
                "telegram_token": data2[
                    "telegram_token"
                ],  # Resubmitting the masked token
                "telegram_chat_id": "-100222",
                "webhook_url": "http://example.com/webhook",
                "low_float_threshold": 18.5,
                "severe_low_float_threshold": 8.5,
                "ratio_jump_threshold": 6.2,
            }
            resp_update2 = await client.post(
                "/api/settings/update", json=update_payload2
            )
            assert resp_update2.status_code == 200

            # The token in config should remain unmasked
            cfg2 = load_config(config_file)
            assert cfg2["alerts"]["telegram-token"] == "123456789:ABC_DEF_GHI"
            assert cfg2["alerts"]["telegram-chat-id"] == "-100222"

            # Test Alert History query
            resp_alerts = await client.get("/api/alerts/history")
            assert resp_alerts.status_code == 200
            alerts = resp_alerts.json()
            assert len(alerts) == 2
            assert alerts[0]["code"] == "GARAN"
            assert alerts[0]["status"] == "failed"
            assert alerts[1]["code"] == "THYAO"
            assert alerts[1]["status"] == "sent"

            # Test Alert History filtering by symbol
            resp_filtered = await client.get("/api/alerts/history?symbol=THYAO")
            assert resp_filtered.status_code == 200
            filtered = resp_filtered.json()
            assert len(filtered) == 1
            assert filtered[0]["code"] == "THYAO"

            # Test Alert History filtering by status
            resp_failed = await client.get("/api/alerts/history?status_filter=failed")
            assert resp_failed.status_code == 200
            failed = resp_failed.json()
            assert len(failed) == 1
            assert failed[0]["code"] == "GARAN"

            # Test KAP endpoint for THYAO
            resp_kap = await client.get("/api/symbol/THYAO/kap")
            assert resp_kap.status_code == 200
            kaps = resp_kap.json()
            assert len(kaps) == 1
            assert kaps[0]["code"] == "THYAO"
            assert kaps[0]["title"] == "Fiili Dolaşım Pay Oranı Değişikliği"
            assert kaps[0]["summary"] == "Güncelleme"
            assert kaps[0]["url"] == "http://kap.org/thyao"

            # Test KAP endpoint for GARAN (should be empty)
            resp_kap_empty = await client.get("/api/symbol/GARAN/kap")
            assert resp_kap_empty.status_code == 200
            assert len(resp_kap_empty.json()) == 0

    anyio.run(run_test)
