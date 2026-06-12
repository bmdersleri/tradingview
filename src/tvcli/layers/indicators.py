"""Pure technical-indicator math over a close-price series.

All functions take a list of floats and return a list of the same length, with
``None`` for warm-up positions where the indicator is not yet defined. No numpy
or pandas dependency — the math is small, deterministic, and fixture-testable.

The :func:`parse_spec` mini-syntax (``NAME[:p1[:p2[:p3]]]``) maps a CLI string
like ``wma:200`` or ``macd:12:26:9`` to an :class:`IndicatorSpec`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import UsageError

FloatSeq = list[float]
OptFloatSeq = list[float | None]

# Indicators drawn on the price axis vs. those needing their own subpanel.
OVERLAY_KINDS = frozenset({"sma", "ema", "wma", "bbands"})
PANEL_KINDS = frozenset({"rsi", "macd"})


def sma(values: FloatSeq, period: int) -> OptFloatSeq:
    if period <= 0:
        raise UsageError(f"period must be positive, got {period}.")
    out: OptFloatSeq = [None] * len(values)
    if len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: FloatSeq, period: int) -> OptFloatSeq:
    if period <= 0:
        raise UsageError(f"period must be positive, got {period}.")
    out: OptFloatSeq = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    # Seed with the simple average of the first `period` values.
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def wma(values: FloatSeq, period: int) -> OptFloatSeq:
    if period <= 0:
        raise UsageError(f"period must be positive, got {period}.")
    out: OptFloatSeq = [None] * len(values)
    weights = list(range(1, period + 1))
    denom = float(sum(weights))
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        out[i] = sum(v * w for v, w in zip(window, weights, strict=True)) / denom
    return out


def bollinger(
    values: FloatSeq, period: int, num_std: float
) -> tuple[OptFloatSeq, OptFloatSeq, OptFloatSeq]:
    if period <= 0:
        raise UsageError(f"period must be positive, got {period}.")
    mid = sma(values, period)
    upper: OptFloatSeq = [None] * len(values)
    lower: OptFloatSeq = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = mid[i]
        assert mean is not None  # sma defined from period-1 onward
        variance = sum((v - mean) ** 2 for v in window) / period
        std = variance**0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return mid, upper, lower


def rsi(values: FloatSeq, period: int) -> OptFloatSeq:
    if period <= 0:
        raise UsageError(f"period must be positive, got {period}.")
    out: OptFloatSeq = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(
    values: FloatSeq, fast: int, slow: int, signal: int
) -> tuple[OptFloatSeq, OptFloatSeq, OptFloatSeq]:
    if not (fast > 0 and slow > 0 and signal > 0):
        raise UsageError("macd periods must be positive.")
    if fast >= slow:
        raise UsageError(f"macd fast ({fast}) must be smaller than slow ({slow}).")
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    macd_line: OptFloatSeq = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema, strict=True)
    ]
    # Signal line is an EMA of the defined macd values; align it back by index.
    defined = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: OptFloatSeq = [None] * len(values)
    histogram: OptFloatSeq = [None] * len(values)
    if len(defined) >= signal:
        macd_vals = [v for _, v in defined]
        sig_vals = ema(macd_vals, signal)
        for (idx, _), sig in zip(defined, sig_vals, strict=True):
            if sig is None:
                continue
            signal_line[idx] = sig
            line = macd_line[idx]
            assert line is not None
            histogram[idx] = line - sig
    return macd_line, signal_line, histogram


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    raw: str
    kind: str
    params: tuple[int, ...] = field(default_factory=tuple)

    @property
    def period(self) -> int | None:
        return self.params[0] if self.params else None

    @property
    def is_overlay(self) -> bool:
        return self.kind in OVERLAY_KINDS


# kind -> number of integer params and their defaults.
_SPEC_DEFAULTS: dict[str, tuple[int, ...]] = {
    "sma": (20,),
    "ema": (20,),
    "wma": (20,),
    "bbands": (20, 2),
    "rsi": (14,),
    "macd": (12, 26, 9),
}


def parse_spec(raw: str) -> IndicatorSpec:
    """Parse ``NAME[:p1[:p2[:p3]]]`` into an :class:`IndicatorSpec`.

    Unknown names, too many params, or non-integer params raise
    :class:`UsageError` (exit 2) with the offending spec in the hint.
    """
    parts = raw.strip().split(":")
    kind = parts[0].lower()
    if kind not in _SPEC_DEFAULTS:
        known = ", ".join(sorted(_SPEC_DEFAULTS))
        raise UsageError(
            f"Unknown indicator '{kind}'.",
            hint=f"Spec '{raw}' is invalid. Known indicators: {known}.",
        )
    defaults = _SPEC_DEFAULTS[kind]
    given = parts[1:]
    if len(given) > len(defaults):
        raise UsageError(
            f"Indicator '{kind}' takes at most {len(defaults)} parameter(s).",
            hint=f"Spec '{raw}' has too many parameters.",
        )
    params: list[int] = list(defaults)
    for i, token in enumerate(given):
        if token == "":
            continue
        try:
            params[i] = int(token)
        except ValueError as exc:
            raise UsageError(
                f"Indicator parameter '{token}' is not an integer.",
                hint=f"Spec '{raw}' has a non-integer parameter.",
            ) from exc
    return IndicatorSpec(raw=raw, kind=kind, params=tuple(params))


@dataclass(frozen=True, slots=True)
class ComputedIndicator:
    spec: IndicatorSpec
    # One named series per drawable line (e.g. {"wma": [...]},
    # {"mid": [...], "upper": [...], "lower": [...]}).
    series: dict[str, OptFloatSeq]

    @property
    def last(self) -> float | None:
        # Last defined value of the primary series, for the JSON payload.
        primary = next(iter(self.series.values()))
        for value in reversed(primary):
            if value is not None:
                return round(value, 4)
        return None


def compute(spec: IndicatorSpec, closes: FloatSeq) -> ComputedIndicator:
    """Compute the series for one spec against a close-price list."""
    if spec.kind == "sma":
        series = {"sma": sma(closes, spec.params[0])}
    elif spec.kind == "ema":
        series = {"ema": ema(closes, spec.params[0])}
    elif spec.kind == "wma":
        series = {"wma": wma(closes, spec.params[0])}
    elif spec.kind == "bbands":
        mid, upper, lower = bollinger(closes, spec.params[0], float(spec.params[1]))
        series = {"mid": mid, "upper": upper, "lower": lower}
    elif spec.kind == "rsi":
        series = {"rsi": rsi(closes, spec.params[0])}
    elif spec.kind == "macd":
        line, signal, hist = macd(
            closes, spec.params[0], spec.params[1], spec.params[2]
        )
        series = {"macd": line, "signal": signal, "histogram": hist}
    else:  # pragma: no cover - parse_spec guards the kind set
        raise UsageError(f"Unsupported indicator '{spec.kind}'.")
    return ComputedIndicator(spec=spec, series=series)
