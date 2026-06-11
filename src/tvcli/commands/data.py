"""Data command group."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ..layers import screener
from ._helpers import resolve_json_mode, run_command

app = typer.Typer(add_completion=False, help="Data commands")


def screen_query(
    request: screener.ScreenRequest,
) -> screener.ScreenResult:
    return screener.run_screen_query(request)


def fields_query(
    market: str,
    search: str | None,
) -> tuple[screener.FieldInfo, ...]:
    return screener.run_fields_query(market, search)


def search_query(
    query: str,
    market: str | None,
) -> tuple[dict[str, Any], ...]:
    return screener.run_search_query(query, market)


def quote_query(symbols: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    return screener.run_quote_query(symbols)


@app.command("screen")
def screen(
    ctx: typer.Context,
    market: Annotated[str, typer.Option("--market")],
    select: Annotated[str, typer.Option("--select")],
    where: Annotated[str | None, typer.Option("--where")] = None,
    order_by: Annotated[str | None, typer.Option("--order-by")] = None,
    desc: Annotated[bool, typer.Option("--desc")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 50,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.screen",
        json_mode=json_mode,
        handler=lambda: screener.build_screen_payload(
            request := screener.ScreenRequest(
                market=market,
                select=screener.split_select(select),
                where=screener.parse_where(where),
                order_by=order_by,
                descending=desc,
                limit=limit,
            ),
            screen_query(request),
        ),
    )


@app.command("fields")
def fields(
    ctx: typer.Context,
    market: Annotated[str, typer.Option("--market")],
    search: Annotated[str | None, typer.Option("--search")] = None,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.fields",
        json_mode=json_mode,
        handler=lambda: screener.build_fields_payload(
            market,
            fields_query(market, search),
        ),
    )


@app.command("search")
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument()],
    market: Annotated[str | None, typer.Option("--market")] = None,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.search",
        json_mode=json_mode,
        handler=lambda: screener.build_search_payload(
            query,
            market,
            search_query(query, market),
        ),
    )


@app.command("quote")
def quote(
    ctx: typer.Context,
    symbols: Annotated[list[str], typer.Argument()],
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.quote",
        json_mode=json_mode,
        handler=lambda: screener.build_quote_payload(
            tuple(symbols),
            quote_query(tuple(symbols)),
        ),
    )
