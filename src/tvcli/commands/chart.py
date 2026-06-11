"""Chart command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ..layers import chart
from ._helpers import resolve_json_mode, run_command

app = typer.Typer(add_completion=False, help="Chart commands")


def shot_query(request: chart.ChartRequest) -> dict[str, Any]:
    return chart.shot_chart(request)


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
