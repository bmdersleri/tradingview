from __future__ import annotations

from tvcli.layers.ohlcv import (
    OhlcvBar,
    build_request_messages,
    decode_frames,
    encode_frame,
    extract_bars,
    parse_updates,
    resolution_for_interval,
)


def test_ohlcv_wire_parser_extracts_bars() -> None:
    frame = encode_frame(
        {
            "m": "timescale_update",
            "p": [
                "cs",
                {"s1": {"s": [{"v": [1718064000, 310, 315.5, 308, 312.5, 18234567]}]}},
            ],
        }
    )

    messages = parse_updates(decode_frames(frame))
    bars = extract_bars(messages)

    assert bars == (
        OhlcvBar(
            time=1718064000,
            open=310.0,
            high=315.5,
            low=308.0,
            close=312.5,
            volume=18234567.0,
        ),
    )


def test_ohlcv_request_messages_include_auth_and_series() -> None:
    messages = build_request_messages(
        sessionid="abc",
        symbol="BIST:THYAO",
        interval="1d",
        bars=500,
    )

    assert resolution_for_interval("1d") == "1D"
    assert len(messages) == 4
    assert "set_auth_token" in messages[0]
    assert (
        '={\\"adjustment\\":\\"splits\\",\\"symbol\\":\\"BIST:THYAO\\"}' in messages[2]
    )
    assert "create_series" in messages[-1]
