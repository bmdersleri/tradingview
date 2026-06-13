"""Chart command group."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from ..errors import TvcliError
from ..layers import analyze, chart, freefloat, ohlcv, signals
from ..output import build_envelope, emit, envelope_from_error, render_table
from ._helpers import (
    resolve_json_mode,
    resolve_retry_policy,
    run_command,
    run_with_retries,
)

app = typer.Typer(add_completion=False, help="Chart commands")


def shot_query(request: chart.ChartRequest) -> dict[str, Any]:
    return chart.shot_chart(request)


def _free_float_for(symbol: str) -> float | None:
    """VAP free-float ratio for a BIST symbol, or None when out of VAP's scope.

    For BIST symbols a VAP failure propagates (the user wants it to be a hard
    component there); non-BIST symbols have no VAP coverage, so the lookup is
    skipped entirely and free_float stays null.
    """
    if not freefloat.is_bist_symbol(symbol):
        return None
    record = freefloat.lookup(freefloat.normalize_code(symbol))
    return record.ratio if record is not None else None


def _risk_events_for(symbol: str) -> list[dict[str, Any]]:
    """Recent adverse free-float events from the local archive (no network).

    Read-only and local-first: events are materialized during sync. Non-BIST
    symbols have no VAP coverage; an empty/erroring archive yields no events and
    never blocks the signal.
    """
    if not freefloat.is_bist_symbol(symbol):
        return []
    from ..layers.freefloat_archive import ArchiveStore

    try:
        return ArchiveStore().latest_risk_events(freefloat.normalize_code(symbol))
    except Exception:
        return []


def analyze_query(request: analyze.AnalyzeRequest) -> dict[str, Any]:
    payload = analyze.run_analysis(request)
    # When --auto produced a signal block, enrich it with VAP free-float the same
    # way `chart signal` does. signals.py / analyze.py stay network-free; the
    # lookup lives here, at the command boundary, and the damping rules come from
    # signals.py so both paths stay in lockstep.
    signal_block = payload.get("signal")
    if isinstance(signal_block, dict):
        free_float = _free_float_for(request.symbol)
        if free_float is not None:
            liquidity = signal_block.setdefault("liquidity", {})
            liquidity["free_float"] = round(free_float, 2)
            liquidity["note"] = signals.low_float_note(free_float)
            signal_block["confidence"] = signals.damp_confidence(
                float(signal_block["confidence"]), free_float
            )
        adverse = signals.adverse_events(_risk_events_for(request.symbol))
        if adverse:
            liquidity = signal_block.setdefault("liquidity", {})
            liquidity["events"] = list(adverse)
            liquidity["event_note"] = signals.event_risk_note(adverse)
            signal_block["confidence"] = signals.damp_for_events(
                float(signal_block["confidence"]), adverse
            )
    return payload


@dataclass(frozen=True, slots=True)
class SignalRequest:
    symbol: str
    interval: str
    bars: int


def signal_query(request: SignalRequest) -> dict[str, Any]:
    bars = ohlcv.fetch_history(
        ohlcv.OhlcvRequest(
            symbol=request.symbol, interval=request.interval, bars=request.bars
        )
    )
    closes = [bar.close for bar in bars]
    report = signals.analyze_signal(closes)
    free_float = _free_float_for(request.symbol)
    if free_float is not None:
        report = signals.apply_liquidity(report, free_float)
    report = signals.apply_event_risk(report, _risk_events_for(request.symbol))
    payload = signals.signal_payload(report)
    payload.update(
        {"symbol": request.symbol, "interval": request.interval, "bars": len(bars)}
    )
    return payload


@app.command("shot")
def shot(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    out: Annotated[Path, typer.Option("--out")],
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    studies: Annotated[str | None, typer.Option("--studies")] = None,
    theme: Annotated[str, typer.Option("--theme")] = "dark",
    width: Annotated[int, typer.Option("--width")] = 1600,
    height: Annotated[int, typer.Option("--height")] = 900,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = chart.ChartRequest(
        symbol=symbol,
        interval=interval,
        out=out,
        width=width,
        height=height,
        theme=theme,
        studies=tuple(part.strip() for part in studies.split(",") if part.strip())
        if studies
        else (),
    )
    run_command(
        "chart.shot",
        json_mode=json_mode,
        handler=lambda: shot_query(request),
    )


@app.command("analyze")
def analyze_command(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    out: Annotated[Path, typer.Option("--out")],
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    indicator: Annotated[
        list[str] | None,
        typer.Option(
            "--indicator",
            help="Indicator spec NAME[:p1[:p2[:p3]]]; repeatable. "
            "e.g. --indicator wma:200 --indicator rsi:14",
        ),
    ] = None,
    bars: Annotated[int, typer.Option("--bars")] = 500,
    style: Annotated[str, typer.Option("--style", help="candle or line")] = "candle",
    volume: Annotated[bool, typer.Option("--volume/--no-volume")] = True,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            help="Detect the market regime and auto-select fitting indicators; "
            "attach a buy/sell/hold signal to the output.",
        ),
    ] = False,
    theme: Annotated[str, typer.Option("--theme")] = "dark",
    width: Annotated[int, typer.Option("--width")] = 1600,
    height: Annotated[int, typer.Option("--height")] = 900,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = analyze.AnalyzeRequest(
        symbol=symbol,
        interval=interval,
        out=out,
        bars=bars,
        indicators=tuple(indicator) if indicator else (),
        width=width,
        height=height,
        theme=theme,
        style=style,
        volume=volume,
        auto=auto,
    )

    def handler() -> dict[str, Any]:
        retries, backoff_seconds = resolve_retry_policy(ctx)
        return run_with_retries(
            lambda: analyze_query(request),
            retries=retries,
            backoff_seconds=backoff_seconds,
        )

    run_command("chart.analyze", json_mode=json_mode, handler=handler)


def _write_signal_human(payload: dict[str, Any]) -> None:
    """Flatten the signal payload into readable tables (summary + votes).

    The generic key/value renderer would dump the nested ``regime``/``liquidity``
    dicts as raw ``str(dict)`` lines, so signal gets its own layout here while
    ``--json`` keeps the full structured envelope.
    """
    regime = payload.get("regime") or {}
    liquidity = payload.get("liquidity") or {}
    free_float = liquidity.get("free_float")
    summary: dict[str, Any] = {
        "symbol": payload.get("symbol"),
        "interval": payload.get("interval"),
        "bars": payload.get("bars"),
        "signal": str(payload.get("signal", "")).upper(),
        "confidence": payload.get("confidence"),
        "score": payload.get("score"),
        "regime": regime.get("kind"),
        "regime_strength": regime.get("strength"),
        "free_float_%": "—" if free_float is None else free_float,
        "liquidity_note": liquidity.get("note") or "—",
        "event_note": liquidity.get("event_note") or "—",
    }
    sys.stdout.write(render_table(summary))
    votes = payload.get("votes") or []
    if votes:
        rows = [
            {
                "indicator": v.get("indicator"),
                "vote": {1: "buy", -1: "sell", 0: "hold"}.get(
                    v.get("vote"), v.get("vote")
                ),
                "strength": v.get("strength"),
                "reason": v.get("reason"),
            }
            for v in votes
        ]
        sys.stdout.write(render_table(rows))


@app.command("signal")
def signal_command(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    bars: Annotated[int, typer.Option("--bars")] = 500,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = SignalRequest(symbol=symbol, interval=interval, bars=bars)

    def handler() -> dict[str, Any]:
        retries, backoff_seconds = resolve_retry_policy(ctx)
        return run_with_retries(
            lambda: signal_query(request),
            retries=retries,
            backoff_seconds=backoff_seconds,
        )

    try:
        result = handler()
    except TvcliError as error:
        emit(envelope_from_error("chart.signal", error), json_mode=json_mode)
        raise typer.Exit(code=error.exit_code) from error

    if json_mode:
        emit(build_envelope(command="chart.signal", data=result), json_mode=True)
        return
    _write_signal_human(result)
