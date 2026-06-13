from __future__ import annotations

from pathlib import Path

from tvcli.config import (
    default_archive_path,
    default_cache_path,
    default_config_path,
    default_session_path,
    default_storage_state_path,
    ensure_config_file,
    env_key,
    load_config,
    resolve_setting,
)


def test_default_paths_respect_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert default_config_path() == tmp_path / "config" / "tvcli" / "config.toml"
    assert default_cache_path() == tmp_path / "state" / "tvcli" / "cache.sqlite3"
    assert default_archive_path() == tmp_path / "data" / "tvcli" / "archive.sqlite3"
    assert default_session_path() == tmp_path / "config" / "tvcli" / "session.json"
    assert default_storage_state_path() == (
        tmp_path / "config" / "tvcli" / "storage_state.json"
    )


def test_ensure_and_load_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[auth]\nusername = "demo"\n', encoding="utf-8")

    loaded = load_config(config_path)

    assert loaded["auth"]["username"] == "demo"


def test_ensure_config_file_materializes(tmp_path: Path) -> None:
    config_path = ensure_config_file(tmp_path / "nested" / "config.toml")

    assert config_path.exists()
    assert config_path.read_text(encoding="utf-8").startswith("# tvcli")


def test_resolve_setting_prefers_environment(monkeypatch) -> None:
    monkeypatch.setenv(env_key("auth", "token"), "secret")

    assert resolve_setting("auth", "token", {"auth": {"token": "fallback"}}) == "secret"
