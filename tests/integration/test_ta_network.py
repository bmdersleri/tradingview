from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest

pytestmark = pytest.mark.network


def run_tvcli_json(*args: str) -> dict[str, object]:
    command = " ".join(
        shlex.quote(part) for part in [sys.executable, "-m", "tvcli", "--json", *args]
    )
    result = subprocess.run(
        ["bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_live_ta_get_returns_summary() -> None:
    payload = run_tvcli_json("ta", "get", "BIST:THYAO", "--interval", "1d")

    assert payload["ok"] is True
    data = payload["data"]
    assert data["symbol"] == "BIST:THYAO"
    assert data["interval"] == "1d"
    assert "summary" in data
    assert "RECOMMENDATION" in data["summary"]
