from __future__ import annotations

import pytest

from tvcli.errors import UsageError
from tvcli.layers import indicators as ind


def test_sma_basic() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = ind.sma(values, 3)
    assert out[:2] == [None, None]
    assert out[2:] == [2.0, 3.0, 4.0]  # (1+2+3)/3, (2+3+4)/3, (3+4+5)/3


def test_wma_basic() -> None:
    # period 3 weights (1,2,3)/6: window [1,2,3] -> (1+4+9)/6 = 2.333...
    out = ind.wma([1.0, 2.0, 3.0, 4.0], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(14 / 6)
    assert out[3] == pytest.approx((2 + 6 + 12) / 6)


def test_ema_seed_is_sma() -> None:
    values = [2.0, 4.0, 6.0, 8.0, 10.0]
    out = ind.ema(values, 3)
    assert out[2] == pytest.approx(4.0)  # seed = (2+4+6)/3
    k = 2 / (3 + 1)
    assert out[3] == pytest.approx(8.0 * k + 4.0 * (1 - k))


def test_bollinger_bands_symmetry() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    mid, upper, lower = ind.bollinger(values, 3, 2.0)
    assert mid[2] == pytest.approx(2.0)
    # window [1,2,3] var = 2/3, std ~0.8165; band = mid +/- 2*std
    assert upper[2] == pytest.approx(2.0 + 2 * (2 / 3) ** 0.5)
    assert lower[2] == pytest.approx(2.0 - 2 * (2 / 3) ** 0.5)


def test_rsi_all_gains_is_100() -> None:
    values = [float(i) for i in range(1, 20)]  # strictly increasing
    out = ind.rsi(values, 14)
    assert out[13] is None  # warm-up: defined from index `period`
    assert out[14] == pytest.approx(100.0)


def test_rsi_known_value() -> None:
    # Mixed series; assert RSI stays in (0, 100) and is defined past warm-up.
    values = [
        44.0,
        44.25,
        44.5,
        43.75,
        44.5,
        45.0,
        45.5,
        45.25,
        46.0,
        47.0,
        46.75,
        46.5,
        46.25,
        47.75,
        47.0,
    ]
    out = ind.rsi(values, 14)
    assert out[14] is not None
    assert 0.0 < out[14] < 100.0


def test_macd_histogram_is_line_minus_signal() -> None:
    values = [float(i) for i in range(1, 60)]
    line, signal, hist = ind.macd(values, 12, 26, 9)
    idx = next(i for i, v in enumerate(hist) if v is not None)
    assert hist[idx] == pytest.approx(line[idx] - signal[idx])


def test_parse_spec_defaults() -> None:
    assert ind.parse_spec("wma").params == (20,)
    assert ind.parse_spec("wma:200").params == (200,)
    assert ind.parse_spec("bbands:20:3").params == (20, 3)
    assert ind.parse_spec("macd").params == (12, 26, 9)
    assert ind.parse_spec("RSI:14").kind == "rsi"  # case-insensitive


def test_parse_spec_partial_param_keeps_default() -> None:
    # empty middle token keeps the default
    assert ind.parse_spec("macd:12::9").params == (12, 26, 9)


@pytest.mark.parametrize(
    "raw",
    ["nope", "wma:abc", "rsi:1:2", "sma:0"],
)
def test_parse_and_compute_reject_bad_specs(raw: str) -> None:
    with pytest.raises(UsageError):
        spec = ind.parse_spec(raw)
        ind.compute(spec, [1.0, 2.0, 3.0])


def test_compute_overlay_vs_panel() -> None:
    closes = [float(i) for i in range(1, 40)]
    wma = ind.compute(ind.parse_spec("wma:5"), closes)
    assert wma.spec.is_overlay is True
    assert wma.last is not None
    rsi = ind.compute(ind.parse_spec("rsi:14"), closes)
    assert rsi.spec.is_overlay is False
    assert "rsi" in rsi.series
