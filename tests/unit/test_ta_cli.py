from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.commands import ta
from tvcli.layers.ta import TaRequest


def test_ta_get_multi_and_matrix_json(monkeypatch) -> None:
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "ta_response.json"
        ).read_text(encoding="utf-8")
    )

    def fake_analysis_query(request: TaRequest) -> dict[str, object]:
        assert request.screener == "turkey"
        return fixture

    def fake_multiple_analysis_query(
        symbols: tuple[str, ...], interval: str
    ) -> tuple[dict[str, object], ...]:
        return tuple(fixture for _ in symbols)

    monkeypatch.setattr(ta, "analysis_query", fake_analysis_query)
    monkeypatch.setattr(ta, "multiple_analysis_query", fake_multiple_analysis_query)

    runner = CliRunner()
    get_result = runner.invoke(
        app, ["--json", "ta", "get", "BIST:THYAO", "--interval", "1d"]
    )
    multi_result = runner.invoke(
        app,
        ["ta", "multi", "BIST:THYAO", "NASDAQ:NVDA", "--json"],
    )
    matrix_result = runner.invoke(
        app,
        ["ta", "matrix", "BIST:THYAO", "--intervals", "1h,4h,1d", "--json"],
    )

    assert get_result.exit_code == 0
    assert multi_result.exit_code == 0
    assert matrix_result.exit_code == 0
    assert '"command": "ta.get"' in get_result.output
    assert '"returned": 2' in multi_result.output
    assert '"returned": 3' in matrix_result.output
