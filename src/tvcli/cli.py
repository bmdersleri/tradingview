"""Command-line interface for tvcli."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .cache import SQLiteTTLCache
from .commands import auth, chart, data, dev, mcp, ohlcv, serve, ta, ui
from .config import default_cache_path, default_config_path
from .doctor import run_doctor
from .output import build_envelope, emit

app = typer.Typer(add_completion=False, help="TradingView CLI toolkit")

app.add_typer(data.app, name="data")
app.add_typer(ta.app, name="ta")
app.add_typer(ohlcv.app, name="ohlcv")
app.add_typer(chart.app, name="chart")
app.add_typer(ui.app, name="ui")
app.add_typer(auth.app, name="auth")
app.add_typer(serve.app, name="serve")
app.add_typer(mcp.app, name="mcp")
app.add_typer(dev.app, name="dev")

cache_app = typer.Typer(add_completion=False, help="Cache utilities")
app.add_typer(cache_app, name="cache")

db_app = typer.Typer(add_completion=False, help="Database utilities (Backup / Restore)")
app.add_typer(db_app, name="db")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    retries: Annotated[int, typer.Option("--retries")] = 0,
    backoff_seconds: Annotated[float, typer.Option("--backoff")] = 1.0,
    config: Annotated[
        Path | None, typer.Option("--config", exists=False, dir_okay=False)
    ] = None,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["json_mode"] = json_mode
    ctx.obj["no_cache"] = no_cache
    ctx.obj["quiet"] = quiet
    ctx.obj["retries"] = retries
    ctx.obj["backoff_seconds"] = backoff_seconds
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


@app.command()
def doctor(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = json_mode or bool(ctx.obj.get("json_mode", False))
    report = run_doctor()
    emit(
        build_envelope(
            command="doctor",
            ok=bool(report["all_ok"]),
            data=report,
        ),
        json_mode=json_mode,
    )
    if not report["all_ok"]:
        raise typer.Exit(code=1)


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


@db_app.command("backup")
def db_backup(
    ctx: typer.Context,
    target: Annotated[
        Path | None,
        typer.Option(
            "--target",
            "-t",
            help=(
                "Target path for the backup file. "
                "Defaults to auto-generated path in XDG data directory."
            ),
        ),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Safely backup the free-float database."""
    json_mode = json_mode or bool(ctx.obj.get("json_mode", False))
    from datetime import datetime

    from .config import default_archive_path, default_data_dir
    from .layers.freefloat_archive import ArchiveStore

    archive_path = default_archive_path()
    if not archive_path.exists():
        emit(
            build_envelope(
                command="db.backup",
                ok=False,
                error={
                    "code": 1,
                    "message": (
                        "Database file does not exist. Run sync or dev seed-db first."
                    ),
                    "hint": "Run sync or dev seed-db first",
                    "retryable": False,
                },
            ),
            json_mode=json_mode,
        )
        raise typer.Exit(code=1)

    if target is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = default_data_dir() / "backups" / f"backup_{timestamp}.sqlite3"

    try:
        store = ArchiveStore(archive_path)
        store.backup(target)
        emit(
            build_envelope(
                command="db.backup",
                data={
                    "backup_path": str(target.resolve()),
                    "source_path": str(archive_path.resolve()),
                    "size_bytes": target.stat().st_size,
                },
            ),
            json_mode=json_mode,
        )
    except Exception as e:
        emit(
            build_envelope(
                command="db.backup",
                ok=False,
                error={
                    "code": 1,
                    "message": str(e),
                    "hint": "Check target directory permissions",
                    "retryable": False,
                },
            ),
            json_mode=json_mode,
        )
        raise typer.Exit(code=1) from e


@db_app.command("restore")
def db_restore(
    ctx: typer.Context,
    source: Annotated[
        Path,
        typer.Argument(
            help="Path to the backup SQLite file to restore from.",
            exists=True,
            file_okay=True,
            dir_okay=False,
        ),
    ],
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Safely restore the free-float database from a backup."""
    json_mode = json_mode or bool(ctx.obj.get("json_mode", False))
    from .config import default_archive_path
    from .layers.freefloat_archive import ArchiveStore

    archive_path = default_archive_path()

    try:
        store = ArchiveStore(archive_path)
        store.restore(source)
        emit(
            build_envelope(
                command="db.restore",
                data={
                    "restored_path": str(archive_path.resolve()),
                    "source_path": str(source.resolve()),
                    "size_bytes": archive_path.stat().st_size,
                },
            ),
            json_mode=json_mode,
        )
    except Exception as e:
        emit(
            build_envelope(
                command="db.restore",
                ok=False,
                error={
                    "code": 1,
                    "message": str(e),
                    "hint": "Ensure the backup file path exists and is readable",
                    "retryable": False,
                },
            ),
            json_mode=json_mode,
        )
        raise typer.Exit(code=1) from e
