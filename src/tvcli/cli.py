"""Command-line interface for tvcli."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .cache import SQLiteTTLCache
from .commands import auth, chart, data, ohlcv, serve, ta, ui
from .config import default_cache_path, default_config_path
from .output import build_envelope, emit

app = typer.Typer(add_completion=False, help="TradingView CLI toolkit")

app.add_typer(data.app, name="data")
app.add_typer(ta.app, name="ta")
app.add_typer(ohlcv.app, name="ohlcv")
app.add_typer(chart.app, name="chart")
app.add_typer(ui.app, name="ui")
app.add_typer(auth.app, name="auth")
app.add_typer(serve.app, name="serve")

cache_app = typer.Typer(add_completion=False, help="Cache utilities")
app.add_typer(cache_app, name="cache")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    config: Annotated[
        Path | None, typer.Option("--config", exists=False, dir_okay=False)
    ] = None,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["json_mode"] = json_mode
    ctx.obj["no_cache"] = no_cache
    ctx.obj["quiet"] = quiet
    ctx.obj["config_path"] = config or default_config_path()
    ctx.obj["cache_path"] = default_cache_path()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def version(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = json_mode or bool(ctx.obj.get("json_mode", False))
    payload = build_envelope(command="version", data={"version": __version__})
    emit(payload, json_mode=json_mode)


@cache_app.command("stats")
def cache_stats(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = json_mode or bool(ctx.obj.get("json_mode", False))
    cache = SQLiteTTLCache(default_cache_path())
    emit(
        build_envelope(command="cache.stats", data=cache.stats()),
        json_mode=json_mode,
    )


@cache_app.command("clear")
def cache_clear(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = json_mode or bool(ctx.obj.get("json_mode", False))
    cache = SQLiteTTLCache(default_cache_path())
    cache.clear()
    emit(
        build_envelope(command="cache.clear", data={"cleared": True}),
        json_mode=json_mode,
    )
