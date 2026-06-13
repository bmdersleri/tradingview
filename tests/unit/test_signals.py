from __future__ import annotations

import math

from tvcli.layers import signals as s


def _uptrend(n: int = 260) -> list[float]:
    return [100.0 + i * 1.5 for i in range(n)]


def _downtrend(n: int = 260) -> list[float]:
    return [500.0 - i * 1.5 for i in range(n)]


def _ranging(n: int = 260) -> list[float]:
    return [100.0 + math.sin(i / 5) * 2 for i in range(n)]


def _volatile(n: int = 260) -> list[float]:
    return [100.0 + math.sin(i / 3) * 25 for i in range(n)]


def test_detect_regime_trending_up() -> None:
    r = s.detect_regime(_uptrend())
    assert r.kind == "trending_up"


def test_detect_regime_trending_down() -> None:
    r = s.detect_regime(_downtrend())
    assert r.kind == "trending_down"


def test_detect_regime_ranging() -> None:
    r = s.detect_regime(_ranging())
    assert r.kind == "ranging"


def test_detect_regime_volatile() -> None:
    r = s.detect_regime(_volatile())
    assert r.kind == "volatile"
    assert r.volatility >= s._VOLATILE_RETURN_STD


def test_analyze_signal_uptrend_is_buy() -> None:
    r = s.analyze_signal(_uptrend())
    assert r.signal == "buy"
    assert r.score > 0
    assert 0.0 <= r.confidence <= 1.0


def test_analyze_signal_downtrend_is_sell() -> None:
    r = s.analyze_signal(_downtrend())
    assert r.signal == "sell"
    assert r.score < 0


def test_volatile_caps_confidence() -> None:
    r = s.analyze_signal(_volatile())
    # Volatile regime should never report high conviction.
    assert r.confidence <= 0.5


def test_vote_rsi_oversold_is_buy() -> None:
    # A long decline pushes RSI into oversold territory.
    closes = [100.0 - i for i in range(40)]
    vote = s._vote_rsi(closes)
    assert vote.indicator == "rsi"
    assert vote.vote == 1
    assert "oversold" in vote.reason.lower()


def test_vote_rsi_overbought_is_sell() -> None:
    closes = [100.0 + i for i in range(40)]
    vote = s._vote_rsi(closes)
    assert vote.vote == -1
    assert "overbought" in vote.reason.lower()


def test_vote_ma_cross_reports_direction() -> None:
    up = s._vote_ma_cross(_uptrend())
    assert up.vote == 1
    assert "above" in up.reason.lower()
    down = s._vote_ma_cross(_downtrend())
    assert down.vote == -1


def test_selected_specs_per_regime() -> None:
    assert s.selected_specs("trending_up") == ("sma:50", "sma:200", "macd:12:26:9")
    assert s.selected_specs("ranging") == ("rsi:14", "bbands:20:2")
    assert s.selected_specs("volatile") == ("bbands:20:2", "rsi:14")


def test_signal_payload_shape() -> None:
    report = s.analyze_signal(_uptrend())
    payload = s.signal_payload(report)
    assert payload["signal"] in {"buy", "sell", "hold"}
    assert "confidence" in payload
    assert payload["regime"]["kind"] == "trending_up"
    assert len(payload["votes"]) == 4
    assert {v["indicator"] for v in payload["votes"]} == {
        "ma_cross",
        "macd",
        "rsi",
        "bbands",
    }
    assert "disclaimer" in payload
    assert isinstance(payload["selected_indicators"], list)


def test_apply_liquidity_low_float_damps_confidence() -> None:
    base = s.analyze_signal(_uptrend())
    enriched = s.apply_liquidity(base, 12.5)
    assert enriched.free_float == 12.5
    assert enriched.liquidity_note is not None
    assert "manipulation" in enriched.liquidity_note.lower()
    # Confidence is reduced (×0.7) but direction is unchanged.
    assert enriched.confidence < base.confidence
    assert enriched.signal == base.signal


def test_apply_liquidity_high_float_keeps_confidence() -> None:
    base = s.analyze_signal(_uptrend())
    enriched = s.apply_liquidity(base, 55.0)
    assert enriched.free_float == 55.0
    assert enriched.liquidity_note is None
    assert enriched.confidence == base.confidence


def test_signal_payload_has_liquidity_block() -> None:
    payload = s.signal_payload(s.analyze_signal(_uptrend()))
    assert "liquidity" in payload
    assert payload["liquidity"] == {
        "free_float": None,
        "note": None,
        "events": [],
        "event_note": None,
    }


def _high(event_type: str) -> dict[str, object]:
    return {"event_type": event_type, "severity": "high", "code": "X"}


def test_adverse_events_filters_to_high_severity_adverse() -> None:
    events = [
        _high("ratio_jump_down"),
        _high("float_shares_jump_down"),
        {"event_type": "ratio_jump_up", "severity": "high"},  # adverse type? no
        {"event_type": "ratio_jump_down", "severity": "medium"},  # not high
        {"event_type": "new_52w_high_ratio", "severity": "high"},  # not adverse
    ]
    kept = s.adverse_events(events)
    assert {e["event_type"] for e in kept} == {
        "ratio_jump_down",
        "float_shares_jump_down",
    }


def test_apply_event_risk_damps_confidence_and_attaches() -> None:
    base = s.analyze_signal(_uptrend())
    enriched = s.apply_event_risk(base, [_high("ratio_threshold_cross_down")])
    # Direction unchanged; confidence discounted once; events + note attached.
    assert enriched.signal == base.signal
    assert enriched.confidence < base.confidence
    assert len(enriched.risk_events) == 1
    assert enriched.event_note is not None
    assert "caution" in enriched.event_note.lower()


def test_apply_event_risk_noop_without_adverse_events() -> None:
    base = s.analyze_signal(_uptrend())
    # No events, and non-adverse events, both leave the report untouched.
    assert s.apply_event_risk(base, []) is base
    only_benign = [{"event_type": "new_52w_high_ratio", "severity": "high"}]
    assert s.apply_event_risk(base, only_benign) is base


def test_apply_event_risk_composes_with_liquidity() -> None:
    base = s.analyze_signal(_uptrend())
    after_float = s.apply_liquidity(base, 12.0)
    after_both = s.apply_event_risk(after_float, [_high("float_shares_jump_down")])
    # Both damps stack; the free-float read is preserved.
    assert after_both.confidence < after_float.confidence
    assert after_both.free_float == 12.0
    assert after_both.liquidity_note is not None
    assert after_both.event_note is not None
    payload = s.signal_payload(after_both)
    assert payload["liquidity"]["free_float"] == 12.0
    assert len(payload["liquidity"]["events"]) == 1
    assert payload["liquidity"]["event_note"] is not None


def test_votes_handle_short_series_without_crashing() -> None:
    # Fewer bars than the slow MA period: must degrade, not raise.
    report = s.analyze_signal([100.0, 101.0, 102.0, 101.5, 103.0])
    assert report.signal in {"buy", "sell", "hold"}
    assert len(report.votes) == 4
