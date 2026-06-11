from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.commands import data
from tvcli.layers.screener import FieldInfo, ScreenRequest, ScreenResult


def test_data_screen_json(monkeypatch) -> None:
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "screener_response.json"
        ).read_text(encoding="utf-8")
    )

    def fake_screen_query(request: ScreenRequest) -> ScreenResult:
        assert request.market == "turkey"
        assert request.limit == 20
        return ScreenResult(
            rows=tuple(fixture["rows"]), total_matches=fixture["total_matches"]
        )

    monkeypatch.setattr(data, "screen_query", fake_screen_query)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--json",
            "data",
            "screen",
            "--market",
            "turkey",
            "--select",
            "name,close,volume,RSI,market_cap_basic",
            "--where",
            "RSI<30;volume>1000000",
            "--order-by",
            "volume",
            "--desc",
            "--limit",
            "20",
        ],
    )

    assert result.exit_code == 0
    assert '"command": "data.screen"' in result.output
    assert '"total_matches": 412' in result.output


def test_data_fields_search_and_quote(monkeypatch) -> None:
    monkeypatch.setattr(
        data,
        "fields_query",
        lambda market, search: (
            FieldInfo(name="RSI", type="number", description="relative strength"),
        ),
    )
    monkeypatch.setattr(
        data,
        "search_query",
        lambda query, market: ({"ticker": "BIST:THYAO", "description": "THYAO"},),
    )
    monkeypatch.setattr(
        data,
        "quote_query",
        lambda symbols: ({"ticker": symbols[0], "close": 312.5},),
    )

    runner = CliRunner()
    fields = runner.invoke(app, ["data", "fields", "--market", "turkey", "--json"])
    search = runner.invoke(app, ["data", "search", "THYAO", "--json"])
    quote = runner.invoke(app, ["data", "quote", "BIST:THYAO", "--json"])

    assert fields.exit_code == 0
    assert search.exit_code == 0
    assert quote.exit_code == 0
    assert '"returned": 1' in fields.output
    assert '"command": "data.search"' in search.output
    assert '"command": "data.quote"' in quote.output
