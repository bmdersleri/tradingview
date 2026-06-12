from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.layers.chart import (
    _looks_like_login_wall,
    chart_url,
    wait_for_canvas_stability,
)


class FakeLocator:
    def __init__(self) -> None:
        self.calls = 0

    def count(self) -> int:
        return 1

    @property
    def first(self) -> FakeLocator:
        return self

    def screenshot(self) -> bytes:
        self.calls += 1
        return b"stable"


class FakePage:
    def __init__(self) -> None:
        self.loc = FakeLocator()
        self.waits = 0

    def locator(self, selector: str) -> FakeLocator:
        return self.loc

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1


def test_chart_url_and_canvas_stability() -> None:
    assert "symbol=BIST:THYAO" in chart_url("BIST:THYAO", "1d")

    page = FakePage()
    wait_for_canvas_stability(page, timeout_ms=2000, sample_ms=10)

    assert page.waits == 1


def test_login_wall_detection_ignores_normal_chart_copy() -> None:
    class Page:
        url = "https://www.tradingview.com/chart/example/?symbol=BIST%3ATHYAO"

        def content(self) -> str:
            return "<html><body>Save Trade Publish Log in menu copy</body></html>"

    assert _looks_like_login_wall(Page()) is False


def test_login_wall_detection_flags_challenge_url() -> None:
    class Page:
        url = "https://www.tradingview.com/accounts/signin/"

        def content(self) -> str:
            return "<html></html>"

    assert _looks_like_login_wall(Page()) is True


