"""Live browser smoke tests for the chart screenshot layer.

Marked ``network`` and ``browser``: excluded from ``just test`` and run only via
``just test-live`` with a valid imported TradingView session and Chromium
installed. They prove two things end to end against the real site:

1. ``chart shot`` renders a non-empty chart for a known symbol.
2. The blank-layout anonymous fallback recovers candles when the authenticated
   render is treated as empty.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from tvcli.auth.session import require_session
from tvcli.layers import chart

pytestmark = [pytest.mark.network, pytest.mark.browser]

SYMBOL = "BIST:THYAO"
INTERVAL = "1d"
# A rendered candle chart clears this comfortably (~0.05 in practice); a blank
# drawing area sits near zero.
MIN_CONTENT_RATIO = 0.01


def _png_content_ratio(path: Path) -> float:
    """Fraction of pixels that differ from the top-left (background) pixel."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    offset = 8
    width = height = color_type = 0
    idat = b""
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        if chunk_type == b"IHDR":
            width, height, _bitd, color_type = struct.unpack(">IIBB", chunk[:10])
        elif chunk_type == b"IDAT":
            idat += chunk
        elif chunk_type == b"IEND":
            break
        offset += 12 + length

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    pos = 0

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        return b if pb <= pc else c

    for _y in range(height):
        filt = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if filt == 1:
                line[x] = (line[x] + a) & 0xFF
            elif filt == 2:
                line[x] = (line[x] + b) & 0xFF
            elif filt == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
            elif filt == 4:
                line[x] = (line[x] + paeth(a, b, c)) & 0xFF
        out += line
        prev = line

    base_r, base_g, base_b = out[0], out[1], out[2]
    non_bg = 0
    total = 0
    step = max(1, (width * height) // 40_000)
    for idx in range(0, width * height, step):
        o = idx * channels
        diff = (
            abs(out[o] - base_r) + abs(out[o + 1] - base_g) + abs(out[o + 2] - base_b)
        )
        total += 1
        if diff > 24:
            non_bg += 1
    return non_bg / total if total else 0.0


def test_live_chart_shot_renders_candles(tmp_path: Path) -> None:
    require_session()  # exit early with a clear error if no session is imported
    out = tmp_path / "chart.png"

    payload = chart.shot_chart(
        chart.ChartRequest(symbol=SYMBOL, interval=INTERVAL, out=out)
    )

    assert payload["symbol"] == SYMBOL
    assert payload["bytes"] > 0
    assert out.exists()
    # Whether or not the authenticated layout was blank, the returned image must
    # contain a real chart.
    assert _png_content_ratio(out) > MIN_CONTENT_RATIO


def test_live_chart_shot_anonymous_fallback_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    require_session()
    out = tmp_path / "chart_fallback.png"

    # Force only the first (authenticated) attempt to be seen as blank so the
    # live anonymous relaunch path is exercised; judge later attempts honestly.
    real_looks_blank = chart._canvas_looks_blank
    state = {"calls": 0}

    def forced_blank_once(page: object) -> bool:
        state["calls"] += 1
        if state["calls"] == 1:
            return True
        return real_looks_blank(page)

    monkeypatch.setattr(chart, "_canvas_looks_blank", forced_blank_once)

    payload = chart.shot_chart(
        chart.ChartRequest(symbol=SYMBOL, interval=INTERVAL, out=out)
    )

    assert payload["anonymous_fallback"] is True
    assert out.exists()
    # The anonymous context must still produce a candle chart, not a blank frame.
    assert _png_content_ratio(out) > MIN_CONTENT_RATIO
