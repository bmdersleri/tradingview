"""Data command group."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from ..errors import NotFoundError, UsageError
from ..layers import float_dashboard as _dashboard
from ..layers import freefloat, freefloat_archive, screener
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


def _parse_iso_date(value: str | None, *, option_name: str) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise UsageError(
            f"Invalid {option_name} '{value}'.",
            hint=f"Use ISO format YYYY-MM-DD for {option_name}.",
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


@app.command("float-sync")
def float_sync(
    ctx: typer.Context,
    latest: Annotated[bool, typer.Option("--latest")] = False,
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    max_days: Annotated[int | None, typer.Option("--max-days")] = None,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    rate_seconds: Annotated[
        float,
        typer.Option(
            "--rate-seconds",
            help="Delay between per-day requests during a backfill (default 20).",
        ),
    ] = 20.0,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    since_date = _parse_iso_date(since, option_name="--since")
    until_date = _parse_iso_date(until, option_name="--until")
    if latest and (since_date or until_date or max_days is not None):
        raise UsageError(
            "Use either --latest or a date-range sync.",
            hint="For backfill use --since/--until/--max-days without --latest.",
        )
    if since_date is not None and until_date is not None and since_date > until_date:
        raise UsageError(
            "--since cannot be later than --until.",
            hint="Swap the range or adjust the dates.",
        )
    run_command(
        "data.float.sync",
        json_mode=json_mode,
        handler=lambda: freefloat_archive.sync_archive(
            latest=latest,
            since=since_date,
            until=until_date,
            max_days=max_days,
            resume=resume,
            rate_seconds=rate_seconds,
        ),
    )


@app.command("float-report")
def float_report(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    limit: Annotated[int, typer.Option("--limit")] = 20,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.float.report",
        json_mode=json_mode,
        handler=lambda: freefloat_archive.ArchiveStore().build_symbol_report(
            symbol, limit=limit
        ),
    )


@app.command("float-history")
def float_history(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    limit: Annotated[int, typer.Option("--limit")] = 100,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.float.history",
        json_mode=json_mode,
        handler=lambda: {
            "symbol": freefloat.normalize_code(symbol),
            "history": freefloat_archive.ArchiveStore().symbol_history(
                symbol, limit=limit
            ),
        },
    )


@app.command("float-events")
def float_events(
    ctx: typer.Context,
    symbol: Annotated[str | None, typer.Argument()] = None,
    limit: Annotated[int, typer.Option("--limit")] = 100,
    severity: Annotated[str | None, typer.Option("--severity")] = None,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.float.events",
        json_mode=json_mode,
        handler=lambda: {
            "symbol": None if symbol is None else freefloat.normalize_code(symbol),
            "events": freefloat_archive.ArchiveStore().symbol_events(
                symbol, limit=limit, severity=severity
            ),
        },
    )


@app.command("float-stats")
def float_stats(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "data.float.stats",
        json_mode=json_mode,
        handler=lambda: freefloat_archive.ArchiveStore().archive_stats(),
    )


@app.command("float-verify")
def float_verify(
    ctx: typer.Context,
    since: Annotated[str, typer.Option("--since", help="Start date YYYY-MM-DD")] = "",
    until: Annotated[str, typer.Option("--until", help="End date YYYY-MM-DD")] = "",
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Report archive coverage and gap dates for a date range.

    Identifies business days in [since, until] that have neither an archived
    report nor a known-empty stamp (i.e. were never attempted). All reads are
    local — no network traffic.
    """
    json_mode = resolve_json_mode(ctx, json_mode)

    def handler() -> dict[str, Any]:
        if not since or not until:
            raise UsageError("--since and --until are both required (YYYY-MM-DD).")
        try:
            d_since = date.fromisoformat(since)
            d_until = date.fromisoformat(until)
        except ValueError as exc:
            raise UsageError(f"Invalid date format: {exc}") from exc
        if d_since > d_until:
            raise UsageError("--since must be <= --until.")

        store = freefloat_archive.ArchiveStore()
        # Count all business days in range.
        total_biz = sum(
            1
            for ord_ in range(d_since.toordinal(), d_until.toordinal() + 1)
            if date.fromordinal(ord_).weekday() < 5
        )
        # Count stored reports in range.
        with store._connect() as conn:  # noqa: SLF001
            stored_row = conn.execute(
                "SELECT COUNT(DISTINCT report_date) AS n FROM freefloat_reports "
                "WHERE report_date BETWEEN ? AND ?",
                (d_since.isoformat(), d_until.isoformat()),
            ).fetchone()
            empty_row = conn.execute(
                "SELECT COUNT(*) AS n FROM freefloat_missing "
                "WHERE report_date BETWEEN ? AND ?",
                (d_since.isoformat(), d_until.isoformat()),
            ).fetchone()
        stored = int(stored_row["n"]) if stored_row else 0
        known_empty = int(empty_row["n"]) if empty_row else 0

        gaps = store.missing_business_days(d_since, d_until)
        coverage_pct = (
            round(100.0 * (stored + known_empty) / total_biz, 2) if total_biz else None
        )

        return {
            "since": since,
            "until": until,
            "business_days": total_biz,
            "stored": stored,
            "known_empty": known_empty,
            "gaps": [str(g) for g in gaps],
            "gap_count": len(gaps),
            "coverage_pct": coverage_pct,
        }

    run_command("data.float.verify", json_mode=json_mode, handler=handler)


@app.command("float-dashboard")
def float_dashboard_cmd(
    ctx: typer.Context,
    symbol: Annotated[str | None, typer.Argument()] = None,
    out: Annotated[Path, typer.Option("--out", help="Output PNG path")] = Path(
        "float_dashboard.png"
    ),
    market: Annotated[bool, typer.Option("--market")] = False,
    date_str: Annotated[str | None, typer.Option("--date")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 120,
    top: Annotated[int, typer.Option("--top")] = 15,
    theme: Annotated[str, typer.Option("--theme")] = "dark",
    width: Annotated[int, typer.Option("--width")] = 1600,
    height: Annotated[int, typer.Option("--height")] = 1000,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Render free-float dashboard PNG (deep-dive or market overview).

    Pass a SYMBOL for a single-symbol 3-panel deep-dive, or --market for a
    market-wide overview. All reads are local — no network traffic.
    """
    json_mode = resolve_json_mode(ctx, json_mode)
    report_date = _parse_iso_date(date_str, option_name="--date")

    def handler() -> dict[str, Any]:
        req = _dashboard.DashboardRequest(
            out=out,
            symbol=symbol,
            market=market,
            report_date=report_date,
            limit=limit,
            top=top,
            theme=theme,
            width=width,
            height=height,
        )
        return _dashboard.run_dashboard(req)

    run_command("data.float.dashboard", json_mode=json_mode, handler=handler)
