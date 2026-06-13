from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app


def test_data_float_report_cli(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    def fake_report(self, symbol: str, *, limit: int = 20) -> dict[str, object]:
        assert symbol == "THYAO"
        assert limit == 5
        return {"symbol": "THYAO", "latest": {"ratio": 40.0}}

    monkeypatch.setattr(
        "tvcli.commands.data.freefloat_archive.ArchiveStore.build_symbol_report",
        fake_report,
    )

    result = CliRunner().invoke(
        app, ["data", "float-report", "THYAO", "--limit", "5", "--json"]
    )

    assert result.exit_code == 0
    assert '"command": "data.float.report"' in result.output
    assert '"symbol": "THYAO"' in result.output


def test_data_float_sync_cli(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    monkeypatch.setattr(
        "tvcli.commands.data.freefloat_archive.sync_archive",
        lambda **_kwargs: {"synced_reports": 2, "reports": []},
    )

    result = CliRunner().invoke(
        app,
        [
            "data",
            "float-sync",
            "--since",
            "2026-06-01",
            "--until",
            "2026-06-05",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"command": "data.float.sync"' in result.output
    assert '"synced_reports": 2' in result.output


def test_data_float_default_command_still_works(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    monkeypatch.setattr(
        "tvcli.commands.data.float_query",
        lambda _report_date: (),
    )

    result = CliRunner().invoke(app, ["data", "float", "--all", "--json"])

    assert result.exit_code == 0
    assert '"command": "data.float"' in result.output
