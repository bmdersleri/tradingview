from __future__ import annotations

import logging
from pathlib import Path

from tvcli.config import resolve_setting, save_config
from tvcli.logging_utils import setup_logger


def test_setup_logger_with_file(tmp_path: Path) -> None:
    log_file = tmp_path / "subdir" / "test.log"
    # Ensure setup_logger sets up FileHandler (lines 37-43)
    logger = setup_logger("test_file_logger", log_file=str(log_file))

    # Check that file handler was added
    handlers = logger.handlers
    assert any(isinstance(h, logging.FileHandler) for h in handlers)

    # Check that log formatting with exception info works (lines 22-23)
    try:
        raise ValueError("Simulated error")
    except ValueError:
        logger.exception("Error occurred")

    assert log_file.exists()
    log_content = log_file.read_text(encoding="utf-8")
    assert "Error occurred" in log_content
    assert "ValueError: Simulated error" in log_content


def test_config_toml_value_fallback(tmp_path: Path) -> None:
    # Line 126: _toml_value fallback for non-primitive types (e.g. list)
    config_file = tmp_path / "config.toml"
    config_data = {"alerts": {"channels": ["tg", "email"]}}
    save_config(config_data, config_file)
    assert config_file.exists()
    content = config_file.read_text(encoding="utf-8")
    assert "channels = \"['tg', 'email']\"" in content


def test_config_resolve_setting_non_dict() -> None:
    # Lines 88, 91: resolve_setting on non-dict objects
    # 1. Non-dict section part (line 88)
    assert (
        resolve_setting(
            "alerts.non_dict", "key", {"alerts": "not-a-dict"}, default="def"
        )
        == "def"
    )

    # 2. Non-dict final part (line 91)
    assert (
        resolve_setting("alerts", "key", {"alerts": "not-a-dict"}, default="def")
        == "def"
    )
