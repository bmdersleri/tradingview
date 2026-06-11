from __future__ import annotations

from dataclasses import dataclass

from tvcli.layers import ta


@dataclass
class FakeHandler:
    screener: str
    exchange: str
    symbol: str
    interval: str

    def get_analysis(self) -> dict[str, object]:
        return {
            "summary": {"recommendation": "BUY", "buy": 1, "neutral": 2, "sell": 3},
            "oscillators": {
                "recommendation": "NEUTRAL",
                "buy": 4,
                "neutral": 5,
                "sell": 6,
            },
            "moving_averages": {
                "recommendation": "STRONG_BUY",
                "buy": 7,
                "neutral": 8,
                "sell": 9,
            },
            "indicators": {"RSI": 56.2},
        }


class FakeInterval:
    INTERVAL_1_MINUTE = "1m"
    INTERVAL_5_MINUTES = "5m"
    INTERVAL_15_MINUTES = "15m"
    INTERVAL_30_MINUTES = "30m"
    INTERVAL_1_HOUR = "1h"
    INTERVAL_2_HOURS = "2h"
    INTERVAL_4_HOURS = "4h"
    INTERVAL_1_DAY = "1d"
    INTERVAL_1_WEEK = "1W"
    INTERVAL_1_MONTH = "1M"


def test_run_ta_get_and_matrix(monkeypatch) -> None:
    handlers: list[FakeHandler] = []

    def fake_get_multiple_analysis(
        screener: str,
        interval: str,
        symbols: list[str],
        additional_indicators: list[str] | None = None,
        timeout: float | None = None,
        proxies: dict[str, str] | None = None,
    ) -> dict[str, object]:
        return {symbol: {"symbol": symbol, "interval": interval} for symbol in symbols}

    def fake_handler(**kwargs: object) -> FakeHandler:
        handler = FakeHandler(**kwargs)  # type: ignore[arg-type]
        handlers.append(handler)
        return handler

    monkeypatch.setattr(
        ta,
        "_load_backend",
        lambda: ta.TaBackend(
            TA_Handler=fake_handler,
            get_multiple_analysis=fake_get_multiple_analysis,
            Interval=FakeInterval,
        ),
    )

    analysis = ta.run_ta_get(
        ta.TaRequest(symbol="BIST:THYAO", interval="1d", screener="turkey")
    )
    multi = ta.run_ta_multi(("BIST:THYAO", "NASDAQ:NVDA"), "1d")
    matrix = ta.run_ta_matrix("BIST:THYAO", ("1h", "1d"))

    assert analysis["indicators"]["RSI"] == 56.2
    assert multi[0]["symbol"] == "BIST:THYAO"
    assert matrix[0]["indicators"]["RSI"] == 56.2
    assert handlers[0].interval == "1d"
    assert handlers[1].interval == "1h"
