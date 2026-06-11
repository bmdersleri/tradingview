from __future__ import annotations

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.commands import serve


def test_serve_webhook_command_routes_to_runner(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_runner(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(serve, "run_webhook_server", fake_runner)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "serve",
            "webhook",
            "--secret",
            "abc",
            "--port",
            "9000",
            "--sink",
            "telegram",
            "--telegram-token",
            "token",
            "--telegram-chat-id",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert captured["secret"] == "abc"
    assert captured["port"] == 9000
    assert captured["sink"] == "telegram"
