from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
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


def test_float_verify_command_reports_coverage(tmp_path: Path) -> None:
    from unittest import mock

    from tvcli.layers import freefloat as ff
    from tvcli.layers.freefloat_archive import ArchiveStore

    store = ArchiveStore(tmp_path / "archive.sqlite3")
    # Store a report for 2026-06-10 (Wednesday)
    store.sync_records(
        (
            ff.FloatRecord(
                code="THYAO",
                isin="TR000000",
                name="THYAO",
                float_shares=350.0,
                capital=1000.0,
                ratio=35.0,
                date="10.06.2026",
            ),
        )
    )
    # Mark 2026-06-11 (Thursday) as known-empty
    with store._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT OR IGNORE INTO freefloat_missing"
            " (report_date, checked_at) VALUES (?,?)",
            ("2026-06-11", datetime.now(UTC).isoformat()),
        )

    with mock.patch(
        "tvcli.commands.data.freefloat_archive.ArchiveStore",
        return_value=store,
    ):
        result = CliRunner().invoke(
            app,
            [
                "data",
                "float-verify",
                "--since",
                "2026-06-08",
                "--until",
                "2026-06-12",
                "--json",
            ],
        )

    assert result.exit_code == 0
    data = json.loads(result.output)["data"]
    assert data["business_days"] == 5  # Mon–Fri
    assert data["stored"] == 1  # 2026-06-10
    assert data["known_empty"] == 1  # 2026-06-11
    assert data["gap_count"] == 3  # Mon(08), Tue(09), Fri(12)
    assert data["coverage_pct"] == pytest.approx(40.0)


def test_float_verify_missing_until(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["data", "float-verify", "--since", "2026-06-01", "--json"]
    )
    assert result.exit_code != 0


def test_float_verify_since_after_until(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "data",
            "float-verify",
            "--since",
            "2026-06-10",
            "--until",
            "2026-06-01",
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_data_float_default_command_still_works(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    monkeypatch.setattr(
        "tvcli.commands.data.float_query",
        lambda _report_date: (),
    )

    result = CliRunner().invoke(app, ["data", "float", "--all", "--json"])

    assert result.exit_code == 0
    assert '"command": "data.float"' in result.output
