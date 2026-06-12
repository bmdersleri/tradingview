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


def test_ohlcv_parser_ignores_bare_time_axis() -> None:
    # A real timescale_update carries a bare time-axis list alongside the {"v": ...}
    # bar entries. The bare list must NOT be parsed as a bar (regression: it used to
    # become a phantom first bar with time=t0, open=t1, high=t2, ...).
    frame = encode_frame(
        {
            "m": "timescale_update",
            "p": [
                "cs",
                {
                    "s1": {
                        "t": [1718064000, 1718150400, 1718236800, 0, 0, 0],
                        "s": [
                            {"v": [1718064000, 310, 315.5, 308, 312.5, 18234567]},
                            {"v": [1718150400, 312, 318.0, 311, 316.0, 19111222]},
                        ],
                    }
                },
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
        OhlcvBar(
            time=1718150400,
            open=312.0,
            high=318.0,
            low=311.0,
            close=316.0,
            volume=19111222.0,
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
