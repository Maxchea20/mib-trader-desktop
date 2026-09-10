"""A3 / A3.1 — role-net consensus, role-mass conflict, scale-correct quality.

Run with: python3 -m pytest tests/test_role_net.py tests/test_trigger_events.py tests/test_extension_origin.py tests/test_brain_v2.py -v
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain import evidence as ev
from src.brain import brain as brain_engine
from src.brain import conflict as conflict_mod
from src.brain.scoring import EXCLUDE_FROM_EXECUTION
from src import settings
from src.contract import AgentResult, LONG, SHORT, NEUTRAL

HTF_NEUTRAL = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
W = settings.weights()


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


def _net(*agents):
    return ev.role_net_evidence(list(agents), W, exclude_agents=EXCLUDE_FROM_EXECUTION)


def test_1_correlated_long_agents_are_one_structure_role():
    """Market Structure + Trend = ONE STRUCTURE contribution, not two votes."""
    agents = [
        A("market_structure", LONG, 90, ["bullish structure"]),
        A("trend", LONG, 90, ["bullish trend"]),
    ]
    one = ev.role_net_evidence([agents[0]], W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    both = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)

    assert both["n_directional_roles"] == 1, both
    assert "STRUCTURE" in both["roles"]
    assert "TREND" not in both["roles"]
    assert both["roles"]["STRUCTURE"]["direction"] == LONG
    assert both["roles"]["STRUCTURE"]["corroboration"] == 2
    assert set(both["roles"]["STRUCTURE"]["agents"]) == {"market_structure", "trend"}
    assert one["n_directional_roles"] == 1
    assert both["n_long_roles"] == 1
    independent_like = one["consensus"] + one["consensus"]
    assert abs(both["consensus"]) < independent_like
    assert both["consensus"] > 0


def test_2_correlated_opposing_location_is_one_role():
    agents = [
        A("support_resistance", SHORT, 80, ["res"]),
        A("fibonacci", SHORT, 75, ["fib"]),
        A("fair_value_gap", SHORT, 78, ["fvg"]),
    ]
    out = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    assert out["n_directional_roles"] == 1
    assert out["n_short_roles"] == 1
    assert out["roles"]["LOCATION"]["direction"] == SHORT
    assert out["roles"]["LOCATION"]["corroboration"] == 3
    assert out["short_role_evidence"] > 0
    cf = conflict_mod.detect_conflict(agents, NEUTRAL)
    assert cf["n_opposing_roles"] == 0 or cf["opposing_roles"] in ([], ["LOCATION"])
    with_thesis = agents + [
        A("market_structure", LONG, 88, ["bos"]),
        A("trend", LONG, 86, ["up"]),
    ]
    cf2 = conflict_mod.detect_conflict(with_thesis, LONG)
    assert cf2["n_opposing_roles"] == 1
    assert cf2["opposing_roles"] == ["LOCATION"]
    assert cf2["severity"] != "veto"
    assert cf2["severity"] != "severe"


def test_3_strong_long_weak_opposition_not_severe():
    agents = [
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", LONG, 88, ["up"]),
        A("breakout", LONG, 86, ["bo"]),
        A("momentum", LONG, 84, ["mom"]),
        A("volume", LONG, 70, ["vol"]),
        A("support_resistance", SHORT, 55, ["weak shelf"]),
    ]
    net = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    assert net["n_long_roles"] >= 2
    # After Alt B, scores track confidence (~89 / ~83 / ~55) so the mean
    # sits just under 40. Directional dominance is the assertion, not the
    # old saturated 100/100 book.
    assert net["consensus"] > 30
    assert net["roles"]["STRUCTURE"]["score"] > net["roles"]["LOCATION"]["score"]
    assert net["roles"]["DRIVE"]["score"] > net["roles"]["LOCATION"]["score"]
    cf = conflict_mod.detect_conflict(agents, LONG)
    assert cf["severity"] not in ("severe", "veto")
    result = brain_engine.decide(agents + [
        A("pattern", NEUTRAL, 20, ["flat"]),
        A("fibonacci", NEUTRAL, 20, ["flat"]),
        A("fair_value_gap", NEUTRAL, 20, ["flat"]),
        A("elliott_wave", NEUTRAL, 20, ["diag"]),
    ], 90000.0, "15m", HTF_NEUTRAL)
    assert result["direction"] == LONG
    assert result["conflict"]["severity"] not in ("severe", "veto")


def test_4_multiple_independent_opposing_roles():
    agents = [
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", LONG, 88, ["up"]),
        A("breakout", LONG, 86, ["bo"]),
        A("momentum", LONG, 84, ["mom"]),
        A("support_resistance", SHORT, 85, ["res"]),
        A("fibonacci", SHORT, 82, ["fib"]),
        A("pattern", SHORT, 84, ["rej"]),
    ]
    net = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    assert net["n_long_roles"] >= 2
    assert net["n_short_roles"] >= 2
    cf = conflict_mod.detect_conflict(agents, LONG)
    assert cf["n_opposing_roles"] >= 2
    assert set(cf["opposing_roles"]) >= {"LOCATION", "PATTERN"}
    assert cf["severity"] in ("moderate", "high", "severe")
    assert cf["conflict"] is True


def test_5_neutral_agents_do_not_dilute():
    directional = [A("market_structure", LONG, 80, ["bos"])]
    with_neutrals = directional + [
        A("trend", NEUTRAL, 50, ["flat"]),
        A("breakout", NEUTRAL, 50, ["flat"]),
        A("momentum", NEUTRAL, 50, ["flat"]),
        A("volume", NEUTRAL, 50, ["flat"]),
        A("pattern", NEUTRAL, 50, ["flat"]),
        A("support_resistance", NEUTRAL, 50, ["flat"]),
        A("fibonacci", NEUTRAL, 50, ["flat"]),
        A("fair_value_gap", NEUTRAL, 50, ["flat"]),
        A("elliott_wave", NEUTRAL, 50, ["diag"]),
    ]
    solo = ev.role_net_evidence(directional, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    packed = ev.role_net_evidence(with_neutrals, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    assert solo["consensus"] == packed["consensus"]
    assert packed["n_directional_roles"] == 1


def test_6_neutral_bias_still_computes_both_sides():
    agents = [
        A("market_structure", LONG, 70, ["bos"]),
        A("trend", LONG, 68, ["up"]),
        A("support_resistance", SHORT, 72, ["res"]),
        A("fibonacci", SHORT, 70, ["fib"]),
        A("pattern", NEUTRAL, 20, ["flat"]),
        A("breakout", NEUTRAL, 20, ["flat"]),
        A("momentum", NEUTRAL, 20, ["flat"]),
        A("volume", NEUTRAL, 20, ["flat"]),
        A("fair_value_gap", NEUTRAL, 20, ["flat"]),
        A("elliott_wave", NEUTRAL, 20, ["diag"]),
    ]
    result = brain_engine.decide(agents, 90000.0, "15m", HTF_NEUTRAL)
    assert result["long_role_evidence"] > 0
    assert result["short_role_evidence"] > 0
    assert "STRUCTURE" in result["role_consensus"]
    assert "LOCATION" in result["role_consensus"]
    assert result["role_consensus"]["STRUCTURE"]["direction"] == LONG
    assert result["role_consensus"]["LOCATION"]["direction"] == SHORT
    cf = result["conflict"]
    assert "opposing_ratio" in cf
    assert cf["n_opposing_roles"] >= 1


def test_7_headcount_does_not_create_severe():
    """Correlated LONG drive vs correlated SHORT location.
    Two agents at conf>=70 must not independently mint severe conflict."""
    agents = [
        A("breakout", LONG, 82, ["bo"]),
        A("momentum", LONG, 80, ["mom"]),
        A("volume", LONG, 78, ["vol"]),
        A("support_resistance", SHORT, 80, ["res"]),
        A("fibonacci", SHORT, 75, ["fib"]),
    ]
    net = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    assert net["n_long_roles"] == 1
    assert net["n_short_roles"] == 1
    cf = conflict_mod.detect_conflict(agents, LONG)
    assert cf["n_opposing_roles"] == 1
    assert len(cf["opposing_agents"]) >= 2
    assert cf["severity"] != "severe"
    assert cf["severity"] != "veto"


def test_no_lookahead_pure_function():
    src = inspect.getsource(ev.role_net_evidence) + inspect.getsource(conflict_mod.detect_conflict)
    assert "market_data" not in src
    assert "walkforward" not in src.lower()
    assert "next_bar" not in src
    agents = [A("market_structure", LONG, 80, ["bos"])]
    a = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    b = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    assert a == b


def test_elliott_excluded_from_role_net():
    agents = [A("elliott_wave", LONG, 99, ["impulse"])]
    net = ev.role_net_evidence(agents, W, exclude_agents=EXCLUDE_FROM_EXECUTION)
    assert net["n_directional_roles"] == 0
    assert net["consensus"] == 0.0


# --- A3.1 scale-correct quality ---------------------------------------------

def test_a31_strong_structure_does_not_saturate():
    """Structure 90 + Trend 94 is high, not clamped to 100."""
    net = _net(
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", LONG, 94, ["up"]),
    )
    score = net["roles"]["STRUCTURE"]["score"]
    assert net["roles"]["STRUCTURE"]["purity"] == 1.0
    assert 88.0 <= score <= 94.0
    assert score == 91.3


def test_a31_medium_structure_materially_lower():
    """Structure 65 + Trend 60 must not look like a 93/100 role."""
    strong = _net(
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", LONG, 94, ["up"]),
    )["roles"]["STRUCTURE"]["score"]
    medium = _net(
        A("market_structure", LONG, 65, ["bos"]),
        A("trend", LONG, 60, ["up"]),
    )["roles"]["STRUCTURE"]["score"]
    assert medium == 63.4
    assert strong - medium >= 20.0


def test_a31_strong_drive_high_not_100():
    net = _net(
        A("breakout", LONG, 86, ["bo"]),
        A("momentum", LONG, 82, ["mom"]),
        A("volume", LONG, 70, ["vol"]),
    )
    score = net["roles"]["DRIVE"]["score"]
    assert score == 81.6
    assert score < 100.0


def test_a31_medium_drive_materially_lower():
    strong = _net(
        A("breakout", LONG, 86, ["bo"]),
        A("momentum", LONG, 82, ["mom"]),
        A("volume", LONG, 70, ["vol"]),
    )["roles"]["DRIVE"]["score"]
    medium = _net(
        A("breakout", LONG, 60, ["bo"]),
        A("momentum", LONG, 55, ["mom"]),
        A("volume", LONG, 50, ["vol"]),
    )["roles"]["DRIVE"]["score"]
    assert medium == 55.8
    assert strong - medium >= 20.0


def test_a31_single_agent_preserves_confidence():
    weak = _net(A("pattern", LONG, 55, ["pat"]))
    strong = _net(A("pattern", LONG, 88, ["pat"]))
    assert weak["roles"]["PATTERN"]["score"] == 55.0
    assert strong["roles"]["PATTERN"]["score"] == 88.0


def test_a31_internal_disagreement_cuts_score():
    net = _net(
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", SHORT, 70, ["down"]),
    )
    rec = net["roles"]["STRUCTURE"]
    assert rec["direction"] == LONG
    assert rec["purity"] < 0.25
    assert rec["score"] == 14.5
    assert rec["quality"] == 90.0


def test_a31_neutrals_do_not_change_role_score():
    base = _net(
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", LONG, 94, ["up"]),
    )
    packed = _net(
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", LONG, 94, ["up"]),
        A("pattern", NEUTRAL, 80, ["flat"]),
        A("breakout", NEUTRAL, 80, ["flat"]),
        A("momentum", NEUTRAL, 80, ["flat"]),
        A("volume", NEUTRAL, 80, ["flat"]),
        A("support_resistance", NEUTRAL, 80, ["flat"]),
        A("fibonacci", NEUTRAL, 80, ["flat"]),
        A("fair_value_gap", NEUTRAL, 80, ["flat"]),
        A("elliott_wave", LONG, 99, ["diag"]),
    )
    assert packed["roles"]["STRUCTURE"]["score"] == base["roles"]["STRUCTURE"]["score"]
    assert packed["n_directional_roles"] == 1
    assert packed["consensus"] == base["consensus"]


def test_a31_cross_role_mean_unchanged():
    """P3 is out of scope: consensus remains the unweighted mean of signed scores."""
    src = inspect.getsource(ev.role_net_evidence)
    assert "sum(c[\"signed\"] for c in contributions) / n" in src
    net = _net(
        A("market_structure", LONG, 90, ["bos"]),
        A("trend", LONG, 94, ["up"]),
        A("pattern", SHORT, 88, ["rej"]),
    )
    s = net["roles"]["STRUCTURE"]["signed"]
    p = net["roles"]["PATTERN"]["signed"]
    assert net["consensus"] == round((s + p) / 2, 1)
