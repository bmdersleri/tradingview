"""Rule-based market-regime detection and buy/sell/hold signal synthesis.

Reuses the pure math in :mod:`tvcli.layers.indicators`. The idea: there is no
single best indicator — the right one depends on the regime. We classify the
regime (trending / ranging / volatile), let four indicators vote, then weight
those votes by how well each suits the detected regime. Everything is
deterministic and explainable; no machine learning, no extra dependency.

This is a decision-support tool, not financial advice. Every indicator is
lagging and derived from past prices; the regime classification cannot predict
the future.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from . import indicators as ind

Vote = Literal[-1, 0, 1]  # sell / hold / buy
SignalLabel = Literal["buy", "sell", "hold"]
RegimeKind = Literal["trending_up", "trending_down", "ranging", "volatile"]

DISCLAIMER = (
    "Decision-support only, not financial advice. Indicators are lagging and "
    "derived from past prices; regime detection cannot predict the future."
)

# Tunable thresholds. Module-level so they are documented and testable in one
# place.
_VOLATILE_RETURN_STD = 0.035  # std of bar-to-bar returns above this => volatile
_TREND_SEPARATION = 0.02  # |sma_fast - sma_slow| / price below this => ranging
_SLOPE_LOOKBACK = 10  # bars used to measure the fast-MA slope
_VOL_LOOKBACK = 30  # bars used to measure return volatility
_SIGNAL_THRESHOLD = 0.15  # |score| above this flips hold -> buy/sell

# Periods used by the regime/vote math. Adaptive to the series length so short
# histories still produce a (weaker) read instead of all-None.
_FAST = 50
_SLOW = 200


def _last_defined(series: ind.OptFloatSeq) -> float | None:
    for value in reversed(series):
        if value is not None:
            return value
    return None


def _periods_for(n: int) -> tuple[int, int]:
    """Pick (fast, slow) MA periods that fit a series of length ``n``."""
    if n >= _SLOW + 5:
        return _FAST, _SLOW
    # Scale down for short series so both MAs are defined.
    slow = max(10, n // 2)
    fast = max(5, slow // 4)
    return fast, slow


def _return_volatility(closes: ind.FloatSeq, lookback: int = _VOL_LOOKBACK) -> float:
    """Std of recent bar-to-bar percentage returns.

    This isolates choppiness from trend: a clean linear move has near-constant
    returns (low std) even though its price range is wide, while a series that
    keeps reversing has a high return std. Bollinger bandwidth alone conflates
    the two, so it is reported as a metric but not used to flag volatility.
    """
    rets: list[float] = []
    window = closes[-(lookback + 1) :] if len(closes) > lookback + 1 else closes
    for prev, cur in zip(window, window[1:], strict=False):
        if prev:
            rets.append((cur - prev) / abs(prev))
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / len(rets)
    return float(variance**0.5)


@dataclass(frozen=True, slots=True)
class RegimeReport:
    kind: RegimeKind
    strength: float  # 0..1, how pronounced the trend separation is
    volatility: float  # Bollinger bandwidth (upper-lower)/mid
    metrics: dict[str, float] = field(default_factory=dict)


def detect_regime(closes: ind.FloatSeq) -> RegimeReport:
    n = len(closes)
    price = closes[-1] if closes else 0.0
    fast_p, slow_p = _periods_for(n)

    fast = _last_defined(ind.sma(closes, fast_p))
    slow = _last_defined(ind.sma(closes, slow_p))
    _, upper, lower = ind.bollinger(closes, min(20, max(2, n // 2)), 2.0)
    mid = _last_defined(ind.sma(closes, min(20, max(2, n // 2))))
    up = _last_defined(upper)
    lo = _last_defined(lower)

    bandwidth = 0.0
    if up is not None and lo is not None and mid:
        bandwidth = (up - lo) / abs(mid)

    ret_vol = _return_volatility(closes)

    separation = 0.0
    if fast is not None and slow is not None and price:
        separation = (fast - slow) / abs(price)

    # Fast-MA slope over the recent lookback (normalized by price).
    fast_series = ind.sma(closes, fast_p)
    slope = 0.0
    defined = [v for v in fast_series if v is not None]
    if len(defined) > _SLOPE_LOOKBACK and price:
        slope = (defined[-1] - defined[-1 - _SLOPE_LOOKBACK]) / abs(price)

    strength = min(1.0, abs(separation) / (_TREND_SEPARATION * 4))
    metrics = {
        "separation": round(separation, 5),
        "slope": round(slope, 5),
        "bandwidth": round(bandwidth, 5),
        "return_std": round(ret_vol, 5),
    }

    if ret_vol >= _VOLATILE_RETURN_STD:
        kind: RegimeKind = "volatile"
    elif abs(separation) < _TREND_SEPARATION:
        kind = "ranging"
    elif separation > 0 and slope >= 0:
        kind = "trending_up"
    elif separation < 0 and slope <= 0:
        kind = "trending_down"
    else:
        # Separation and slope disagree (MA spread one way, recent drift the
        # other) — treat as a range rather than a confident trend.
        kind = "ranging"

    return RegimeReport(
        kind=kind,
        strength=round(strength, 4),
        volatility=round(ret_vol, 4),
        metrics=metrics,
    )


@dataclass(frozen=True, slots=True)
class IndicatorVote:
    indicator: str
    vote: Vote
    strength: float  # 0..1 conviction of this single vote
    reason: str


def _vote_ma_cross(closes: ind.FloatSeq) -> IndicatorVote:
    fast_p, slow_p = _periods_for(len(closes))
    fast_s = ind.sma(closes, fast_p)
    slow_s = ind.sma(closes, slow_p)
    fast = _last_defined(fast_s)
    slow = _last_defined(slow_s)
    if fast is None or slow is None:
        return IndicatorVote("ma_cross", 0, 0.0, "Not enough data for moving averages.")
    price = abs(closes[-1]) or 1.0
    gap = (fast - slow) / price
    # Detect a recent crossover for extra conviction.
    recent_cross = False
    paired = [
        (f, s)
        for f, s in zip(fast_s, slow_s, strict=True)
        if f is not None and s is not None
    ]
    if len(paired) > 6:
        window = paired[-6:]
        signs = [1 if f >= s else -1 for f, s in window]
        recent_cross = len(set(signs)) > 1
    strength = min(1.0, abs(gap) / 0.05)
    if recent_cross:
        strength = min(1.0, strength + 0.3)
    if fast > slow:
        return IndicatorVote(
            "ma_cross",
            1,
            strength,
            f"SMA-{fast_p} above SMA-{slow_p}"
            + (" (recent golden cross)" if recent_cross else "")
            + ".",
        )
    if fast < slow:
        return IndicatorVote(
            "ma_cross",
            -1,
            strength,
            f"SMA-{fast_p} below SMA-{slow_p}"
            + (" (recent death cross)" if recent_cross else "")
            + ".",
        )
    return IndicatorVote("ma_cross", 0, 0.0, "Moving averages are flat.")


def _vote_macd(closes: ind.FloatSeq) -> IndicatorVote:
    line_s, signal_s, hist_s = ind.macd(closes, 12, 26, 9)
    line = _last_defined(line_s)
    signal = _last_defined(signal_s)
    hist = _last_defined(hist_s)
    if line is None or signal is None or hist is None:
        return IndicatorVote("macd", 0, 0.0, "Not enough data for MACD.")
    price = abs(closes[-1]) or 1.0
    strength = min(1.0, abs(hist) / (price * 0.01))
    if hist > 0:
        return IndicatorVote(
            "macd", 1, strength, "MACD line above signal (bullish momentum)."
        )
    if hist < 0:
        return IndicatorVote(
            "macd", -1, strength, "MACD line below signal (bearish momentum)."
        )
    return IndicatorVote("macd", 0, 0.0, "MACD at the signal line.")


def _vote_rsi(closes: ind.FloatSeq) -> IndicatorVote:
    value = _last_defined(ind.rsi(closes, 14))
    if value is None:
        return IndicatorVote("rsi", 0, 0.0, "Not enough data for RSI.")
    if value < 30:
        return IndicatorVote(
            "rsi",
            1,
            min(1.0, (30 - value) / 30 + 0.4),
            f"RSI {value:.0f} is oversold (<30).",
        )
    if value > 70:
        return IndicatorVote(
            "rsi",
            -1,
            min(1.0, (value - 70) / 30 + 0.4),
            f"RSI {value:.0f} is overbought (>70).",
        )
    return IndicatorVote("rsi", 0, abs(value - 50) / 50, f"RSI {value:.0f} is neutral.")


def _vote_bbands(closes: ind.FloatSeq) -> IndicatorVote:
    period = min(20, max(2, len(closes) // 2))
    _, upper_s, lower_s = ind.bollinger(closes, period, 2.0)
    upper = _last_defined(upper_s)
    lower = _last_defined(lower_s)
    if upper is None or lower is None or not closes:
        return IndicatorVote("bbands", 0, 0.0, "Not enough data for Bollinger Bands.")
    price = closes[-1]
    span = (upper - lower) or 1.0
    if price <= lower:
        return IndicatorVote(
            "bbands",
            1,
            min(1.0, (lower - price) / span + 0.5),
            "Price at/below the lower band (mean-reversion buy).",
        )
    if price >= upper:
        return IndicatorVote(
            "bbands",
            -1,
            min(1.0, (price - upper) / span + 0.5),
            "Price at/above the upper band (mean-reversion sell).",
        )
    return IndicatorVote("bbands", 0, 0.0, "Price within the Bollinger Bands.")


def vote_signals(closes: ind.FloatSeq) -> list[IndicatorVote]:
    return [
        _vote_ma_cross(closes),
        _vote_macd(closes),
        _vote_rsi(closes),
        _vote_bbands(closes),
    ]


# Per-regime weights: trust trend-following indicators in trends, mean-reversion
# ones in ranges, and nobody fully when volatile.
_REGIME_WEIGHTS: dict[RegimeKind, dict[str, float]] = {
    "trending_up": {"ma_cross": 1.0, "macd": 1.0, "rsi": 0.3, "bbands": 0.3},
    "trending_down": {"ma_cross": 1.0, "macd": 1.0, "rsi": 0.3, "bbands": 0.3},
    "ranging": {"ma_cross": 0.3, "macd": 0.3, "rsi": 1.0, "bbands": 1.0},
    "volatile": {"ma_cross": 0.5, "macd": 0.5, "rsi": 0.5, "bbands": 0.5},
}

_SELECTED_SPECS: dict[RegimeKind, tuple[str, ...]] = {
    "trending_up": ("sma:50", "sma:200", "macd:12:26:9"),
    "trending_down": ("sma:50", "sma:200", "macd:12:26:9"),
    "ranging": ("rsi:14", "bbands:20:2"),
    "volatile": ("bbands:20:2", "rsi:14"),
}


def selected_specs(regime: RegimeKind) -> tuple[str, ...]:
    return _SELECTED_SPECS[regime]


@dataclass(frozen=True, slots=True)
class SignalReport:
    regime: RegimeReport
    signal: SignalLabel
    score: float  # -1..1 weighted consensus
    confidence: float  # 0..1
    votes: list[IndicatorVote]
    selected_indicators: tuple[str, ...]
    free_float: float | None = None  # free-float ratio (%), if known
    liquidity_note: str | None = None  # liquidity/manipulation-risk warning
    # Recent adverse free-float events from the local archive, kept as context.
    risk_events: tuple[dict[str, Any], ...] = ()
    event_note: str | None = None  # summary of the adverse events, if any


# Below this free-float percentage a stock is thin and easier to push around, so
# we keep the signal direction but discount the confidence in it.
LOW_FREE_FLOAT_PCT = 20.0
_LOW_FLOAT_CONFIDENCE_FACTOR = 0.7


def low_float_note(free_float: float) -> str | None:
    """The manipulation-risk note for a thin float, or None when liquid enough."""
    if free_float >= LOW_FREE_FLOAT_PCT:
        return None
    return (
        f"Low free-float ({free_float:.1f}%): thin liquidity, higher "
        "manipulation risk — treat the signal with extra caution."
    )


def damp_confidence(confidence: float, free_float: float) -> float:
    """Discount confidence for a thin float; leave it untouched otherwise."""
    if free_float >= LOW_FREE_FLOAT_PCT:
        return confidence
    return round(confidence * _LOW_FLOAT_CONFIDENCE_FACTOR, 4)


def apply_liquidity(report: SignalReport, free_float: float) -> SignalReport:
    """Attach a free-float read; damp confidence when the float is thin.

    Free-float is a static liquidity metric, not a directional signal — it never
    flips buy/sell/hold, it only tempers how much to trust a thin-stock call.
    """
    from dataclasses import replace

    return replace(
        report,
        confidence=damp_confidence(report.confidence, free_float),
        free_float=round(free_float, 2),
        liquidity_note=low_float_note(free_float),
    )


# Free-float events that argue for *extra* caution on a long/buy read: a sudden
# float contraction or a drop through the thin-float threshold all reduce
# tradability without changing the price-derived direction. Like free-float
# itself, they temper confidence and add context — they never flip the signal.
_ADVERSE_EVENT_TYPES = frozenset(
    {
        "ratio_jump_down",
        "ratio_threshold_cross_down",
        "float_shares_jump_down",
        "liquidity_risk_low_float",
    }
)
_EVENT_CONFIDENCE_FACTOR = 0.85


def adverse_events(
    events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Keep only the high-severity adverse events worth surfacing on a signal."""
    return tuple(
        dict(event)
        for event in events
        if event.get("event_type") in _ADVERSE_EVENT_TYPES
        and event.get("severity") == "high"
    )


