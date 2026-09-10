"""A4 — honest decision explanation."""
import os, sys, inspect, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.brain import brain as brain_engine
from src.brain import explanation as expl
from src.contract import AgentResult, LONG, SHORT, NEUTRAL

HTF_NEUTRAL = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
HTF_LONG = {"regime": "LONG", "regime_score": 25.0, "per_timeframe": {}}
HTF_SHORT = {"regime": "SHORT", "regime_score": -25.0, "per_timeframe": {}}
ATR = 400.0
ALL = ["market_structure", "trend", "pattern", "momentum", "volume",
       "support_resistance", "fibonacci", "fair_value_gap", "breakout", "elliott_wave"]

def A(agent, direction, conf, evidence=None, levels=None, valid=True):
    return AgentResult(agent=agent, direction=direction, confidence=conf, strength=conf,
        evidence=list(evidence or []), key_levels=list(levels or []), timeframe="15m", valid=valid)

def _fill(overrides):
    out = []
    for aid in ALL:
        out.append(overrides[aid] if aid in overrides else A(aid, NEUTRAL, 20, ["flat"]))
    return out

def _strong(direction):
    other = SHORT if direction == LONG else LONG
    origin = 80000.0 if direction == LONG else 92000.0
    side = "support" if direction == LONG else "resistance"
    return _fill({
        "market_structure": A("market_structure", direction, 88, [f"{direction} BOS", "State: TRIGGERED"], [{"label": "BOS", "price": origin, "type": side}]),
        "trend": A("trend", direction, 84, [f"{direction} trend"]),
        "pattern": A("pattern", direction, 80, [f"{direction} pattern", "State: CONFIRMED"]),
        "momentum": A("momentum", direction, 82, [f"{direction} momentum", f"State: {direction}_ACCELERATING"]),
        "volume": A("volume", direction, 75, ["Volume confirms", "State: BULLISH_CONFIRMATION" if direction == LONG else "State: BEARISH_CONFIRMATION"]),
        "support_resistance": A("support_resistance", direction, 70, ["level hold"]),
        "fibonacci": A("fibonacci", direction, 68, ["fib"]),
        "breakout": A("breakout", direction, 78, ["State: CONTINUATION"], [{"label": "BREAKOUT", "price": origin + (10 if direction == LONG else -10), "type": "break"}]),
        "elliott_wave": A("elliott_wave", other, 40, ["diag"]),
    })

def test_1_fire_long_explanation():
    r = brain_engine.decide(_strong(LONG), 80400.0, "15m", HTF_LONG, atr_value=ATR)
    assert r["decision_state"] == "FIRE_LONG", r["decision_state"]
    e = r["explanation"]
    assert e["explanation_version"] == "BRAIN_EXPLANATION_V1"
    assert e["final"]["direction"] == LONG
    assert e["final"]["consensus_score"] == r["consensus_score"]
    assert e["why_fire"] and e["why_wait"] is None and e["blocking"]["primary"] is None
    blob = json.dumps(e).lower()
    assert "agents agree" not in blob and "of 9 agents" not in blob

def test_2_fire_short_explanation():
    r = brain_engine.decide(_strong(SHORT), 91600.0, "15m", HTF_SHORT, atr_value=ATR)
    assert r["decision_state"] == "FIRE_SHORT"
    e = r["explanation"]
    assert e["why_fire"] and e["final"]["direction"] == SHORT and e["trigger"]["event_count"] >= 1

def test_3_wait_consensus_names_threshold():
    agents = _fill({"market_structure": A("market_structure", LONG, 70, ["mild bos"]), "trend": A("trend", LONG, 60, ["up"]), "support_resistance": A("support_resistance", SHORT, 68, ["res"]), "fibonacci": A("fibonacci", SHORT, 62, ["fib"])})
    r = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL, atr_value=ATR)
    assert r["state"] == "WAIT"
    e = r["explanation"]
    joined = " ".join(e["why_wait"]).lower()
    assert "consensus" in joined or "neutral" in joined or "blocker" in joined
    assert "of 9 agents" not in json.dumps(e).lower()

def test_4_wait_trigger_does_not_blame_setup():
    agents = _fill({"market_structure": A("market_structure", LONG, 85, ["bullish structure, no trigger keyword"]), "trend": A("trend", LONG, 82, ["bullish trend"]), "pattern": A("pattern", LONG, 78, ["forming pattern"]), "support_resistance": A("support_resistance", LONG, 74, ["near support"]), "momentum": A("momentum", LONG, 70, ["up"]), "volume": A("volume", LONG, 68, ["ok"])})
    r = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL, atr_value=ATR)
    assert r["state"] == "WAIT"
    e = r["explanation"]
    if r["decision_state"].startswith("SETUP_"):
        assert e["blocking"]["primary"] == "no valid trigger event"
        assert "trigger" in " ".join(e["why_wait"]).lower()
        assert "setup is the blocker" not in " ".join(e["why_wait"]).lower()
    else:
        assert r["decision_state"].startswith("BIAS_") or r["decision_state"] == "NEUTRAL"

