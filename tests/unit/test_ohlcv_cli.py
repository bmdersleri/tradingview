from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.layers.ohlcv import OhlcvBar


def test_ohlcv_get_and_export(monkeypatch, tmp_path: Path) -> None:
    history = (
        OhlcvBar(
            time=1718064000,
            open=310.0,
            high=315.5,
            low=308.0,
            close=312.5,
            volume=18234567.0,
        ),
    )
    monkeypatch.setattr(
        "tvcli.commands.ohlcv.fetch_history_query",
        lambda request: history,
    )

    runner = CliRunner()
    get_result = runner.invoke(
        app,
        ["ohlcv", "get", "BIST:THYAO", "--interval", "1d", "--bars", "1", "--json"],
    )
    export_path = tmp_path / "bars.csv"
    export_result = runner.invoke(
        app,
        [
            "ohlcv",
            "export",
            "BIST:THYAO",
            "--interval",
            "1d",
            "--bars",
            "1",
            "--out",
            str(export_path),
            "--json",
        ],
    )

    assert get_result.exit_code == 0
    assert export_result.exit_code == 0
    assert '"command": "ohlcv.get"' in get_result.output
    assert '"command": "ohlcv.export"' in export_result.output
    assert export_path.exists()