def event_risk_note(events: Sequence[Mapping[str, Any]]) -> str | None:
    """A one-line summary of adverse free-float events, or None when there are none."""
    if not events:
        return None
    kinds = ", ".join(sorted({str(event.get("event_type")) for event in events}))
    return (
        f"Recent adverse free-float event(s) ({kinds}): float-side risk rose "
        "lately — treat the signal with extra caution."
    )


def damp_for_events(confidence: float, events: Sequence[Mapping[str, Any]]) -> float:
    """Discount confidence once when adverse events are present; else unchanged."""
    if not events:
        return confidence
    return round(confidence * _EVENT_CONFIDENCE_FACTOR, 4)


def apply_event_risk(
    report: SignalReport, events: Sequence[Mapping[str, Any]]
) -> SignalReport:
    """Attach recent adverse free-float events; damp confidence when any are present.

    Mirrors :func:`apply_liquidity`: events are context, never a direction flip.
    Compose the two for the full picture (free-float level + recent float moves).
    """
    from dataclasses import replace

    adverse = adverse_events(events)
    if not adverse:
        return report
    return replace(
        report,
        confidence=damp_for_events(report.confidence, adverse),
        risk_events=adverse,
        event_note=event_risk_note(adverse),
    )


def analyze_signal(closes: ind.FloatSeq) -> SignalReport:
    regime = detect_regime(closes)
    votes = vote_signals(closes)
    weights = _REGIME_WEIGHTS[regime.kind]

    numerator = sum(weights[v.indicator] * v.vote * v.strength for v in votes)
    denominator = sum(weights[v.indicator] for v in votes) or 1.0
    score = numerator / denominator

    if score > _SIGNAL_THRESHOLD:
        label: SignalLabel = "buy"
    elif score < -_SIGNAL_THRESHOLD:
        label = "sell"
    else:
        label = "hold"

    # Confidence blends consensus magnitude with how clear the regime is. A
    # volatile regime caps confidence (regime.strength is low / damped).
    regime_clarity = regime.strength if regime.kind != "volatile" else 0.3
    confidence = max(0.0, min(1.0, abs(score) * (0.5 + 0.5 * regime_clarity)))

    return SignalReport(
        regime=regime,
        signal=label,
        score=round(score, 4),
        confidence=round(confidence, 4),
        votes=votes,
        selected_indicators=selected_specs(regime.kind),
    )


def signal_payload(report: SignalReport) -> dict[str, object]:
    """Serialize a SignalReport into the JSON envelope ``data`` shape."""
    return {
        "signal": report.signal,
        "confidence": report.confidence,
        "score": report.score,
        "regime": {
            "kind": report.regime.kind,
            "strength": report.regime.strength,
            "volatility": report.regime.volatility,
            "metrics": report.regime.metrics,
        },
        "votes": [
            {
                "indicator": v.indicator,
                "vote": v.vote,
                "strength": round(v.strength, 4),
                "reason": v.reason,
            }
            for v in report.votes
        ],
        "selected_indicators": list(report.selected_indicators),
        "liquidity": {
            "free_float": report.free_float,
            "note": report.liquidity_note,
            "events": list(report.risk_events),
            "event_note": report.event_note,
        },
        "disclaimer": DISCLAIMER,
    }