def test_5_unknown_extension_honest():
    agents = _fill({
        "market_structure": A("market_structure", LONG, 88, ["State: TRIGGERED"], []),
        "trend": A("trend", LONG, 80, ["up"]),
        "pattern": A("pattern", LONG, 78, ["State: CONFIRMED"]),
        "momentum": A("momentum", LONG, 76, ["State: LONG_ACCELERATING"]),
        "volume": A("volume", LONG, 70, ["State: BULLISH_CONFIRMATION"]),
        "breakout": A("breakout", LONG, 70, ["State: CONTINUATION"], []),
        "support_resistance": A("support_resistance", LONG, 68, ["shelf"], [{"label": "S", "price": 89950.0, "type": "support"}]),
        "fibonacci": A("fibonacci", LONG, 60, ["fib"]),
        "fair_value_gap": A("fair_value_gap", LONG, 55, ["fvg"]),
        "elliott_wave": A("elliott_wave", SHORT, 40, ["diag"]),
    })
    r = brain_engine.decide(agents, 90000.0, "15m", HTF_LONG, atr_value=ATR)
    assert r["extension_state"] == "UNKNOWN" and r["decision_state"] == "WAIT_NO_EXTENSION_REF"
    e = r["explanation"]
    assert e["location_timing"]["extension_origin_unknown"] is True
    assert e["location_timing"]["extension_origin"] == "UNKNOWN"
    assert "UNKNOWN" in " ".join(e["why_wait"]).upper()
    assert "89950" not in json.dumps(e["location_timing"])
    assert any(ev["origin"] == "UNKNOWN" for ev in e["trigger"]["events"])

def test_6_structure_recovery_not_a_contradiction():
    agents = _fill({"market_structure": A("market_structure", SHORT, 72, ["Higher High + Higher Low → bullish sequence developing", "Latest event: bearish BOS still active", "State: RECOVERY"], [{"label": "BOS", "price": 92000.0, "type": "resistance"}]), "trend": A("trend", SHORT, 48, ["still down"])})
    r = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL, atr_value=ATR)
    st = r["explanation"]["market_state"]["structure"]
    assert st["direction"] == SHORT and st["reversal_confirmed"] is False
    stmts = " ".join(r["explanation"]["market_state"]["statements"]).lower()
    assert ("short" in stmts or "bearish" in stmts) and ("developing" in stmts or "recover" in stmts)

def test_7_role_conflict_not_headcount():
    agents = _fill({"market_structure": A("market_structure", LONG, 90, ["bos"]), "trend": A("trend", LONG, 88, ["up"]), "breakout": A("breakout", LONG, 86, ["bo"]), "momentum": A("momentum", LONG, 84, ["mom"]), "support_resistance": A("support_resistance", SHORT, 85, ["res"]), "fibonacci": A("fibonacci", SHORT, 82, ["fib"]), "pattern": A("pattern", SHORT, 84, ["rej"])})
    e = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL, atr_value=ATR)["explanation"]
    roles_txt = " ".join(e["directional_thesis"]["roles"])
    assert "STRUCTURE" in roles_txt
    assert "LOCATION" in roles_txt or "PATTERN" in roles_txt
    assert "of 9 agents" not in json.dumps(e).lower()

def test_8_neutrals_not_directional_support():
    e = brain_engine.decide(_fill({"market_structure": A("market_structure", LONG, 80, ["bos"])}), 90000.0, "15m", HTF_NEUTRAL)["explanation"]
    assert "PATTERN" not in e["directional_thesis"]["supporting_roles"]
    assert "LOCATION" not in e["directional_thesis"]["supporting_roles"]

def test_explanation_is_json_serializable_for_restart():
    r = brain_engine.decide(_strong(LONG), 80400.0, "15m", HTF_LONG, atr_value=ATR)
    restored = json.loads(json.dumps(r["explanation"]))
    assert restored["explanation_version"] == "BRAIN_EXPLANATION_V1"

def test_no_lookahead_and_no_trading_logic_in_explanation():
    src = inspect.getsource(expl.build_explanation)
    assert "walkforward" not in src.lower() and "next_bar" not in src and "market_data" not in src

def test_existing_decision_unchanged_by_explanation():
    r = brain_engine.decide(_strong(LONG), 80400.0, "15m", HTF_LONG, atr_value=ATR)
    assert r["state"] == "LONG" and r["decision_state"] == "FIRE_LONG"
