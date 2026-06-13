from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app
from tvcli.config import default_archive_path
from tvcli.layers import freefloat as ff
from tvcli.layers.freefloat_archive import ArchiveStore


def test_db_backup_restore(monkeypatch, tmp_path: Path) -> None:
    # Set custom XDG data home so default_archive_path resolves to a temp dir
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir.parent))

    runner = CliRunner()

    # 1. Try backing up when database doesn't exist
    result_fail = runner.invoke(app, ["db", "backup", "--json"])
    assert result_fail.exit_code == 1
    assert "Database file does not exist" in result_fail.output

    # 2. Seed a database
    archive_path = default_archive_path()
    store = ArchiveStore(archive_path)
    records = [
        ff.FloatRecord(
            code="THYAO",
            isin="TRTHYAO0001",
            name="THYAO",
            float_shares=100.0,
            capital=200.0,
            ratio=50.0,
            date="13.06.2026",
        )
    ]
    store.sync_records(tuple(records))

    # Verify seeded database has 1 symbol
    stats = store.archive_stats()
    assert stats["symbols"] == 1

    # 3. Perform backup
    backup_path = tmp_path / "my_backup.sqlite3"
    result_backup = runner.invoke(
        app, ["db", "backup", "-t", str(backup_path), "--json"]
    )
    assert result_backup.exit_code == 0
    assert '"command": "db.backup"' in result_backup.output
    assert backup_path.exists()

    # 4. Modify original database by deleting file (simulating data loss)
    archive_path.unlink()
    assert not archive_path.exists()

    # 5. Restore from backup
    result_restore = runner.invoke(app, ["db", "restore", str(backup_path), "--json"])
    assert result_restore.exit_code == 0
    assert '"command": "db.restore"' in result_restore.output

    # 6. Verify restored database has the original data back!
    assert store.archive_stats()["symbols"] == 1
