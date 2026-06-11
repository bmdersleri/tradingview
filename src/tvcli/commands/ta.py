"""TA command group."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ..layers import ta
from ._helpers import resolve_json_mode, run_command

app = typer.Typer(add_completion=False, help="TA commands")


def analysis_query(request: ta.TaRequest) -> Any:
    return ta.run_ta_get(request)


def multiple_analysis_query(symbols: tuple[str, ...], interval: str) -> tuple[Any, ...]:
    return ta.run_ta_multi(symbols, interval)


@app.command("get")
def get(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    screener_name: Annotated[str, typer.Option("--screener")] = "auto",
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "ta.get",
        json_mode=json_mode,
        handler=lambda: ta.build_snapshot_payload(
            request := ta.TaRequest(
                symbol=symbol,
                interval=interval,
                screener=ta.derive_screener(symbol, screener_name),
            ),
            analysis_query(request),
        ),
    )


@app.command("multi")
def multi(
    ctx: typer.Context,
    symbols: Annotated[list[str], typer.Argument()],
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "ta.multi",
        json_mode=json_mode,
        handler=lambda: ta.build_multi_payload(
            tuple(symbols),
            interval,
            tuple(
                ta.build_snapshot_payload(
                    ta.TaRequest(
                        symbol=symbol,
                        interval=interval,
                        screener=ta.derive_screener(symbol),
                    ),
                    analysis,
                )
                for symbol, analysis in zip(
                    tuple(symbols),
                    multiple_analysis_query(tuple(symbols), interval),
                    strict=True,
                )
            ),
        ),
    )


@app.command("matrix")
def matrix(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    intervals: Annotated[str, typer.Option("--intervals")] = "1h,4h,1d",
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    interval_list = tuple(part.strip() for part in intervals.split(",") if part.strip())
    run_command(
        "ta.matrix",
        json_mode=json_mode,
        handler=lambda: ta.build_matrix_payload(
            symbol,
            interval_list,
            tuple(
                ta.build_snapshot_payload(
                    ta.TaRequest(
                        symbol=symbol,
                        interval=interval,
                        screener=ta.derive_screener(symbol),
                    ),
                    analysis_query(
                        ta.TaRequest(
                            symbol=symbol,
                            interval=interval,
                            screener=ta.derive_screener(symbol),
                        )
                    ),
                )
                for interval in interval_list
            ),
        ),
    )