def test_chart_shot_command_uses_layer(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "chart.png"
    monkeypatch.setattr(
        "tvcli.commands.chart.shot_query",
        lambda request: {
            "path": str(out),
            "symbol": request.symbol,
            "interval": request.interval,
            "bytes": 123,
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "chart",
            "shot",
            "BIST:THYAO",
            "--interval",
            "1d",
            "--out",
            str(out),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"command": "chart.shot"' in result.output


def test_chart_analyze_command_uses_layer(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "chart.png"
    monkeypatch.setattr(
        "tvcli.commands.chart.analyze_query",
        lambda request: {
            "symbol": request.symbol,
            "interval": request.interval,
            "bars": 399,
            "style": request.style,
            "volume": request.volume,
            "indicators": [
                {"spec": s, "kind": s.split(":")[0], "period": 200, "last": 1.0}
                for s in request.indicators
            ],
            "path": str(out),
            "bytes": 456,
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "chart",
            "analyze",
            "BIST:THYAO",
            "--indicator",
            "wma:200",
            "--indicator",
            "rsi:14",
            "--style",
            "line",
            "--no-volume",
            "--out",
            str(out),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"command": "chart.analyze"' in result.output
    assert '"wma:200"' in result.output
    assert '"rsi:14"' in result.output
    assert '"style": "line"' in result.output
    assert '"volume": false' in result.output


def test_chart_signal_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "tvcli.commands.chart.signal_query",
        lambda request: {
            "symbol": request.symbol,
            "interval": request.interval,
            "bars": 250,
            "signal": "buy",
            "confidence": 0.62,
            "score": 0.4,
            "regime": {"kind": "trending_up", "strength": 0.8, "volatility": 0.01},
            "votes": [],
            "selected_indicators": ["sma:50", "sma:200"],
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["chart", "signal", "BIST:THYAO", "--bars", "250", "--json"]
    )

    assert result.exit_code == 0
    assert '"command": "chart.signal"' in result.output
    assert '"signal": "buy"' in result.output
    assert '"kind": "trending_up"' in result.output


def test_chart_signal_human_shows_liquidity(monkeypatch) -> None:
    monkeypatch.setattr(
        "tvcli.commands.chart.signal_query",
        lambda request: {
            "symbol": request.symbol,
            "interval": request.interval,
            "bars": 250,
            "signal": "buy",
            "confidence": 0.1,
            "score": 0.3,
            "regime": {"kind": "ranging", "strength": 0.2, "volatility": 0.01},
            "votes": [
                {"indicator": "rsi", "vote": 1, "strength": 0.5, "reason": "oversold"}
            ],
            "selected_indicators": ["rsi:14"],
            "liquidity": {"free_float": 0.12, "note": "Low free-float (0.1%): risk."},
        },
    )

    runner = CliRunner()
    # No --json: human-readable tables.
    result = runner.invoke(app, ["chart", "signal", "BIST:ENPRA", "--bars", "250"])

    assert result.exit_code == 0
    assert "free_float" in result.output
    assert "0.12" in result.output
    assert "Low free-float" in result.output
    # Votes table renders the per-indicator reason too.
    assert "oversold" in result.output


def test_chart_analyze_auto_attaches_signal(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "auto.png"

    def fake_analyze(request: object) -> dict[str, object]:
        assert request.auto is True
        return {
            "symbol": "BIST:THYAO",
            "interval": "1d",
            "bars": 250,
            "style": "candle",
            "volume": True,
            "indicators": [],
            "path": str(out),
            "bytes": 100,
            "signal": {"signal": "hold", "confidence": 0.1},
        }

    monkeypatch.setattr("tvcli.commands.chart.analyze_query", fake_analyze)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["chart", "analyze", "BIST:THYAO", "--auto", "--out", str(out), "--json"],
    )

    assert result.exit_code == 0
    assert '"command": "chart.analyze"' in result.output
    assert '"signal"' in result.output


def test_signal_query_enriches_bist_with_free_float(monkeypatch) -> None:
    from tvcli.commands import chart as chart_cmd
    from tvcli.layers import freefloat, ohlcv

    bars = tuple(
        ohlcv.OhlcvBar(
            time=1_700_000_000 + i * 86_400,
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=1000.0,
        )
        for i in range(260)
    )
    monkeypatch.setattr(
        "tvcli.commands.chart.ohlcv.fetch_history", lambda request: bars
    )
    # Thin float => liquidity note + confidence damp.
    monkeypatch.setattr(
        "tvcli.commands.chart.freefloat.lookup",
        lambda code: freefloat.FloatRecord(
            code="THYAO",
            isin="X",
            name="T",
            float_shares=1.0,
            capital=10.0,
            ratio=10.0,
            date="11.06.2026",
        ),
    )

    payload = chart_cmd.signal_query(
        chart_cmd.SignalRequest(symbol="BIST:THYAO", interval="1d", bars=260)
    )
    assert payload["liquidity"]["free_float"] == 10.0
    assert "manipulation" in payload["liquidity"]["note"].lower()


def test_signal_query_skips_free_float_for_non_bist(monkeypatch) -> None:
    from tvcli.commands import chart as chart_cmd
    from tvcli.layers import ohlcv

    bars = tuple(
        ohlcv.OhlcvBar(
            time=1_700_000_000 + i * 86_400,
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=1000.0,
        )
        for i in range(260)
    )
    monkeypatch.setattr(
        "tvcli.commands.chart.ohlcv.fetch_history", lambda request: bars
    )

    def boom(_code: str) -> object:
        raise AssertionError("free-float lookup must not run for non-BIST symbols")

    monkeypatch.setattr("tvcli.commands.chart.freefloat.lookup", boom)

    payload = chart_cmd.signal_query(
        chart_cmd.SignalRequest(symbol="NASDAQ:AAPL", interval="1d", bars=260)
    )
    assert payload["liquidity"]["free_float"] is None


def test_chart_shot_rejects_studies(monkeypatch, tmp_path: Path) -> None:
    # --studies is not supported on `shot`; it must fail fast (exit 2) before any
    # browser work and point the user at `chart analyze`.
    monkeypatch.setattr(
        "tvcli.layers.chart.require_session",
        lambda: (_ for _ in ()).throw(AssertionError("session must not be touched")),
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "chart",
            "shot",
            "BIST:THYAO",
            "--studies",
            "RSI,MACD",
            "--out",
            str(tmp_path / "x.png"),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert '"ok": false' in result.output
    assert "chart analyze" in result.output
