"""Pure technical-analysis payload shaping."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import NotFoundError, TvcliError, UsageError


@dataclass(frozen=True, slots=True)
class TaRequest:
    symbol: str
    interval: str
    screener: str


@dataclass(frozen=True, slots=True)
class TaSnapshot:
    symbol: str
    interval: str
    summary: dict[str, Any]
    oscillators: dict[str, Any]
    moving_averages: dict[str, Any]
    indicators: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaBackend:
    TA_Handler: type[Any]
    get_multiple_analysis: Any
    Interval: Any


def derive_screener(symbol: str, screener: str = "auto") -> str:
    if screener != "auto":
        return screener
    prefix = symbol.split(":", 1)[0].upper()
    if prefix == "BIST":
        return "turkey"
    if prefix in {"NASDAQ", "NYSE", "AMEX"}:
        return "america"
    if prefix in {"BINANCE", "BYBIT", "KUCOIN"}:
        return "crypto"
    if prefix in {"FX_IDC", "OANDA"}:
        return "forex"
    raise NotFoundError(
        f"Unable to derive screener for {symbol}",
        hint="Pass `--screener` explicitly.",
    )


def _load_backend() -> TaBackend:
    try:
        module = importlib.import_module("tradingview_ta")
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise TvcliError(
            "TradingView TA backend is unavailable.",
            hint="Install the `market` extra to enable ta get/multi/matrix.",
        ) from exc
    return TaBackend(
        TA_Handler=module.TA_Handler,
        get_multiple_analysis=module.get_multiple_analysis,
        Interval=module.Interval,
    )


def _read_block(source: Any, key: str) -> dict[str, Any]:
    if isinstance(source, Mapping):
        block = source.get(key, {})
    else:
        block = getattr(source, key, {})
    if isinstance(block, Mapping):
        return dict(block)
    return {
        "recommendation": getattr(block, "recommendation", None),
        "buy": getattr(block, "buy", None),
        "neutral": getattr(block, "neutral", None),
        "sell": getattr(block, "sell", None),
    }


def _read_indicators(source: Any) -> dict[str, Any]:
    if isinstance(source, Mapping):
        indicators = source.get("indicators", {})
    else:
        indicators = getattr(source, "indicators", {})
    if isinstance(indicators, Mapping):
        return dict(indicators)
    if hasattr(indicators, "__dict__"):
        return dict(indicators.__dict__)
    return dict(indicators)


def build_snapshot_payload(request: TaRequest, analysis: Any) -> dict[str, Any]:
    return {
        "symbol": request.symbol,
        "interval": request.interval,
        "summary": _read_block(analysis, "summary"),
        "oscillators": _read_block(analysis, "oscillators"),
        "moving_averages": _read_block(analysis, "moving_averages"),
        "indicators": _read_indicators(analysis),
    }


def build_multi_payload(
    symbols: tuple[str, ...],
    interval: str,
    snapshots: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "symbols": list(symbols),
        "interval": interval,
        "returned": len(snapshots),
        "snapshots": [dict(snapshot) for snapshot in snapshots],
    }


def build_matrix_payload(
    symbol: str,
    intervals: tuple[str, ...],
    snapshots: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "intervals": list(intervals),
        "returned": len(snapshots),
        "snapshots": [dict(snapshot) for snapshot in snapshots],
    }


def unsupported_backend(name: str) -> TvcliError:
    return TvcliError(
        f"{name} backend is not wired yet.",
        hint="Install the market dependencies and complete the TradingView adapter.",
    )


def _split_symbol(symbol: str) -> tuple[str, str]:
    if ":" not in symbol:
        return "", symbol
    exchange, ticker = symbol.split(":", 1)
    return exchange, ticker


def _interval_value(backend: TaBackend, interval: str) -> str:
    mapping = {
        "1m": backend.Interval.INTERVAL_1_MINUTE,
        "5m": backend.Interval.INTERVAL_5_MINUTES,
        "15m": backend.Interval.INTERVAL_15_MINUTES,
        "30m": backend.Interval.INTERVAL_30_MINUTES,
        "1h": backend.Interval.INTERVAL_1_HOUR,
        "2h": backend.Interval.INTERVAL_2_HOURS,
        "4h": backend.Interval.INTERVAL_4_HOURS,
        "1d": backend.Interval.INTERVAL_1_DAY,
        "1W": backend.Interval.INTERVAL_1_WEEK,
        "1M": backend.Interval.INTERVAL_1_MONTH,
    }
    if interval not in mapping:
        raise UsageError(
            f"Unsupported interval: {interval}",
            hint="Use one of: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1W, 1M.",
        )
    return str(mapping[interval])


def run_ta_get(request: TaRequest) -> Any:
    backend = _load_backend()
    exchange, ticker = _split_symbol(request.symbol)
    handler = backend.TA_Handler(
        screener=request.screener,
        exchange=exchange,
        symbol=ticker,
        interval=_interval_value(backend, request.interval),
    )
    return handler.get_analysis()


def run_ta_multi(
    symbols: tuple[str, ...],
    interval: str,
    screener: str | None = None,
) -> tuple[Any, ...]:
    backend = _load_backend()
    symbol_list = list(symbols)
    if not symbol_list:
        raise UsageError(
            "At least one symbol is required.",
            hint="Pass one or more symbols.",
        )
    resolved_screener = screener or derive_screener(symbol_list[0], "auto")
    analyses = backend.get_multiple_analysis(
        resolved_screener,
        _interval_value(backend, interval),
        symbol_list,
    )
    return tuple(analyses.get(symbol.upper()) for symbol in symbol_list)


def run_ta_matrix(symbol: str, intervals: tuple[str, ...]) -> tuple[Any, ...]:
    backend = _load_backend()
    exchange, ticker = _split_symbol(symbol)
    resolved_screener = derive_screener(symbol, "auto")
    results: list[Any] = []
    for interval in intervals:
        handler = backend.TA_Handler(
            screener=resolved_screener,
            exchange=exchange,
            symbol=ticker,
            interval=_interval_value(backend, interval),
        )
        results.append(handler.get_analysis())
    return tuple(results)
