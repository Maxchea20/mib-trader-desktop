"""Extension reference must be the trigger-event origin, not the nearest key_level.

Run with: python3 -m pytest tests/test_extension_origin.py tests/test_brain_v2.py -v -n 0
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain import scoring
from src.brain import brain as brain_engine
from src.contract import AgentResult, LONG, SHORT, NEUTRAL


HTF_LONG = {"regime": "LONG", "regime_score": 25.0, "per_timeframe": {}}
MAX_EXT = 1.2


def A(agent, direction, conf, evidence=None, levels=None, valid=True):
    return AgentResult(
        agent=agent,
        direction=direction,
        confidence=conf,
        strength=conf,
        evidence=list(evidence or []),
        key_levels=list(levels or []),
        timeframe="15m",
        valid=valid,
    )


def test_1_structure_origin_not_nearby_sr():
    """Trigger origin 80000, price 90000, unrelated S/R 89950 → measure from 80000."""
    agents = [
        A("market_structure", LONG, 80,
          ["BOS recovery", "State: TRIGGERED"],
          [{"label": "BOS", "price": 80000.0, "type": "support"}]),
        A("support_resistance", LONG, 70,
          ["nearby shelf"],
          [{"label": "S", "price": 89950.0, "type": "support"}]),
        A("trend", LONG, 75, ["uptrend"]),
    ]
    ext = scoring.extension_timing(agents, 90000.0, LONG, atr_value=500.0, max_extension_pct=MAX_EXT)
    assert ext["reference_price"] == 80000.0, ext
    assert ext["reference_agent"] == "market_structure", ext
    assert abs(ext["extension_pct"] - 12.5) < 0.01, ext
    assert ext["state"] == "EXTENDED", ext


def test_2_breakout_origin_not_unrelated_nearby_level():
    """Breakout trigger origin wins over an unrelated nearby level on another agent."""
    agents = [
        A("breakout", LONG, 82,
          ["Bullish break", "State: CONTINUATION"],
          [{"label": "BREAKOUT", "price": 80100.0, "type": "break"}]),
        A("support_resistance", LONG, 70,
          ["incidental"],
          [{"label": "S", "price": 89940.0, "type": "support"}]),
        A("fibonacci", LONG, 60,
          ["nearby fib"],
          [{"label": "fib", "price": 89800.0, "type": "support"}]),
        A("trend", LONG, 70, ["up"]),
    ]
    ext = scoring.extension_timing(agents, 90000.0, LONG, atr_value=400.0, max_extension_pct=MAX_EXT)
    assert ext["reference_price"] == 80100.0, ext
    assert ext["reference_agent"] == "breakout", ext
    assert ext["state"] == "EXTENDED", ext
    assert ext["extension_pct"] > 10, ext


def test_3_trigger_without_origin_does_not_use_nearby_sr():
    """Trigger exists but publishes no origin — must NOT fall back to S/R 89950."""
    agents = [
        A("market_structure", LONG, 80, ["State: TRIGGERED"], []),
        A("support_resistance", LONG, 70,
          ["nearby shelf"],
          [{"label": "S", "price": 89950.0, "type": "support"}]),
        A("trend", LONG, 75, ["up"]),
    ]
    ext = scoring.extension_timing(agents, 90000.0, LONG, atr_value=500.0, max_extension_pct=MAX_EXT)
    assert ext["state"] == "UNKNOWN", ext
    assert ext["reference_price"] is None, ext
    assert ext["reference_agent"] is None, ext

    filled = list(agents) + [
        A("pattern", LONG, 70, ["State: CONFIRMED"]),
        A("momentum", LONG, 72, ["State: LONG_ACCELERATING"]),
        A("volume", LONG, 65, ["State: BULLISH_CONFIRMATION"]),
        A("fibonacci", LONG, 60, ["fib"]),
        A("fair_value_gap", LONG, 55, ["fvg"]),
        A("breakout", LONG, 55, ["watch"]),
        A("elliott_wave", SHORT, 40, ["diag"]),
    ]
    result = brain_engine.decide(filled, 90000.0, "15m", HTF_LONG, atr_value=500.0)
    assert result["extension_state"] == "UNKNOWN", result["extension_detail"]
    assert result["state"] == "WAIT", result
    assert result["decision_state"] == "WAIT_NO_EXTENSION_REF", result["decision_state"]
    assert "extension origin unknown" in result["blocking_reasons"]


def test_4_normal_small_extension_from_trigger_origin_remains_early():
    """Normal case: trigger origin just under price, no decoy levels."""
    origin = 89920.0
    price = 90000.0
    agents = [
        A("market_structure", LONG, 80,
          ["State: TRIGGERED"],
          [{"label": "BOS", "price": origin, "type": "support"}]),
        A("trend", LONG, 75, ["up"]),
    ]
    ext = scoring.extension_timing(agents, price, LONG, atr_value=200.0, max_extension_pct=MAX_EXT)
    expected = (price - origin) / origin * 100
    assert ext["reference_price"] == origin, ext
    assert abs(ext["extension_pct"] - round(expected, 2)) < 0.011, ext
    assert ext["state"] in ("EARLY", "TIMELY"), ext
    assert ext["state"] != "UNKNOWN"


def test_short_trigger_origin_not_nearby_resistance():
    """SHORT mirror: origin 92000, price 80000, unrelated resistance 80040."""
    agents = [
        A("market_structure", SHORT, 80,
          ["State: TRIGGERED"],
          [{"label": "BOS", "price": 92000.0, "type": "resistance"}]),
        A("support_resistance", SHORT, 70,
          ["nearby"],
          [{"label": "R", "price": 80040.0, "type": "resistance"}]),
    ]
    ext = scoring.extension_timing(agents, 80000.0, SHORT, atr_value=400.0, max_extension_pct=MAX_EXT)
    assert ext["reference_price"] == 92000.0, ext
    assert ext["reference_agent"] == "market_structure", ext
    assert ext["state"] == "EXTENDED", ext
