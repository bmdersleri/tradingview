"""Configuration helpers for tvcli."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

APP_NAME = "tvcli"
DEFAULT_CONFIG_FILENAME = "config.toml"


def _xdg_home(env_var: str, fallback: Path) -> Path:
    value = os.environ.get(env_var)
    return Path(value).expanduser() if value else fallback


def default_config_dir() -> Path:
    return _xdg_home("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME


def default_config_path() -> Path:
    return default_config_dir() / DEFAULT_CONFIG_FILENAME


def default_state_dir() -> Path:
    return _xdg_home("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP_NAME


def default_data_dir() -> Path:
    return _xdg_home("XDG_DATA_HOME", Path.home() / ".local" / "share") / APP_NAME


def default_cache_path() -> Path:
    return default_state_dir() / "cache.sqlite3"


def default_archive_path() -> Path:
    return default_data_dir() / "archive.sqlite3"


def default_session_path() -> Path:
    return default_config_dir() / "session.json"


def default_storage_state_path() -> Path:
    return default_config_dir() / "storage_state.json"


def default_alerts_path() -> Path:
    return default_data_dir() / "alerts.jsonl"


def ensure_config_file(path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("# tvcli configuration\n", encoding="utf-8")
    return config_path


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = ensure_config_file(path)
    raw = config_path.read_bytes()
    if not raw.strip():
        return {}
    return tomllib.loads(raw.decode("utf-8"))


def env_key(section: str, key: str) -> str:
    normalized = f"{section}_{key}".replace("-", "_").upper()
    return f"TVCLI_{normalized}"


def resolve_setting(
    section: str,
    key: str,
    config: dict[str, Any] | None = None,
    default: Any = None,
) -> Any:
    env_value = os.environ.get(env_key(section, key))
    if env_value is not None:
        return env_value
    current: Any = config or {}
    for part in section.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part, {})
    if not isinstance(current, dict):
        return default
    return current.get(key, default)
