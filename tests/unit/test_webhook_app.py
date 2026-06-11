from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tvcli.webhook.app import create_app


def test_webhook_app_healthz_and_file_append(tmp_path: Path) -> None:
    alerts_path = tmp_path / "alerts.jsonl"
    app = create_app(secret="secret", sink="file", alerts_path=alerts_path)
    with TestClient(app) as client:
        health = client.get("/healthz")
        rejected = client.post("/hook/wrong", json={"x": 1})
        accepted = client.post(
            "/hook/secret", json={"symbol": "BIST:THYAO", "price": 320}
        )
        raw = client.post(
            "/hook/secret",
            data="plain alert body",
            headers={"content-type": "text/plain"},
        )

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
    with TestClient(app) as test_client:
        response = test_client.post(
            "/hook/secret",
            json={"symbol": "BIST:THYAO", "price": 320, "message": "crossing"},
        )

    assert response.status_code == 200
    assert alerts_path.exists()
    assert client.requests[0][0] == "https://api.telegram.org/botbot-token/sendMessage"
    assert "TradingView alert" in client.requests[0][1]["text"]
