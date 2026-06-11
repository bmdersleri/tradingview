from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.commands import ui


def test_ui_commands_route_to_layer(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        ui,
        "alert_create_query",
        lambda request: {"symbol": request.symbol, "created": True},
    )
    monkeypatch.setattr(ui, "alert_list_query", lambda: {"returned": 0, "rows": []})
    monkeypatch.setattr(ui, "alert_delete_query", lambda request: {"deleted": "all"})
    monkeypatch.setattr(
        ui,
        "watchlist_add_query",
        lambda request: {"list": request.list_name, "returned": len(request.symbols)},
    )
    monkeypatch.setattr(
        ui,
        "watchlist_export_query",
        lambda list_name: {"list": list_name, "returned": 0, "symbols": []},
    )
    monkeypatch.setattr(
        ui,
        "pine_push_query",
        lambda request: {
            "name": request.name,
            "bytes": request.file_path.stat().st_size,
        },
    )

    pine_file = tmp_path / "script.pine"
    pine_file.write_text("indicator('x')", encoding="utf-8")

    runner = CliRunner()
    alert_create = runner.invoke(
        app,
        [
            "ui",
            "alert",
            "create",
            "BIST:THYAO",
            "--condition",
            "Crossing",
            "--value",
            "320",
            "--json",
        ],
    )
    alert_list = runner.invoke(app, ["ui", "alert", "list", "--json"])
    alert_delete = runner.invoke(app, ["ui", "alert", "delete", "--all", "--json"])
    watchlist_add = runner.invoke(
        app,
        [
            "ui",
            "watchlist",
            "add",
            "BIST:THYAO",
            "NASDAQ:NVDA",
            "--list",
            "main",
            "--json",
        ],
    )
    watchlist_export = runner.invoke(
        app,
        ["ui", "watchlist", "export", "--list", "main", "--json"],
    )
    pine_push = runner.invoke(
        app,
        [
            "ui",
            "pine",
            "push",
            "--file",
            str(pine_file),
            "--name",
            "demo",
            "--add-to-chart",
            "--json",
        ],
    )

    assert alert_create.exit_code == 0
    assert alert_list.exit_code == 0
    assert alert_delete.exit_code == 0
    assert watchlist_add.exit_code == 0
    assert watchlist_export.exit_code == 0
    assert pine_push.exit_code == 0
    assert '"command": "ui.alert.create"' in alert_create.output
    assert '"command": "ui.watchlist.add"' in watchlist_add.output
    assert '"command": "ui.pine.push"' in pine_push.output
