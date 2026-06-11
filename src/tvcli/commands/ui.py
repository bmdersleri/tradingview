"""UI command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ..layers import ui
from ._helpers import resolve_json_mode, run_command

app = typer.Typer(add_completion=False, help="UI commands")
alert_app = typer.Typer(add_completion=False, help="Alert commands")
watchlist_app = typer.Typer(add_completion=False, help="Watchlist commands")
pine_app = typer.Typer(add_completion=False, help="Pine commands")

app.add_typer(alert_app, name="alert")
app.add_typer(watchlist_app, name="watchlist")
app.add_typer(pine_app, name="pine")


def alert_create_query(request: ui.AlertCreateRequest) -> dict[str, Any]:
    return ui.run_alert_create(request)


def alert_list_query() -> dict[str, Any]:
    return ui.run_alert_list()


def alert_delete_query(request: ui.AlertDeleteRequest) -> dict[str, Any]:
    return ui.run_alert_delete(request)


def watchlist_add_query(request: ui.WatchlistAddRequest) -> dict[str, Any]:
    return ui.run_watchlist_add(request)


def watchlist_export_query(list_name: str) -> dict[str, Any]:
    return ui.run_watchlist_export(list_name)


def pine_push_query(request: ui.PinePushRequest) -> dict[str, Any]:
    return ui.run_pine_push(request)


@alert_app.command("create")
def alert_create(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    condition: Annotated[str, typer.Option("--condition")] = "Crossing",
    value: Annotated[float, typer.Option("--value")] = 0.0,
    message: Annotated[str | None, typer.Option("--message")] = None,
    webhook: Annotated[str | None, typer.Option("--webhook")] = None,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = ui.AlertCreateRequest(
        symbol=symbol,
        condition=condition,
        value=value,
        message=message,
        webhook=webhook,
    )
    run_command(
        "ui.alert.create",
        json_mode=json_mode,
        handler=lambda: alert_create_query(request),
    )


@alert_app.command("list")
def alert_list(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command("ui.alert.list", json_mode=json_mode, handler=alert_list_query)


@alert_app.command("delete")
def alert_delete(
    ctx: typer.Context,
    alert_id: Annotated[str | None, typer.Option("--id")] = None,
    delete_all: Annotated[bool, typer.Option("--all")] = False,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = ui.AlertDeleteRequest(alert_id=alert_id, delete_all=delete_all)
    run_command(
        "ui.alert.delete",
        json_mode=json_mode,
        handler=lambda: alert_delete_query(request),
    )


@watchlist_app.command("add")
def watchlist_add(
    ctx: typer.Context,
    symbols: Annotated[list[str], typer.Argument()],
    list_name: Annotated[str, typer.Option("--list")],
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = ui.WatchlistAddRequest(symbols=tuple(symbols), list_name=list_name)
    run_command(
        "ui.watchlist.add",
        json_mode=json_mode,
        handler=lambda: watchlist_add_query(request),
    )


@watchlist_app.command("export")
def watchlist_export(
    ctx: typer.Context,
    list_name: Annotated[str, typer.Option("--list")],
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "ui.watchlist.export",
        json_mode=json_mode,
        handler=lambda: watchlist_export_query(list_name),
    )


@pine_app.command("push")
def pine_push(
    ctx: typer.Context,
    file_path: Annotated[Path, typer.Option("--file")],
    name: Annotated[str, typer.Option("--name")],
    save_only: Annotated[bool, typer.Option("--save-only/--add-to-chart")] = True,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = ui.PinePushRequest(file_path=file_path, name=name, save_only=save_only)
    run_command(
        "ui.pine.push",
        json_mode=json_mode,
        handler=lambda: pine_push_query(request),
    )
