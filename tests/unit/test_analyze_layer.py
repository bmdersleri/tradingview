from __future__ import annotations

from pathlib import Path

import pytest

from tvcli.errors import UsageError
from tvcli.layers import analyze, ohlcv


def _fake_bars(n: int = 60) -> tuple[ohlcv.OhlcvBar, ...]:
    base = 1_700_000_000
    out = []
    for i in range(n):
        price = 100.0 + (i % 7) - (i % 3) * 0.5 + i * 0.1
        out.append(
            ohlcv.OhlcvBar(
                time=base + i * 86_400,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=1000.0 + i,
            )
        )
    return tuple(out)


def test_run_analysis_renders_png(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "chart.png"
    bars = _fake_bars()
    monkeypatch.setattr("tvcli.layers.analyze.fetch_bars_query", lambda request: bars)

    payload = analyze.run_analysis(
        analyze.AnalyzeRequest(
            symbol="BIST:THYAO",
            interval="1d",
            out=out,
            bars=60,
            indicators=("wma:10", "rsi:14", "macd:12:26:9"),
        )
    )

    assert payload["symbol"] == "BIST:THYAO"
    assert payload["bars"] == 60
    assert payload["bytes"] > 0
    assert payload["path"] == str(out.resolve())
    assert out.exists()
    # Real PNG magic bytes — proves matplotlib actually rendered a file.
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    specs = [ind["spec"] for ind in payload["indicators"]]
    assert specs == ["wma:10", "rsi:14", "macd:12:26:9"]
    wma = next(i for i in payload["indicators"] if i["kind"] == "wma")
    assert wma["last"] is not None


def test_run_analysis_default_candle_and_volume(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "candle.png"
    monkeypatch.setattr(
        "tvcli.layers.analyze.fetch_bars_query", lambda request: _fake_bars()
    )
    payload = analyze.run_analysis(
        analyze.AnalyzeRequest(
            symbol="X:Y", interval="1d", out=out, indicators=("wma:10",)
        )
    )
    assert payload["style"] == "candle"
    assert payload["volume"] is True
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_run_analysis_line_without_volume(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "line.png"
    monkeypatch.setattr(
        "tvcli.layers.analyze.fetch_bars_query", lambda request: _fake_bars()
    )
    payload = analyze.run_analysis(
        analyze.AnalyzeRequest(
            symbol="X:Y",
            interval="1d",
            out=out,
            indicators=("ema:10", "macd:12:26:9"),
            style="line",
            volume=False,
        )
    )
    assert payload["style"] == "line"
    assert payload["volume"] is False
    assert out.exists()


def test_run_analysis_rejects_bad_style(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "tvcli.layers.analyze.fetch_bars_query", lambda request: _fake_bars()
    )
    with pytest.raises(UsageError):
        analyze.run_analysis(
            analyze.AnalyzeRequest(
                symbol="X:Y", interval="1d", out=tmp_path / "x.png", style="heikin"
            )
        )


def test_run_analysis_defaults_to_wma200(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "default.png"
    monkeypatch.setattr(
        "tvcli.layers.analyze.fetch_bars_query", lambda request: _fake_bars(250)
    )
    payload = analyze.run_analysis(
        analyze.AnalyzeRequest(symbol="X:Y", interval="1d", out=out)
    )
    assert payload["indicators"][0]["spec"] == "wma:200"
    assert out.exists()


def test_run_analysis_rejects_bad_spec(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "tvcli.layers.analyze.fetch_bars_query", lambda request: _fake_bars()
    )
    with pytest.raises(UsageError):
        analyze.run_analysis(
            analyze.AnalyzeRequest(
                symbol="X:Y",
                interval="1d",
                out=tmp_path / "bad.png",
                indicators=("frobnicate:9",),
            )
        )
