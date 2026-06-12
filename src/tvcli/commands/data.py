"""Data command group."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

import typer

from ..errors import NotFoundError, UsageError
from ..layers import freefloat, screener
from ._helpers import resolve_json_mode, run_command

app = typer.Typer(add_completion=False, help="Data commands")


def screen_query(
    request: screener.ScreenRequest,
) -> screener.ScreenResult:
    return screener.run_screen_query(request)


def float_query(report_date: date | None) -> tuple[freefloat.FloatRecord, ...]:
    return freefloat.fetch_report(report_date)


def _parse_report_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as exc:
        raise UsageError(
            f"Invalid date '{value}'.",
            hint="Use DD/MM/YYYY, e.g. --date 11/06/2026.",
        ) from exc


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


@app.command("float")
def float_command(
    ctx: typer.Context,
    symbol: Annotated[str | None, typer.Argument()] = None,
    all_companies: Annotated[bool, typer.Option("--all")] = False,
    date_str: Annotated[str | None, typer.Option("--date")] = None,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """BIST free-float (fiili dolaşım) ratios from VAP / MKK."""
    json_mode = resolve_json_mode(ctx, json_mode)
    if not symbol and not all_companies:
        raise UsageError(
            "Provide a SYMBOL or pass --all.",
            hint="e.g. `tvcli data float THYAO` or `tvcli data float --all`.",
        )
    report_date = _parse_report_date(date_str)

    def handler() -> dict[str, Any]:
        records = float_query(report_date)
        if all_companies and not symbol:
            return freefloat.build_float_payload(records, report_date=report_date)
        code = freefloat.normalize_code(symbol or "")
        match = next((r for r in records if r.code == code), None)
        if match is None:
            raise NotFoundError(
                f"No free-float record for '{code}'.",
                hint="Check the BIST code, or try another --date.",
            )
        return freefloat.build_float_payload(records, single=match)

    run_command("data.float", json_mode=json_mode, handler=handler)
