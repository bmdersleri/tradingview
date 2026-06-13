from __future__ import annotations

import importlib

import pytest

from tvcli.errors import TvcliError, UsageError
from tvcli.layers import ta


def test_derive_screener_explicit_and_prefixes() -> None:
    # Line 39: screener != "auto"
    assert ta.derive_screener("BIST:THYAO", "america") == "america"

    # Line 46: crypto prefix
    assert ta.derive_screener("BINANCE:BTCUSDT") == "crypto"
    assert ta.derive_screener("bybit:ETHUSDT") == "crypto"

    # Line 48: forex prefix
    assert ta.derive_screener("FX_IDC:EURUSD") == "forex"
    assert ta.derive_screener("oanda:GBPUSD") == "forex"


def test_load_backend_import_error(monkeypatch) -> None:
    # Lines 56-62: importlib.import_module raises ImportError
    def mock_import_module(name):
        if name == "tradingview_ta":
            raise ImportError("Mocked import error")
        return importlib.import_module(name)

    monkeypatch.setattr(importlib, "import_module", mock_import_module)
    with pytest.raises(TvcliError) as exc_info:
        ta._load_backend()
    assert "TradingView TA backend is unavailable" in str(exc_info.value)


def test_read_block_and_indicators_non_mapping() -> None:
    # Test _read_block with non-Mapping object (Lines 74, 77)
    class FakeAnalysis:
        def __init__(self):
            # Create instance attributes so they populate __dict__
            self.summary = type("FakeBlock", (), {})()
            self.summary.recommendation = "BUY"
            self.summary.buy = 10
            self.summary.neutral = 5
            self.summary.sell = 1

            self.indicators = type("FakeIndicators", (), {})()
            self.indicators.RSI = 56.2

    analysis = FakeAnalysis()
    res_block = ta._read_block(analysis, "summary")
    assert res_block["recommendation"] == "BUY"
    assert res_block["buy"] == 10

    # Test _read_indicators with non-Mapping object having __dict__ (Lines 89, 92-94)
    res_ind = ta._read_indicators(analysis)
    assert res_ind["RSI"] == 56.2


def test_unsupported_backend() -> None:
    # Line 135
    exc = ta.unsupported_backend("dummy")
    assert "dummy backend is not wired yet" in str(exc)


def test_split_symbol_no_colon() -> None:
    # Line 143: split_symbol without colon
    assert ta._split_symbol("THYAO") == ("", "THYAO")


def test_interval_value_unsupported() -> None:
    # Line 162: unsupported interval
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

    class FakeBackend:
        Interval = FakeInterval

    backend = FakeBackend()
    with pytest.raises(UsageError) as exc_info:
        ta._interval_value(backend, "invalid-interval")  # type: ignore
    assert "Unsupported interval" in str(exc_info.value)


def test_run_ta_multi_empty() -> None:
    # Line 189: run_ta_multi with empty symbols
    with pytest.raises(UsageError) as exc_info:
        ta.run_ta_multi((), "1d")
    assert "At least one symbol is required" in str(exc_info.value)
