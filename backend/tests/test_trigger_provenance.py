"""Regression tests for the 2026-09-12 audit's Step 2 fix: trigger
provenance persistence.

Before this change, brain/scoring.py::trigger_score() computed a rich
per-event record (origin price, contributing agents/roles,
corroboration, state tokens) every tick, but it only ever lived inside
the in-memory Brain result dict for that one call -- decision_log
persisted the `decisions` row (score/state/etc.) and `agent_decisions`
rows (one per agent), but never the trigger events themselves. Once
the tick passed, which structural event actually fired the trade could
no longer be reconstructed from the database.

These tests use an isolated temp SQLite file (never the project's real
market_data.db) and prove:
  1. A decision that fires on a clustered trigger event persists a
     matching row in the new `trigger_events` table, with timestamp,
     price, direction, trigger type, source/origin agents, reference
     (origin) level, timeframe, and a structural-context snapshot --
     readable back via decision_log.logger.get_trigger_events().
  2. A decision that fires on TWO distinct trigger events (different
     origins) persists TWO rows, not one merged/overwritten row.
  3. A WAIT decision with no qualifying trigger persists zero
     trigger_events rows (no fabricated provenance for a trigger that
     never happened).
  4. The pre-existing `decisions`/`agent_decisions` persistence is
     unaffected (parity check) -- this was an additive change only.

Run with: python3 -m pytest tests/test_trigger_provenance.py -v
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Isolate the decision-log DB to a throwaway temp file BEFORE
# importing anything that touches it. decision_log/logger.py calls
# logdb.init_db() at import time, and decision_log/db.py resolves its
# path via market_data.database.db_path() on every call -- patching
# the module-level _DB_PATH here (rather than relying on import-order
# of the MARKET_DB_PATH env var) is deterministic regardless of what
# else has already imported market_data.database in this process.
import src.market_data.database as mdb
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mib_test_triggerprov_")
os.close(_TMP_DB_FD)
mdb._DB_PATH = _TMP_DB_PATH

from src import decision_log  # noqa: E402  (must import after the DB patch above)
from src.brain import brain as brain_engine
from src.contract import AgentResult, LONG, SHORT, NEUTRAL

HTF_LONG = {"regime": "LONG", "regime_score": 25.0, "per_timeframe": {}}
HTF_NEUTRAL = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
ATR = 400.0


def A(agent, direction, conf, evidence=None, levels=None):
    return AgentResult(
        agent=agent, direction=direction, confidence=conf, strength=conf,
        evidence=list(evidence or []), key_levels=list(levels or []), timeframe="15m",
    )


PRICE = 90100.0  # kept above every published origin so the LONG
# extension check (_level_behind_move: origin must sit BEHIND the
# move) resolves cleanly instead of the fixture accidentally landing
# on WAIT_NO_EXTENSION_REF / WAIT_EXTENDED for reasons unrelated to
# what this test file is actually checking (persistence, not scoring).


def _fire_long_single_event_agents():
    return [
        A("market_structure", LONG, 82, ["LONG BOS recovery", "State: TRIGGERED"],
          [{"label": "BOS", "price": 90000.0, "type": "support"}]),
        A("breakout", LONG, 78, ["Breakout continuation", "State: CONTINUATION"],
          [{"label": "BREAKOUT", "price": 90010.0, "type": "break"}]),
        A("trend", LONG, 75, ["LONG trend"]),
        # deliberately no "State: X" line on pattern -- CONFIRMED is a
        # trigger keyword and would otherwise spawn its own unlocated
        # (origin-less) cluster, inflating event_count for reasons
        # unrelated to what this fixture is testing.
        A("pattern", LONG, 70, ["LONG pattern"]),
        # deliberately no trigger-keyword State line here either --
        # this fixture is the ONE-event baseline; momentum gets its own
        # State/level only in _fire_long_two_event_agents() below.
        A("momentum", LONG, 72, ["LONG momentum"]),
        A("volume", LONG, 65, ["Volume confirms", "State: BULLISH_CONFIRMATION"]),
        A("support_resistance", LONG, 68, ["Level hold"]),
        A("fibonacci", LONG, 60, ["Fib confluence"]),
        A("fair_value_gap", LONG, 55, ["FVG support"]),
        A("elliott_wave", SHORT, 40, ["diagnostic only"]),
    ]


def _fire_long_two_event_agents():
    """market_structure/breakout share one origin (~90000); momentum
    publishes a second, distant origin (93000) -> two distinct trigger
    events in the same decision."""
    agents = _fire_long_single_event_agents()
    for a in agents:
        if a.agent == "momentum":
            a.evidence = ["LONG momentum", "State: LONG_ACCELERATING"]
            a.key_levels = [{"label": "ORIGIN", "price": 93000.0, "type": "resistance"}]
    return agents


def _wait_agents():
    """No agent reports an actionable trigger state -> WAIT, no
    qualifying trigger event."""
    return [
        A("market_structure", LONG, 55, ["mild long lean"]),
        A("trend", LONG, 50, ["mild uptrend"]),
        A("pattern", NEUTRAL, 20, ["flat"]),
        A("momentum", NEUTRAL, 20, ["flat"]),
        A("volume", NEUTRAL, 20, ["flat"]),
        A("support_resistance", NEUTRAL, 20, ["flat"]),
        A("fibonacci", NEUTRAL, 20, ["flat"]),
        A("fair_value_gap", NEUTRAL, 20, ["flat"]),
        A("breakout", NEUTRAL, 20, ["flat"]),
        A("elliott_wave", NEUTRAL, 20, ["flat"]),
    ]


def _analysis_payload(agents, brain, price=PRICE, ts=1_700_000_000):
    return {
        "symbol": "BTC_USDT",
        "timeframe": "15m",
        "price": price,
        "agents": [a.to_dict() for a in agents],
        "market_state": {"timestamp": ts, "volatility": {"atr": ATR, "atr_pct": 0.44}},
        "brain": brain,
    }


def test_fired_decision_persists_one_trigger_event_row_with_provenance():
    agents = _fire_long_single_event_agents()
    brain = brain_engine.decide(agents, PRICE, "15m", HTF_LONG, atr_value=ATR)
    assert brain["state"] == "LONG", brain.get("why_state")

    analysis = _analysis_payload(agents, brain)
    decision_id = decision_log.logger.observe(analysis, 1.5, 2.5, 1000.0)
    assert decision_id is not None

    rows = decision_log.logger.get_trigger_events(decision_id)
    assert len(rows) == 1
    row = rows[0]

    # provenance fields required by the spec, all persisted:
    assert row["direction"] == "LONG"
    assert row["timeframe"] == "15m"
    assert row["symbol"] == "BTC_USDT"
    assert row["price"] == PRICE
    assert row["origin_price"] == 90000.0            # reference level
    assert row["timestamp"] > 0                       # when it was recorded
    assert "TRIGGERED" in row["trigger_type"] or "CONTINUATION" in row["trigger_type"]  # trigger type
    assert set(row["source_agents"]) == {"market_structure", "breakout"}  # source/origin
    assert row["corroboration"] == 2
    assert row["max_confidence"] == 82.0

    # relevant structural context survived too
    ctx = row["structural_context"]
    assert ctx["htf_regime"] == "LONG"
    assert ctx["decision_state"] == "FIRE_LONG"
    assert ctx["setup_state"] is not None


def test_two_distinct_trigger_events_persist_as_two_rows():
    agents = _fire_long_two_event_agents()
    brain = brain_engine.decide(agents, PRICE, "15m", HTF_LONG, atr_value=ATR)
    assert brain["trigger_detail"]["event_count"] == 2, brain["trigger_detail"]

    analysis = _analysis_payload(agents, brain)
    decision_id = decision_log.logger.observe(analysis, 1.5, 2.5, 1000.0)
    assert decision_id is not None

    rows = decision_log.logger.get_trigger_events(decision_id)
    assert len(rows) == 2
    origins = {r["origin_price"] for r in rows}
    assert 90000.0 in origins
    assert 93000.0 in origins
    # each row keeps its own distinct source list -- not merged
    momentum_row = next(r for r in rows if r["origin_price"] == 93000.0)
    assert momentum_row["source_agents"] == ["momentum"]


def test_wait_decision_with_no_trigger_persists_zero_trigger_events():
    agents = _wait_agents()
    brain = brain_engine.decide(agents, PRICE, "15m", HTF_NEUTRAL, atr_value=ATR)
    assert brain["state"] == "WAIT"

    analysis = _analysis_payload(agents, brain)
    decision_id = decision_log.logger.observe(analysis, 1.5, 2.5, 1000.0)
    # WAIT with a changed state still writes a full decision row (per
    # existing observe() behavior: state != last_state), but with no
    # qualifying trigger there is nothing to persist as provenance.
    if decision_id is not None:
        rows = decision_log.logger.get_trigger_events(decision_id)
        assert rows == []


def test_existing_decision_and_agent_rows_unaffected_by_the_change():
    """Parity check: this was an additive change. The pre-existing
    `decisions` row and one `agent_decisions` row per agent must still
    be written exactly as before."""
    agents = _fire_long_single_event_agents()
    brain = brain_engine.decide(agents, PRICE, "15m", HTF_LONG, atr_value=ATR)
    analysis = _analysis_payload(agents, brain)
    decision_id = decision_log.logger.observe(analysis, 1.5, 2.5, 1000.0)
    assert decision_id is not None

    from src.decision_log import db as logdb
    with logdb._conn() as conn:
        decision_row = conn.execute(
            "SELECT * FROM decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        agent_rows = conn.execute(
            "SELECT * FROM agent_decisions WHERE decision_id=?", (decision_id,)
        ).fetchall()

    assert decision_row is not None
    assert decision_row["final_decision"] == "LONG"
    assert decision_row["direction"] == "LONG"
    assert decision_row["brain_confidence"] == brain["brain_confidence"]
    assert len(agent_rows) == len(agents)