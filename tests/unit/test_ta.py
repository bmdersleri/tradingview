from __future__ import annotations

import json
from pathlib import Path

import pytest

from tvcli.errors import NotFoundError
from tvcli.layers.ta import (
    TaRequest,
    build_matrix_payload,
    build_multi_payload,
    build_snapshot_payload,
    derive_screener,
)


def test_derive_screener_from_exchange() -> None:
    assert derive_screener("BIST:THYAO") == "turkey"
    assert derive_screener("NASDAQ:NVDA") == "america"


def test_derive_screener_requires_explicit_value_for_unknown() -> None:
    with pytest.raises(NotFoundError):
        derive_screener("OTC:XYZ")


def test_build_snapshot_payload_uses_fixture() -> None:
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "ta_response.json"
        ).read_text(encoding="utf-8")
    )
    payload = build_snapshot_payload(
        TaRequest(symbol="BIST:THYAO", interval="1d", screener="turkey"),
        fixture,
    )

    assert payload["summary"]["recommendation"] == "BUY"
    assert payload["indicators"]["RSI"] == pytest.approx(56.2)


def test_matrix_and_multi_payloads_shape_rows() -> None:
    snapshot = {
        "symbol": "BIST:THYAO",
        "interval": "1d",
        "summary": {"recommendation": "BUY"},
        "oscillators": {"recommendation": "NEUTRAL"},
        "moving_averages": {"recommendation": "STRONG_BUY"},
        "indicators": {"RSI": 56.2},
    }
    multi = build_multi_payload(
        ("BIST:THYAO", "NASDAQ:NVDA"), "1d", (snapshot, snapshot)
    )
    matrix = build_matrix_payload("BIST:THYAO", ("1h", "1d"), (snapshot, snapshot))

    assert multi["returned"] == 2
    assert matrix["returned"] == 2
    assert matrix["intervals"] == ["1h", "1d"]
