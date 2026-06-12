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


def test_live_screener_search_returns_candidates() -> None:
    payload = run_tvcli_json("data", "search", "THYAO", "--market", "turkey")

    assert payload["ok"] is True
    data = payload["data"]
    assert data["returned"] >= 1
    assert any(candidate["ticker"] == "BIST:THYAO" for candidate in data["candidates"])
