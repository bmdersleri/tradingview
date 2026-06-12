"""Chart command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ..layers import analyze, chart
from ._helpers import (
    resolve_json_mode,
    resolve_retry_policy,
    run_command,
    run_with_retries,
)

app = typer.Typer(add_completion=False, help="Chart commands")


def shot_query(request: chart.ChartRequest) -> dict[str, Any]:
    return chart.shot_chart(request)


def analyze_query(request: analyze.AnalyzeRequest) -> dict[str, Any]:
    return analyze.run_analysis(request)


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
    )

    def handler() -> dict[str, Any]:
        retries, backoff_seconds = resolve_retry_policy(ctx)
        return run_with_retries(
            lambda: analyze_query(request),
            retries=retries,
            backoff_seconds=backoff_seconds,
        )

    run_command("chart.analyze", json_mode=json_mode, handler=handler)
