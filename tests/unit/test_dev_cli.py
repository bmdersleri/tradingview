from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.layers.freefloat_archive import ArchiveStore


def test_dev_seed_db(monkeypatch, tmp_path: Path) -> None:
    # 1. Mock the data and state directories to use tmp_path
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    # 2. Run the seed-db CLI command
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["dev", "seed-db", "--symbols", "5", "--days", "3", "--json"],
    )

    assert result.exit_code == 0
    assert "dev.seed-db" in result.output
    assert '"reports_synced": 3' in result.output
    assert '"symbols_count": 5' in result.output

    # 3. Verify that the database exists and has records
    store = ArchiveStore()
    stats = store.archive_stats()
    assert stats["reports"] == 3
    assert stats["symbols"] == 5
