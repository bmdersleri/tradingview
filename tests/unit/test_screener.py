from __future__ import annotations

import json
from pathlib import Path

from tvcli.layers.screener import (
    FieldInfo,
    ScreenRequest,
    ScreenResult,
    build_fields_payload,
    build_quote_payload,
    build_screen_payload,
    build_search_payload,
    parse_where,
    split_select,
)


def test_parse_where_supports_multiple_clauses() -> None:
    clauses = parse_where("RSI<30;volume>1000000;name in ['THYAO','AKBNK']")

    assert clauses[0].field == "RSI"
    assert clauses[0].operator == "<"
    assert clauses[0].value == 30
    assert clauses[1].operator == ">"
    assert clauses[2].operator == "in"
    assert clauses[2].value == ("THYAO", "AKBNK")


def test_split_select_trims_fields() -> None:
    assert split_select("name, close, volume") == ("name", "close", "volume")


def test_build_screen_payload_normalizes_ticker() -> None:
    payload = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "screener_response.json"
        ).read_text(encoding="utf-8")
    )
    request = ScreenRequest(
        market=payload["market"],
        select=("name", "close", "volume", "RSI", "market_cap_basic"),
        where=(),
        order_by="volume",
        descending=True,
        limit=20,
    )
    result = ScreenResult(
        rows=tuple(payload["rows"]), total_matches=payload["total_matches"]
    )

    screen = build_screen_payload(request, result)

    assert screen["total_matches"] == 412
    assert screen["returned"] == 2
    assert screen["rows"][1]["ticker"] == "BIST:AKBNK"


def test_supporting_payload_builders() -> None:
    fields = build_fields_payload(
        "turkey",
        (FieldInfo(name="RSI", type="number", description="relative strength"),),
    )
    search = build_search_payload("THYAO", "turkey", ({"ticker": "BIST:THYAO"},))
    quote = build_quote_payload(("BIST:THYAO",), ({"ticker": "BIST:THYAO"},))

    assert fields["returned"] == 1
    assert search["returned"] == 1
    assert quote["symbols"] == ["BIST:THYAO"]
