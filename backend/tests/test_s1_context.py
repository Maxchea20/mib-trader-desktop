"""S1 15m setup stage: Trend -> Structure -> Breakout qualification -> Momentum confidence."""
from pathlib import Path
from types import SimpleNamespace as NS

from src.brain import s1_context as C


def _ev(et="CHoCH", ts=0, level=100.0, direction="BULLISH"):
    return NS(event_type=et, timestamp=ts, reference_price=level, price=level + 1, direction=direction)


def _bars(n, start_close=101.0, ts0=0):
    return [{"ts": ts0 + i * 900, "open": start_close, "high": start_close + 1, "low": start_close - 1,
             "close": start_close} for i in range(n)]


def test_trend_direction_reads_existing_trend_state():
    assert C.trend_direction(NS(valid=True, state="STRONG_BULL")) == "LONG"
    assert C.trend_direction(NS(valid=True, state="WEAK_BEAR")) == "SHORT"
    assert C.trend_direction(NS(valid=True, state="NEUTRAL")) is None
    assert C.trend_direction(NS(valid=False, state="BULL")) is None
    assert C.trend_direction(None) is None


def test_trend_context():
    assert C.trend_context("LONG", "LONG") == C.ALIGNED
    assert C.trend_context("SHORT", "LONG") == C.COUNTER
    assert C.trend_context(None, "SHORT") == C.NEUTRAL_CTX


def test_counter_trend_bos_is_not_a_thesis():
    r = C.qualify_break(_ev("BOS"), NS(flags={}), _bars(3), 101.0, "LONG", C.COUNTER)
    assert r and "counter-trend" in r
    assert C.qualify_break(_ev("CHoCH"), NS(flags={}), _bars(3), 101.0, "LONG", C.COUNTER) is None


def test_extended_bos_rejected_using_structure_flag():
    r = C.qualify_break(_ev("BOS"), NS(flags={"extended_bos": True}), _bars(3), 101.0, "LONG", C.ALIGNED)
    assert r and "extended" in r


def test_stale_break_rejected():
    form = _bars(C.BREAKOUT_MAX_LIFETIME_BARS + 3)
    r = C.qualify_break(_ev(ts=0), NS(flags={}), form, 101.0, "LONG", C.ALIGNED)
    assert r and "stale" in r


def test_failed_breakout_rejected_on_close_back_through_level():
    form = _bars(4)
    form[2]["close"] = 99.0  # after the break bar (ts 0), closed back under 100
    r = C.qualify_break(_ev(ts=0, level=100.0), NS(flags={}), form, 101.0, "LONG", C.ALIGNED)
    assert r and "FAILED" in r
    r = C.qualify_break(_ev(ts=0, level=100.0), NS(flags={}), _bars(4), 99.5, "LONG", C.ALIGNED)
    assert r and "FAILED" in r


def test_momentum_confidence_levels():
    assert C.momentum_confidence(NS(state="LONG_ACCELERATING"), "LONG") == C.HIGH
    assert C.momentum_confidence(NS(state="STRONG_SHORT"), "SHORT") == C.HIGH
    assert C.momentum_confidence(NS(state="SHORT_ACCELERATING"), "LONG") == C.LOW
    assert C.momentum_confidence(NS(state="LONG_EXHAUSTING"), "LONG") == C.LOW
    assert C.momentum_confidence(NS(state="LONG_DECELERATING"), "LONG") == C.MEDIUM
    assert C.momentum_confidence(NS(state="NEUTRAL"), "LONG") == C.MEDIUM
    assert C.momentum_confidence(None, "LONG") == C.MEDIUM


def test_momentum_is_confidence_not_a_blanket_filter():
    assert C.setup_allowed(C.ALIGNED, C.MEDIUM)  # non-accelerating momentum still passes with the trend
    assert C.setup_allowed(C.ALIGNED, C.HIGH)
    assert not C.setup_allowed(C.ALIGNED, C.LOW)
    assert C.setup_allowed(C.NEUTRAL_CTX, C.HIGH)
    assert not C.setup_allowed(C.NEUTRAL_CTX, C.MEDIUM)
    assert C.setup_allowed(C.COUNTER, C.HIGH)
    assert not C.setup_allowed(C.COUNTER, C.MEDIUM)


def test_evaluate_setup_uses_trend_structure_and_momentum_from_aux():
    aux = {"trend": NS(valid=True, state="BULL"), "mom": NS(state="LONG_DECELERATING")}
    got = C.evaluate_setup(_bars(5), _bars(5), _ev(), NS(flags={}), "LONG", 101.0, aux)
    assert got["ok"] and got["context"] == C.ALIGNED and got["confidence"] == C.MEDIUM
    aux = {"trend": NS(valid=True, state="NEUTRAL"), "mom": NS(state="LONG_DECELERATING")}
    got = C.evaluate_setup(_bars(5), _bars(5), _ev(), NS(flags={}), "LONG", 101.0, aux)
    assert not got["ok"] and got["qualified"]
    aux = {"trend": NS(valid=True, state="BEAR"), "mom": NS(state="STRONG_LONG")}
    got = C.evaluate_setup(_bars(5), _bars(5), _ev("BOS"), NS(flags={}), "LONG", 101.0, aux)
    assert not got["ok"] and not got["qualified"]


def test_s1_setup_stage_has_no_fvg_or_volume():
    code = Path("src/brain/s1_context.py").read_text(encoding="utf-8").split('"""', 2)[2]  # after the docstring
    for needle in ('"fvg"', '"vol"', '"sr"', "fair_value_gap", "volume.", "support_resistance"):
        assert needle not in code


def test_engine_gates_s1_window_on_setup():
    text = Path("src/brain/s1_engine.py").read_text(encoding="utf-8")
    assert "evaluate_setup(" in text
    assert 'if setup["ok"] and m5_after_close and m5_event_level(ev_s1) is not None:' in text
    assert "slot in (1, 2)" not in text  # no slot gating left in S1
    assert "last_15m_structure(candles_15m or [])" in text  # S1 thesis from CLOSED 15m candles
    assert 'fresh_m5_event_s1(events, side, ts5, st["consumed_s1"], inv)' in text


def test_closed_only_failed_check_ignores_live_price():
    form = _bars(4)
    assert C.qualify_break(_ev(ts=0, level=100.0), NS(flags={}), form, None, "LONG", C.ALIGNED) is None
    form[3]["close"] = 99.0  # a CLOSED 15m back under the level still fails it
    r = C.qualify_break(_ev(ts=0, level=100.0), NS(flags={}), form, None, "LONG", C.ALIGNED)
    assert r and "FAILED" in r


def test_not_ready_shape():
    got = C.not_ready("no closed 15m BOS/CHoCH")
    assert got["ok"] is False and got["context"] is None and got["reason"]
