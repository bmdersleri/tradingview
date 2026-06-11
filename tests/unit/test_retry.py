from __future__ import annotations

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.errors import NetworkError


def test_global_retry_flags_retry_retryable_errors(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fields_query(market: str, search: str | None) -> tuple[object, ...]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise NetworkError("temporary upstream failure", hint="Retry later.")
        return ({"name": "RSI"},)

    monkeypatch.setattr("tvcli.commands.data.fields_query", fake_fields_query)
    monkeypatch.setattr(
        "tvcli.commands.data.screener.build_fields_payload",
        lambda market, fields: {"market": market, "fields": list(fields)},
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--retries",
            "1",
            "--backoff",
            "0",
            "data",
            "fields",
            "--market",
            "turkey",
            "--search",
            "rsi",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert calls["count"] == 2
    assert '"command": "data.fields"' in result.output
