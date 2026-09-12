"""Regression tests for the 2026-09-12 audit's Step 1 fix:
market_structure was previously excluded from
CONFLUENCE["level_agents"] (config.py), so its published Swing
High/Low key_levels could never form a confluence zone or feed
location_score / confluence_bonus, despite market_structure being the
highest-weighted agent in the system.

These tests prove:
  1. A market_structure key_level now clusters into a confluence zone
     together with another level agent's nearby level.
  2. That zone flows through to Brain's confluence_bonus and
     location_score (end-to-end, not just the isolated function).
  3. A lone market_structure level (no corroborating agent) still does
     NOT form a zone on its own -- confluence still requires >= 2
     agents, so the fix adds market_structure as an eligible
     participant without changing the zone-formation rule itself.

Run with: python3 -m pytest tests/test_confluence_market_structure.py -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain.confluence import find_confluence_zones
from src.brain import brain as brain_engine
from src.contract import AgentResult, LONG, SHORT, NEUTRAL
from src import settings

HTF_NEUTRAL = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}

PRICE = 90000.0


def _agent(agent, direction, confidence, key_levels=None, evidence=None):
    return AgentResult(
        agent=agent, direction=direction, confidence=confidence,
        strength=confidence, evidence=evidence or [], key_levels=key_levels or [],
    )


def test_market_structure_is_an_eligible_confluence_level_agent():
    """Config-level regression: market_structure must be present in the
    live confluence level_agents list, or every test below would pass
    for the wrong reason (find_confluence_zones would just silently
    skip it, same as before the fix)."""
    assert "market_structure" in settings.confluence()["level_agents"]


def test_market_structure_level_joins_confluence_zone_with_fibonacci():
    """A Swing High from market_structure and a nearby Fibonacci level
    should cluster into ONE zone crediting both agents -- this was
    impossible before the fix (market_structure's level was invisible
    to find_confluence_zones regardless of price proximity)."""
    agents = [
        _agent("market_structure", LONG, 80,
               key_levels=[{"label": "Swing High", "price": 90100.0, "type": "resistance"}]),
        _agent("fibonacci", LONG, 60,
               key_levels=[{"label": "Fib 0.618", "price": 90120.0, "type": "resistance"}]),
    ]
    zones = find_confluence_zones(agents, PRICE)
    assert len(zones) == 1
    zone = zones[0]
    assert "market_structure" in zone["agents"]
    assert "fibonacci" in zone["agents"]
    assert zone["count"] == 2
    assert zone["bonus"] > 0


def test_lone_market_structure_level_still_forms_no_zone():
    """Confluence still requires >=2 corroborating agents -- adding
    market_structure as an eligible participant must not relax that
    rule. A single market_structure level with no other nearby agent
    level should produce zero zones, same as any other lone agent
    would have before this fix."""
    agents = [
        _agent("market_structure", LONG, 80,
               key_levels=[{"label": "Swing High", "price": 90100.0, "type": "resistance"}]),
    ]
    zones = find_confluence_zones(agents, PRICE)
    assert zones == []


def test_confluence_bonus_and_location_score_reflect_market_structure_level():
    """End-to-end through Brain.decide(): a market_structure + S/R
    confluence zone near price should produce a positive
    confluence_bonus and a location_score that reflects proximity to
    that zone, for a LONG bias. Before the fix, confluence_bonus for
    this exact fixture would be 0.0 (market_structure's level simply
    never entered find_confluence_zones), because market_structure's
    Swing High was the only structurally-relevant level near price and
    it was invisible to confluence.
    """
    agents = [
        _agent("market_structure", LONG, 80,
               key_levels=[{"label": "Swing Low", "price": 89950.0, "type": "support"}],
               evidence=["LONG BOS recovery", "State: TRIGGERED"]),
        _agent("support_resistance", LONG, 68,
               key_levels=[{"label": "Support", "price": 89970.0, "type": "support"}],
               evidence=["Level hold"]),
        _agent("trend", LONG, 75, evidence=["LONG trend"]),
        _agent("pattern", LONG, 70, evidence=["LONG pattern", "State: CONFIRMED"]),
        _agent("momentum", LONG, 72, evidence=["LONG momentum", "State: LONG_ACCELERATING"]),
        _agent("volume", LONG, 65, evidence=["Volume confirms", "State: BULLISH_CONFIRMATION"]),
        _agent("fibonacci", LONG, 60, evidence=["Fib confluence"]),
        _agent("fair_value_gap", LONG, 55, evidence=["FVG support"]),
        _agent("breakout", LONG, 55, evidence=["Continuation"]),
        _agent("elliott_wave", SHORT, 40, evidence=["diagnostic only"]),
    ]
    result = brain_engine.decide(agents, PRICE, "15m", HTF_NEUTRAL, atr_value=50.0)

    assert result["confluence_bonus"] > 0
    zone_agent_sets = [set(z["agents"]) for z in result["confluence_zones"]]
    assert any({"market_structure", "support_resistance"} <= s for s in zone_agent_sets), (
        "expected a confluence zone containing both market_structure and "
        f"support_resistance, got zones: {result['confluence_zones']}"
    )
    # location_score reuses find_confluence_zones -- with the fix, the
    # nearby support zone (market_structure + S/R) should read as a
    # meaningfully favorable location for a LONG, not the neutral
    # default (50.0) returned when no relevant zone is found.
    assert result["location_score"] > 50.0