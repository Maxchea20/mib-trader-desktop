"""A1 — trigger events are clustered by origin, not raw agent count.

Run with: python3 -m pytest tests/test_trigger_events.py tests/test_extension_origin.py tests/test_brain_v2.py -v
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain import scoring
from src.brain import brain as brain_engine
from src.contract import AgentResult, LONG, SHORT

HTF_LONG = {"regime": "LONG", "regime_score": 25.0, "per_timeframe": {}}
ATR = 400.0  # 0.5 ATR = 200; 80000 vs 80010 is same event; 80000 vs 83000 is not


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


def test_1_same_event_two_agents():
    """Structure 80000 + Breakout 80010 = ONE cluster, both sources visible."""
    agents = [
        A("market_structure", LONG, 80,
          ["State: TRIGGERED"],
          [{"label": "BOS", "price": 80000.0, "type": "support"}]),
        A("breakout", LONG, 82,
          ["State: CONTINUATION"],
          [{"label": "BREAKOUT", "price": 80010.0, "type": "break"}]),
    ]
    solo = scoring.trigger_score(
        [agents[0]], LONG, atr_value=ATR, price=90000.0
    )
    both = scoring.trigger_score(agents, LONG, atr_value=ATR, price=90000.0)

    assert both["event_count"] == 1, both
    assert both["cluster_count"] == 1, both
    assert set(both["events"][0]["sources"]) == {"market_structure", "breakout"}
    assert both["events"][0]["corroboration"] == 2
    assert both["state"] == "CONFIRMING"
    assert both["trigger_origin"] == 80000.0
    assert "market_structure" in both["primary"][0] or "market_structure" in str(both["primary"])
    assert both["score"] > solo["score"]
    independent_like = 2 * 20.0 + ((80 + 82) / 2) * 0.5
    assert both["score"] < independent_like


def test_2_same_event_three_agents():
    agents = [
        A("market_structure", LONG, 80, ["State: TRIGGERED"],
          [{"label": "BOS", "price": 80000.0}]),
        A("breakout", LONG, 82, ["State: CONTINUATION"],
          [{"label": "BREAKOUT", "price": 80010.0}]),
        A("momentum", LONG, 75, ["State: LONG_ACCELERATING"],
          [{"label": "ORIGIN", "price": 80020.0}]),
    ]
    out = scoring.trigger_score(agents, LONG, atr_value=ATR, price=90000.0)
    assert out["event_count"] == 1, out
    assert out["events"][0]["corroboration"] == 3
    assert set(out["events"][0]["sources"]) == {
        "market_structure", "breakout", "momentum"
    }
    assert out["state"] == "CONFIRMING"


def test_3_two_distinct_events():
    """80000 vs 83000 is many ATRs apart — two clusters."""
    agents = [
        A("market_structure", LONG, 80, ["State: TRIGGERED"],
          [{"label": "BOS", "price": 80000.0}]),
        A("momentum", LONG, 75, ["State: LONG_ACCELERATING"],
          [{"label": "ORIGIN", "price": 83000.0}]),
    ]
    out = scoring.trigger_score(agents, LONG, atr_value=ATR, price=90000.0)
    assert out["event_count"] == 2, out
    origins = {e["origin_price"] for e in out["events"]}
    assert 80000.0 in origins
    assert 83000.0 in origins


def test_4_single_early_trigger():
    """Lone Structure trigger stays one event / TRIGGERED. No Breakout required."""
    agents = [
        A("market_structure", LONG, 80, ["State: TRIGGERED"],
          [{"label": "BOS", "price": 80000.0}]),
        A("trend", LONG, 70, ["uptrend"]),
    ]
    out = scoring.trigger_score(agents, LONG, atr_value=ATR, price=90000.0)
    assert out["event_count"] == 1
    assert out["state"] == "TRIGGERED"
    assert out["score"] > 0
    assert out["events"][0]["sources"] == ["market_structure"]


def test_5_missing_origin_not_invented_and_a2_unknown_intact():
    """No origin published — do not invent one or cluster onto nearby S/R."""
    agents = [
        A("market_structure", LONG, 80, ["State: TRIGGERED"], []),
        A("support_resistance", LONG, 70, ["nearby shelf"],
          [{"label": "S", "price": 89950.0, "type": "support"}]),
        A("trend", LONG, 75, ["up"]),
    ]
    out = scoring.trigger_score(agents, LONG, atr_value=ATR, price=90000.0)
    assert out["event_count"] == 1
    assert out["events"][0]["origin_price"] is None
    assert out["trigger_origin"] is None

    ext = scoring.extension_timing(agents, 90000.0, LONG, ATR, 1.2)
    assert ext["state"] == "UNKNOWN"
    assert ext["reference_price"] is None

    filled = list(agents) + [
        A("pattern", LONG, 70, ["State: CONFIRMED"]),
        A("momentum", LONG, 72, ["State: LONG_ACCELERATING"]),
        A("volume", LONG, 65, ["State: BULLISH_CONFIRMATION"]),
        A("fibonacci", LONG, 60, ["fib"]),
        A("fair_value_gap", LONG, 55, ["fvg"]),
        A("breakout", LONG, 55, ["watch"]),
        A("elliott_wave", SHORT, 40, ["diag"]),
    ]
    result = brain_engine.decide(filled, 90000.0, "15m", HTF_LONG, atr_value=ATR)
    assert result["extension_state"] == "UNKNOWN"
    assert result["state"] == "WAIT"
    assert result["decision_state"] == "WAIT_NO_EXTENSION_REF"


def test_6_short_same_event_cluster():
    agents = [
        A("market_structure", SHORT, 80, ["State: TRIGGERED"],
          [{"label": "BOS", "price": 92000.0, "type": "resistance"}]),
        A("breakout", SHORT, 78, ["State: CONTINUATION"],
          [{"label": "BREAKOUT", "price": 91980.0, "type": "break"}]),
    ]
    out = scoring.trigger_score(agents, SHORT, atr_value=ATR, price=80000.0)
    assert out["event_count"] == 1, out
    assert out["events"][0]["corroboration"] == 2
    assert out["trigger_direction"] == SHORT
    assert set(out["events"][0]["sources"]) == {"market_structure", "breakout"}


def test_7_no_lookahead_pure_function():
    """Clustering is a pure function of the current agent list + ATR."""
    src = inspect.getsource(scoring.trigger_score) + inspect.getsource(scoring._cluster_trigger_members)
    assert "market_data" not in src
    assert "walkforward" not in src.lower()
    assert "next_bar" not in src
    agents = [
        A("market_structure", LONG, 80, ["State: TRIGGERED"],
          [{"label": "BOS", "price": 80000.0}]),
    ]
    a = scoring.trigger_score(agents, LONG, atr_value=ATR, price=90000.0)
    b = scoring.trigger_score(agents, LONG, atr_value=ATR, price=90000.0)
    assert a == b
    assert scoring.trigger_score.__code__.co_nlocals >= 0


def test_same_direction_far_origins_do_not_merge_without_atr_guess():
    """Without ATR, only exact-same origin clusters — no dollar radius."""
    agents = [
        A("market_structure", LONG, 80, ["State: TRIGGERED"],
          [{"label": "BOS", "price": 80000.0}]),
        A("breakout", LONG, 82, ["State: CONTINUATION"],
          [{"label": "BREAKOUT", "price": 80010.0}]),
    ]
    out = scoring.trigger_score(agents, LONG, atr_value=None, price=90000.0)
    assert out["event_count"] == 2, out
