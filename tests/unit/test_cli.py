from pathlib import Path

from typer.testing import CliRunner

from tvcli.cli import app


def test_version_command_emits_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    runner = CliRunner()
    result = runner.invoke(app, ["--json", "version"])

    assert result.exit_code == 0
    assert '"command": "version"' in result.output
    assert '"version": "0.5.0"' in result.output


def test_cache_commands_use_default_state_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    runner = CliRunner()
    stats = runner.invoke(app, ["cache", "stats", "--json"])
    clear = runner.invoke(app, ["cache", "clear", "--json"])

    assert stats.exit_code == 0
    assert '"command": "cache.stats"' in stats.output
    assert clear.exit_code == 0
    assert '"cleared": true' in clear.output
