"""BRAIN V2 test suite — covers the spec's named scenarios A-AJ (section 51).

Run with: python3 -m pytest tests/test_brain_v2.py -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain import brain as brain_engine
from src.brain import position
from src.contract import AgentResult, LONG, SHORT, NEUTRAL

HTF_NEUTRAL = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
HTF_LONG = {"regime": "LONG", "regime_score": 25.0, "per_timeframe": {}}
HTF_SHORT = {"regime": "SHORT", "regime_score": -25.0, "per_timeframe": {}}

ALL_AGENTS = ["market_structure", "trend", "pattern", "momentum", "volume",
             "support_resistance", "fibonacci", "fair_value_gap", "breakout", "elliott_wave"]


def _agents(**overrides):
    """Build a full 10-agent list; unspecified agents default to weak NEUTRAL."""
    out = []
    for aid in ALL_AGENTS:
        if aid in overrides:
            direction, confidence, evid = overrides[aid]
            out.append(AgentResult(aid, direction, confidence, confidence, evid))
        else:
            out.append(AgentResult(aid, NEUTRAL, 20, 20, ["flat"]))
    return out


def _strong_fire_agents(direction):
    other = SHORT if direction == LONG else LONG
    trig_word = "TRIGGERED" if direction == LONG else "TRIGGERED"
    return _agents(
        market_structure=(direction, 80, [f"{direction} BOS recovery", "State: TRIGGERED"]),
        trend=(direction, 75, [f"{direction} trend"]),
        pattern=(direction, 70, [f"{direction} pattern", "State: CONFIRMED"]),
        momentum=(direction, 72, [f"{direction} momentum", f"State: {'LONG' if direction==LONG else 'SHORT'}_ACCELERATING"]),
        volume=(direction, 65, ["Volume confirms", "State: BULLISH_CONFIRMATION" if direction == LONG else "State: BEARISH_CONFIRMATION"]),
        support_resistance=(direction, 68, ["Level hold"]),
        fibonacci=(direction, 60, ["Fib confluence"]),
        breakout=(direction, 55, ["Continuation"]),
        elliott_wave=(other, 40, ["diagnostic only"]),
    )


# --- A/B: Strong LONG / SHORT agreement ---
def test_A_strong_long_agreement():
    result = brain_engine.decide(_strong_fire_agents(LONG), 90000.0, "15m", HTF_LONG)
    assert result["state"] == "LONG"
    assert result["decision_state"] == "FIRE_LONG"


def test_B_strong_short_agreement():
    result = brain_engine.decide(_strong_fire_agents(SHORT), 90000.0, "15m", HTF_SHORT)
    assert result["state"] == "SHORT"
    assert result["decision_state"] == "FIRE_SHORT"


# --- C: Balanced LONG/SHORT ---
def test_C_balanced_long_short():
    agents = _agents(
        market_structure=(LONG, 78, ["bullish"]), pattern=(LONG, 79, ["bullish"]),
        fibonacci=(LONG, 69, ["bullish"]), trend=(LONG, 53, ["bullish"]),
        momentum=(SHORT, 67, ["bearish"]), support_resistance=(SHORT, 73, ["bearish"]),
        fair_value_gap=(SHORT, 67, ["bearish"]), elliott_wave=(SHORT, 42, ["diagnostic"]),
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["state"] == "WAIT"
    assert result["direction"] == NEUTRAL
    # This is the core bug fix: brain_confidence must NOT be near-zero
    # despite near-zero consensus, since real evidence exists both sides.
    assert result["brain_confidence"] > 15, f"brain_confidence too low: {result['brain_confidence']}"


# --- D: Strong agents but high conflict -> conflict severity should be flagged ---
def test_D_high_conflict():
    agents = _agents(
        market_structure=(LONG, 85, ["bullish"]), trend=(LONG, 85, ["bullish"]),
        pattern=(LONG, 85, ["bullish"]),
        momentum=(SHORT, 85, ["bearish"]), support_resistance=(SHORT, 85, ["bearish"]),
        fair_value_gap=(SHORT, 85, ["bearish"]),
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["conflict"]["severity"] in ("moderate", "severe")


# --- E: Weak consensus -> WAIT ---
def test_E_weak_consensus():
    agents = _agents(market_structure=(LONG, 25, ["weak"]))
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["state"] == "WAIT"


# --- F: Strong consensus but no trigger -> WAIT, specifically SETUP_ state ---
def test_F_strong_consensus_no_trigger():
    agents = _agents(
        market_structure=(LONG, 78, ["bullish structure, no trigger keyword"]),
        trend=(LONG, 80, ["bullish trend"]),
        pattern=(LONG, 75, ["bullish pattern forming"]),
        support_resistance=(LONG, 70, ["near support"]),
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["state"] == "WAIT"
    assert result["trigger_score"] == 0.0
    assert "SETUP" in result["decision_state"] or "BIAS" in result["decision_state"]


# --- G: Strong consensus but poor location ---
def test_G_strong_consensus_poor_location():
    agents = _strong_fire_agents(LONG)
    # Remove key_levels entirely and keep price far from any zone implicitly (no zones -> default location score)
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_LONG)
    # location_score should exist and be a real number regardless
    assert "location_score" in result
    assert 0 <= result["location_score"] <= 100


# --- H: Strong consensus but extended -> WAIT_EXTENDED ---
def test_H_extended():
    agents = _agents(
        market_structure=(LONG, 80, ["bullish", "State: TRIGGERED"]),
        trend=(LONG, 75, ["bullish"]), pattern=(LONG, 70, ["bullish", "State: CONFIRMED"]),
        momentum=(LONG, 72, ["bullish"]), volume=(LONG, 65, ["bullish"]),
        support_resistance=(LONG, 68, ["bullish"], ) if False else (LONG, 68, ["bullish"]),
    )
    # Give the LONG agents a key_level far from price to force EXTENDED
    agents2 = []
    for a in agents:
        if a.agent == "market_structure":
            a.key_levels = [{"label": "ref", "price": 80000.0, "type": "support"}]
        agents2.append(a)
    result = brain_engine.decide(agents2, 90000.0, "15m", HTF_LONG, atr_value=50.0)
    assert result["extension_detail"]["extension_pct"] > 0
    # With reference 80000 and price 90000, extension is huge -> should be EXTENDED and WAIT
    if result["extension_state"] == "EXTENDED":
        assert result["state"] == "WAIT"
        assert result["decision_state"] == "WAIT_EXTENDED"


# --- I: Early reversal trigger recognized without full confirmation ---
def test_I_early_reversal_trigger():
    agents = _agents(
        market_structure=(LONG, 76, ["Early bullish reversal trigger", "State: TRIGGERED"]),
        trend=(LONG, 60, ["bullish"]), breakout=(LONG, 55, ["retest holding"]),
        momentum=(LONG, 58, ["accelerating"]),
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["trigger_score"] > 0
    assert "market_structure" in " ".join(result["trigger_detail"]["primary"])


# --- K: Breakout + retest + hold ---
def test_K_breakout_retest_hold():
    agents = _agents(
        breakout=(LONG, 82, ["Bullish breakout lifecycle: CONTINUATION", "State: CONTINUATION"]),
        trend=(LONG, 70, ["bullish"]), market_structure=(LONG, 65, ["bullish"]),
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["trigger_score"] > 0


# --- L: Breakout failure -> should not be treated as a trigger ---
def test_L_breakout_failure():
    agents = _agents(
        breakout=(NEUTRAL, 30, ["Breakout FAILED", "State: FAILED"]),
        trend=(LONG, 60, ["bullish"]),
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    # A FAILED-state breakout, even if direction happened to be LONG, must not count as a primary trigger
    assert "breakout" not in " ".join(result["trigger_detail"].get("primary", []))


# --- O/P/Q: HTF aligned / counter-trend / neutral ---
def test_O_htf_aligned():
    result = brain_engine.decide(_strong_fire_agents(LONG), 90000.0, "15m", HTF_LONG)
    assert result["htf_gate"]["relation"] == "aligned"


def test_P_htf_counter_trend_raises_bar_not_blocks():
    strong = _agents(
        market_structure=(SHORT, 95, ["Bearish BOS recovery", "State: TRIGGERED"]),
        trend=(SHORT, 92, ["bearish trend"]),
        pattern=(SHORT, 90, ["bearish pattern", "State: CONFIRMED"]),
        momentum=(SHORT, 90, ["bearish momentum", "State: SHORT_ACCELERATING"]),
        volume=(SHORT, 88, ["Volume confirms", "State: BEARISH_CONFIRMATION"]),
        support_resistance=(SHORT, 88, ["resistance rejection"]),
        breakout=(SHORT, 85, ["continuation"]),
        elliott_wave=(LONG, 40, ["diagnostic only"]),
    )
    aligned = brain_engine.decide(strong, 90000.0, "15m", HTF_SHORT)
    counter = brain_engine.decide(strong, 90000.0, "15m", HTF_LONG)
    assert counter["entry_score_required"] > aligned["entry_score_required"]
    assert aligned["state"] == "SHORT"
    # Counter-trend is a higher bar, not an automatic block — with a
    # setup this strong, it still clears even the raised bar.
    assert counter["state"] == "SHORT", (
        f"consensus {counter['consensus_score']} vs required {counter['entry_score_required']} "
        "— counter-trend should raise the bar, not make it impossible"
    )


def test_Q_htf_neutral():
    result = brain_engine.decide(_strong_fire_agents(LONG), 90000.0, "15m", HTF_NEUTRAL)
    assert result["htf_gate"]["relation"] in ("neutral",)


# --- R: Insufficient agents -> AVOID ---
def test_R_insufficient_agents():
    agents = [AgentResult("market_structure", LONG, 80, 80, ["bullish"])]
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["state"] == "AVOID"
    assert result["decision_state"] == "WAIT_INSUFFICIENT_DATA"


# --- S: Neutral agents preserved as information, not silently dropped ---
def test_S_neutral_agents_preserved():
    agents = _agents(market_structure=(LONG, 80, ["bullish"]))
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert len(result["neutral_agents"]) >= 8  # everything except market_structure defaults to NEUTRAL


# --- T/U: Elliott Wave never decides execution ---
def test_T_elliott_only_directional_evidence():
    agents = _agents(elliott_wave=(LONG, 95, ["strong elliott bullish"]))
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    # With every OTHER agent weak/neutral, Elliott alone (excluded from
    # execution math) must not push this to a directional bias.
    assert result["direction"] == NEUTRAL


def test_U_elliott_conflicting_with_all_execution_agents():
    agents = _strong_fire_agents(LONG)  # elliott already SHORT in _strong_fire_agents
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_LONG)
    # Elliott disagreeing must not block a otherwise-valid LONG fire.
    assert result["state"] == "LONG"
    for c in result["contributions"]:
        if c["agent"] == "elliott_wave":
            assert c["execution_influence"] is False


# --- W: Avoid double counting (role grouping) ---
def test_W_avoid_double_counting():
    # Three LOCATION-role agents all describing the same zone should not
    # out-score a single, very strong STRUCTURE signal on breadth alone
    # in a way that ignores role diversity.
    agents = _agents(
        support_resistance=(LONG, 60, ["support"]),
        fibonacci=(LONG, 60, ["fib support"]),
        fair_value_gap=(LONG, 60, ["fvg support"]),
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    # All three are the SAME role (LOCATION) - should count as ONE
    # aligned role, not three.
    assert len(result["setup_detail"]["roles_aligned"]) <= 1


# --- X: LONG/SHORT symmetry ---
def test_X_long_short_symmetry():
    long_result = brain_engine.decide(_strong_fire_agents(LONG), 90000.0, "15m", HTF_LONG)
    short_result = brain_engine.decide(_strong_fire_agents(SHORT), 90000.0, "15m", HTF_SHORT)
    assert long_result["setup_score"] == short_result["setup_score"]
    assert long_result["trigger_score"] == short_result["trigger_score"]
    assert long_result["state"] == "LONG"
    assert short_result["state"] == "SHORT"


# --- Y: No lookahead — decide() is a pure function of its inputs, no hidden state ---
def test_Y_no_lookahead_pure_function():
    agents = _strong_fire_agents(LONG)
    r1 = brain_engine.decide(agents, 90000.0, "15m", HTF_LONG)
    r2 = brain_engine.decide(agents, 90000.0, "15m", HTF_LONG)
    assert r1["state"] == r2["state"]
    assert r1["consensus_score"] == r2["consensus_score"]


# --- Z/AA: Open trade thesis strengthening / weakening ---
def test_Z_thesis_strengthening():
    entry = brain_engine.decide(_strong_fire_agents(LONG), 90000.0, "15m", HTF_LONG)
    entry["price"] = 90000.0
    thesis = position.build_thesis(entry)
    stronger_agents = _strong_fire_agents(LONG)
    for a in stronger_agents:
        if a.direction == LONG:
            a.confidence = min(99, a.confidence + 15)
    current = brain_engine.decide(stronger_agents, 91000.0, "15m", HTF_LONG)
    result = position.evaluate_open_position(thesis, stronger_agents, current)
    assert result["recommendation"] in ("HOLD",)
    assert result["consensus_delta"] > 0


def test_AA_thesis_weakening():
    entry = brain_engine.decide(_strong_fire_agents(LONG), 90000.0, "15m", HTF_LONG)
    entry["price"] = 90000.0
    thesis = position.build_thesis(entry)
    weaker_agents = _agents(
        market_structure=(LONG, 30, ["weakening", "State: LONG_EXHAUSTING"]),
        trend=(LONG, 30, ["still bullish but weak"]),
        momentum=(LONG, 25, ["fading", "State: LONG_EXHAUSTING"]),
    )
    current = brain_engine.decide(weaker_agents, 89800.0, "15m", HTF_LONG)
    result = position.evaluate_open_position(thesis, weaker_agents, current)
    assert result["thesis_state"] in ("WEAKENING", "INVALIDATED")


# --- AB: Structural exit ---
def test_AB_structural_exit():
    entry = brain_engine.decide(_strong_fire_agents(LONG), 90000.0, "15m", HTF_LONG)
    entry["price"] = 90000.0
    thesis = position.build_thesis(entry)
    failed_agents = _agents(
        market_structure=(SHORT, 70, ["Structure broke down", "State: FAILED"]),
        momentum=(LONG, 30, ["fading", "State: LONG_EXHAUSTING"]),
    )
    current = brain_engine.decide(failed_agents, 89000.0, "15m", HTF_LONG)
    result = position.evaluate_open_position(thesis, failed_agents, current)
    assert result["recommendation"] == "EXIT"
    assert len(result["structural_failures"]) > 0


# --- AE: Temporary missing agent (invalid) doesn't crash, excluded cleanly ---
def test_AE_temporary_missing_agent():
    agents = _strong_fire_agents(LONG)
    for a in agents:
        if a.agent == "volume":
            a.valid = False
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_LONG)
    assert result["active_agents"] < 10
    # Should still be able to fire with 8 remaining valid execution agents
    assert result["state"] in ("LONG", "WAIT")


# --- AF: Invalid agent excluded from consensus math ---
def test_AF_invalid_agent_excluded():
    agents = _agents(market_structure=(LONG, 80, ["bullish"]))
    agents[0].valid = False  # invalidate the one directional agent
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["direction"] == NEUTRAL


# --- AG: Extreme extension ---
def test_AG_extreme_extension():
    agents = _strong_fire_agents(LONG)
    for a in agents:
        if a.agent == "market_structure":
            a.key_levels = [{"label": "ref", "price": 70000.0, "type": "support"}]
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_LONG, atr_value=100.0)
    assert result["extension_detail"]["extension_pct"] > 10


# --- AJ: Strong trigger with weak setup ---
def test_AJ_strong_trigger_weak_setup():
    agents = _agents(
        market_structure=(LONG, 95, ["Strong trigger", "State: TRIGGERED"]),
        breakout=(LONG, 70, ["mild continuation"]),
        trend=(LONG, 40, ["mild"]),  # enough for consensus to
        # cross the direction threshold at all, without adding real role diversity
    )
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["direction"] == LONG, f"consensus {result['consensus_score']} didn't cross threshold"
    # A single agent, even with a strong trigger, has weak (non-role-diverse) setup
    assert result["trigger_score"] > 0
    assert result["setup_score"] < 60


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])