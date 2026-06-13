from __future__ import annotations

import json
from pathlib import Path

import anyio
import httpx

from tvcli.webhook.app import create_app


def test_webhook_app_healthz_and_file_append(tmp_path: Path) -> None:
    alerts_path = tmp_path / "alerts.jsonl"
    app = create_app(secret="secret", sink="file", alerts_path=alerts_path)

    async def run_requests() -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return (
                await client.get("/healthz"),
                await client.post("/hook/wrong", json={"x": 1}),
                await client.post(
                    "/hook/secret", json={"symbol": "BIST:THYAO", "price": 320}
                ),
                await client.post(
                    "/hook/secret",
                    content="plain alert body",
                    headers={"content-type": "text/plain"},
                ),
            )

    health, rejected, accepted, raw = anyio.run(run_requests)

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert raw.status_code == 200

    lines = alerts_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["body"]["symbol"] == "BIST:THYAO"
    assert second["body"] == "plain alert body"


class FakeTelegramClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, *, json: dict[str, object]) -> httpx.Response:
        self.requests.append((url, json))
        return httpx.Response(200, json={"ok": True})


def test_webhook_telegram_dispatch(tmp_path: Path) -> None:
    alerts_path = tmp_path / "alerts.jsonl"
    client = FakeTelegramClient()
    app = create_app(
        secret="secret",
        sink="telegram",
        alerts_path=alerts_path,
        telegram_token="bot-token",
        telegram_chat_id="12345",
        telegram_client=client,
    )

    async def send_alert() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as test_client:
            return await test_client.post(
                "/hook/secret",
                json={"symbol": "BIST:THYAO", "price": 320, "message": "crossing"},
            )

    response = anyio.run(send_alert)

    assert response.status_code == 200
    assert alerts_path.exists()
    assert client.requests[0][0] == "https://api.telegram.org/botbot-token/sendMessage"
    assert "TradingView alert" in client.requests[0][1]["text"]


def test_dashboard_routes(tmp_path: Path) -> None:
    from unittest.mock import patch

    app = create_app(secret="secret", sink="file")

    async def run_requests() -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            with patch("tvcli.layers.float_dashboard.run_dashboard") as mock_run:

                def fake_run(req: any) -> None:
                    req.out.write_bytes(b"fake png content")

                mock_run.side_effect = fake_run

                return (
                    await client.get("/dashboard"),
                    await client.get("/dashboard/market"),
                    await client.get("/dashboard/symbol/THYAO"),
                )

    html_resp, market_resp, symbol_resp = anyio.run(run_requests)

    assert html_resp.status_code == 200
    assert "TVCLI Free-Float Analytics" in html_resp.text
    assert market_resp.status_code == 200
    assert market_resp.content == b"fake png content"
    assert symbol_resp.status_code == 200
    assert symbol_resp.content == b"fake png content"


def test_dashboard_routes_error(tmp_path: Path) -> None:
    from unittest.mock import patch

    app = create_app(secret="secret", sink="file")

    async def run_requests() -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            with patch("tvcli.layers.float_dashboard.run_dashboard") as mock_run:
                from tvcli.errors import NotFoundError

                mock_run.side_effect = NotFoundError("Symbol not found")

                return (await client.get("/dashboard/symbol/INVALID"),)

    err_resp = anyio.run(run_requests)[0]
    assert err_resp.status_code == 404
