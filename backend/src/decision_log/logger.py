"""Main orchestration for the decision logging system.

This is the ONLY module other code should call into. Every function here
is safe to wrap in try/except at the call site (and every call site
does) — nothing in this module can raise in a way that should ever be
allowed to interrupt the actual trading loop.

Call observe() once per autotrader tick, right after full_analysis() and
brain.decide() have already produced their results. This module only
reads what's already been computed — it recomputes nothing, per the
spec's "do not calculate duplicate indicators solely for logging" rule.
"""
import json
import time
import uuid
from typing import Dict, Optional

from . import db as logdb
from . import setup_tracker
from . import risk_assessor
from . import explain

# In-memory "what was the last decision state for this timeframe" — only
# needs to survive within one running process; a restart naturally
# starts fresh, which just means one extra full record gets logged after
# restart rather than treating it as a repeat. Not a correctness issue.
_last_decision_state: Dict[str, str] = {}
_last_candle_ts: Dict[str, int] = {}

logdb.init_db()


def _agent_field(agents: list, agent_id: str, field: str = "direction", default=None):
    for a in agents:
        if a.get("agent") == agent_id:
            return a.get(field, default)
    return default


def _record_checkpoint(setup_id: str, symbol: str, timeframe: str, ts: int,
                       price: float, htf_regime: str, structure: str,
                       momentum: str, breakout: str) -> Dict:
    fields = {"htf_regime": htf_regime, "structure": structure,
              "momentum": momentum, "breakout": breakout}

    with logdb._conn() as conn:
        prev = conn.execute(
            """SELECT * FROM market_checkpoints WHERE setup_id=?
               ORDER BY timestamp DESC LIMIT 1""", (setup_id,)
        ).fetchone()

        changed_fields = []
        if prev is not None:
            for key, val in fields.items():
                if prev[key] != val:
                    changed_fields.append((key, prev[key], val))

        status = "CHANGE_DETECTED" if changed_fields else "NO_SIGNIFICANT_CHANGE"
        conn.execute(
            """INSERT INTO market_checkpoints
               (setup_id, timestamp, symbol, timeframe, price, htf_regime,
                structure, momentum, breakout, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (setup_id, ts, symbol, timeframe, price, htf_regime, structure,
             momentum, breakout, status),
        )
        for key, prev_val, cur_val in changed_fields:
            conn.execute(
                """INSERT INTO market_changes
                   (setup_id, timestamp, field, previous_value, current_value, status)
                   VALUES (?,?,?,?,?,?)""",
                (setup_id, ts, key, prev_val, cur_val, "CHANGE_DETECTED"),
            )
        conn.commit()

    if changed_fields:
        setup_tracker.increment_setup_counters(setup_id, meaningful_change=True)
    return {"status": status, "changed_fields": changed_fields}


def _record_full_decision(setup_id: str, symbol: str, timeframe: str, ts: int,
                          price: float, agents: list, market_state: Dict,
                          brain: Dict, sl_atr_mult: float, tp_atr_mult: float,
                          notional_usd: float, bid: Optional[float],
                          ask: Optional[float]) -> str:
    decision_id = f"DEC-{uuid.uuid4().hex[:12]}"

    htf = brain.get("htf_regime", {})
    gate = brain.get("htf_gate", {})
    conflict = brain.get("conflict", {})
    bias = brain.get("direction", "NEUTRAL")
    state = brain.get("state", "WAIT")

    htf_blocked = gate.get("relation") == "counter-trend"
    htf_blocked_reason = None
    if htf_blocked:
        htf_blocked_reason = (
            f"HTF {htf.get('regime', 'NEUTRAL')} regime conflicts with {bias} bias — "
            f"extra score +{gate.get('extra_score_required', 0):.0f}, "
            f"extra confidence +{gate.get('extra_confidence_required', 0):.0f} required"
        )

    passing = [a for a in agents if a.get("direction") == bias and bias != "NEUTRAL"]
    failing = [a for a in agents if a.get("direction") not in ("NEUTRAL", bias) and a.get("direction") in ("LONG", "SHORT")]
    neutral = [a for a in agents if a.get("direction") == "NEUTRAL"]

    atr_pct = market_state.get("volatility", {}).get("atr_pct") if market_state else None
    atr_val = market_state.get("volatility", {}).get("atr") if market_state else None

    risk = risk_assessor.assess(
        price=price, atr=atr_val or 0.0, sl_atr_mult=sl_atr_mult,
        tp_atr_mult=tp_atr_mult, notional_usd=notional_usd, bid=bid, ask=ask,
    ) if atr_val else {}

    final_decision = state
    rejection_stage = None
    rejection_reason = None
    if state in ("WAIT", "AVOID"):
        why = brain.get("why_state", [])
        rejection_reason = "; ".join(why) if why else None
        if htf_blocked:
            rejection_stage = "HTF_REGIME"
        elif conflict.get("veto") or conflict.get("severity") == "severe":
            rejection_stage = "CONFLICT"
        elif brain.get("active_agents", 99) < 6:
            rejection_stage = "MIN_AGENTS"
        else:
            rejection_stage = "ENTRY_THRESHOLD"

    explanation = explain.explain_decision(
        final_decision=state, bias=bias, agent_results=agents,
        htf_blocked=htf_blocked, htf_regime=htf.get("regime", "NEUTRAL"),
        rejection_stage=rejection_stage, rejection_reason=rejection_reason,
    )

    structure_dir = _agent_field(agents, "market_structure")
    momentum_dir = _agent_field(agents, "momentum")
    breakout_dir = _agent_field(agents, "breakout")
    trend_dir = _agent_field(agents, "trend")

    with logdb._conn() as conn:
        conn.execute(
            """INSERT INTO decisions (
                decision_id, setup_id, timestamp, symbol, timeframe,
                current_price, spread, spread_pct, atr, atr_pct, volatility_status, market_session,
                htf_structure, m15_structure, trend_state, momentum_state, breakout_state,
                htf_regime, htf_regime_score, htf_direction, htf_confidence, htf_blocked, htf_blocked_reason,
                confluence_bullish_score, confluence_bearish_score, confluence_net_score, confluence_confidence,
                agents_passing, agents_failing, agents_neutral,
                entry_filter_result, entry_filter_reason,
                risk_status, risk_score, stop_distance, risk_reward, position_size_status,
                final_decision, confidence, primary_reason, secondary_reason,
                rejection_stage, rejection_reason, explanation,
                direction, brain_confidence, setup_score, setup_state,
                trigger_score, trigger_state, location_score, extension_state,
                decision_state, brain_version, primary_evidence_json, supporting_evidence_json
            ) VALUES (?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,
                      ?,?,?,?, ?,?,?,?, ?,?,?,?)""",
            (
                decision_id, setup_id, ts, symbol, timeframe,
                price, risk.get("spread"), risk.get("spread_pct"), atr_val, atr_pct,
                risk.get("volatility_status"), None,
                htf.get("regime"), structure_dir, trend_dir, momentum_dir, breakout_dir,
                htf.get("regime"), htf.get("regime_score"), htf.get("regime"),
                None, int(htf_blocked), htf_blocked_reason,
                None, None, brain.get("consensus_score"), brain.get("confidence"),
                len(passing), len(failing), len(neutral),
                "PASS" if state in ("LONG", "SHORT") else "REJECT", None,
                risk.get("risk_status"), risk.get("risk_score"), risk.get("stop_distance"),
                risk.get("risk_reward"), risk.get("position_size_status"),
                final_decision, brain.get("confidence"),
                (brain.get("reasons") or [None])[0], (brain.get("reasons") or [None, None])[1] if len(brain.get("reasons", [])) > 1 else None,
                rejection_stage, rejection_reason, explanation,
                # Brain V2 fields confirmed generated but not previously
                # persisted (see audit) — pulled with .get() throughout
                # since a caller could in principle still be running the
                # pre-V2 Brain, which won't have these keys at all; NULL
                # in that case is correct, not a bug to paper over.
                bias, brain.get("brain_confidence"), brain.get("setup_score"), brain.get("setup_state"),
                brain.get("trigger_score"), brain.get("trigger_state"), brain.get("location_score"),
                brain.get("extension_state"), brain.get("decision_state"), brain.get("brain_version"),
                json.dumps(brain.get("primary_evidence")) if brain.get("primary_evidence") is not None else None,
                json.dumps(brain.get("supporting_evidence")) if brain.get("supporting_evidence") is not None else None,
            ),
        )
        for a in agents:
            conn.execute(
                """INSERT INTO agent_decisions
                   (decision_id, agent_name, direction, score, status, confidence, reason, evidence_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (decision_id, a.get("agent"), a.get("direction"), a.get("strength"),
                 "PASS" if a.get("direction") == bias and bias != "NEUTRAL" else
                 ("FAIL" if a.get("direction") in ("LONG", "SHORT") else "NEUTRAL"),
                 a.get("confidence"), (a.get("evidence") or [None])[0],
                 json.dumps(a.get("evidence") or [])),
            )
        conn.commit()

    setup_tracker.increment_setup_counters(
        setup_id, decision=True, trade=(state in ("LONG", "SHORT")),
        rejected=(state in ("WAIT", "AVOID")), rejection_stage=rejection_stage,
        confluence_score=brain.get("confidence"),
    )
    return decision_id


def observe(analysis: Dict, sl_atr_mult: float, tp_atr_mult: float,
           notional_usd: float, bid: Optional[float] = None,
           ask: Optional[float] = None) -> Optional[str]:
    """Call once per autotrader tick. Returns the decision_id if a full
    decision record was written this tick, else None (a checkpoint-only
    tick, or logging failed safely).

    This function catches its OWN exceptions internally — belt-and-
    suspenders on top of callers also wrapping this in try/except, per
    the spec's "logging must never block the trading engine" rule. A
    malformed input or a database hiccup here should never be able to
    propagate into the trading loop, even if a future call site forgets
    to wrap it."""
    try:
        return _observe_inner(analysis, sl_atr_mult, tp_atr_mult, notional_usd, bid, ask)
    except Exception:
        return None


def _observe_inner(analysis: Dict, sl_atr_mult: float, tp_atr_mult: float,
                   notional_usd: float, bid: Optional[float] = None,
                   ask: Optional[float] = None) -> Optional[str]:
    if "error" in analysis:
        return None

    symbol = analysis["symbol"]
    timeframe = analysis["timeframe"]
    price = analysis["price"]
    agents = analysis["agents"]
    market_state = analysis.get("market_state", {})
    brain = analysis["brain"]
    ts = int(time.time())

    # Candle-level setup lifecycle. Uses the market_state's own candle
    # timestamp if available, else falls back to "now" bucketed to the
    # timeframe — falling back is only relevant if market_state ever
    # omits this, which it doesn't today, but keeps this resilient.
    candle_ts = market_state.get("timestamp", ts) if market_state else ts

    key = f"{symbol}:{timeframe}"
    prev_candle_ts = _last_candle_ts.get(key)
    setup_tracker.close_previous_setup_if_needed(symbol, timeframe, prev_candle_ts, candle_ts)
    _last_candle_ts[key] = candle_ts

    state = brain.get("state", "WAIT")
    setup_id = setup_tracker.get_or_create_setup(symbol, timeframe, candle_ts, state)
    setup_tracker.update_setup_final_state(setup_id, state)

    htf_regime = brain.get("htf_regime", {}).get("regime", "NEUTRAL")
    structure_dir = _agent_field(agents, "market_structure") or "NEUTRAL"
    momentum_dir = _agent_field(agents, "momentum") or "NEUTRAL"
    breakout_dir = _agent_field(agents, "breakout") or "NEUTRAL"

    _record_checkpoint(setup_id, symbol, timeframe, ts, price, htf_regime,
                       structure_dir, momentum_dir, breakout_dir)

    # Full decision record whenever the resolved state genuinely changes,
    # OR whenever it resolves to something fireable (LONG/SHORT) — this
    # matches "whenever MIB reaches a meaningful trade decision" without
    # writing a heavy record on every single unchanged WAIT tick.
    last_state = _last_decision_state.get(key)
    decision_id = None
    if state != last_state or state in ("LONG", "SHORT"):
        decision_id = _record_full_decision(
            setup_id, symbol, timeframe, ts, price, agents, market_state,
            brain, sl_atr_mult, tp_atr_mult, notional_usd, bid, ask,
        )
    _last_decision_state[key] = state
    return decision_id


def record_trade_execution(trade_id: str, decision_id: Optional[str], setup_id: Optional[str],
                           direction: str, entry_price: float, position_size: float,
                           stop_loss: Optional[float], take_profit: Optional[float]) -> None:
    try:
        with logdb._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO trade_executions
                   (trade_id, decision_id, setup_id, timestamp, direction, entry_price,
                    position_size, stop_loss, take_profit)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (trade_id, decision_id, setup_id, int(time.time()), direction,
                 entry_price, position_size, stop_loss, take_profit),
            )
            conn.commit()
    except Exception:
        pass


def record_trade_event(trade_id: str, event_type: str, details: Optional[Dict] = None) -> None:
    try:
        with logdb._conn() as conn:
            conn.execute(
                "INSERT INTO trade_events (trade_id, timestamp, event_type, details_json) VALUES (?,?,?,?)",
                (trade_id, int(time.time()), event_type, json.dumps(details or {})),
            )
            conn.commit()
    except Exception:
        pass


def record_trade_outcome(trade_id: str, exit_price: float, pnl: float,
                         exit_reason: str, entry_price: float, sl_price: Optional[float]) -> None:
    try:
        r_multiple = None
        if sl_price is not None and entry_price != sl_price:
            risk_per_unit = abs(entry_price - sl_price)
            if risk_per_unit > 0:
                r_multiple = (exit_price - entry_price) / risk_per_unit if exit_price >= entry_price else -(entry_price - exit_price) / risk_per_unit
        with logdb._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trade_outcomes
                   (trade_id, exit_price, exit_time, pnl, r_multiple, exit_reason)
                   VALUES (?,?,?,?,?,?)""",
                (trade_id, exit_price, int(time.time()), pnl, r_multiple, exit_reason),
            )
            conn.commit()
    except Exception:
        pass


def get_diagnostic_stats(since_ts: Optional[int] = None) -> Dict:
    """Answers the spec's own priority questions directly:
    how many setups, how many decisions, how many rejected and why,
    which agents pass most often. Read-only, safe to call anytime."""
    try:
        since_ts = since_ts if since_ts is not None else int(time.time()) - 86400
        with logdb._conn() as conn:
            setups = conn.execute(
                "SELECT COUNT(*) c FROM setups WHERE opened_at >= ?", (since_ts,)
            ).fetchone()["c"]
            decisions = conn.execute(
                "SELECT COUNT(*) c FROM decisions WHERE timestamp >= ?", (since_ts,)
            ).fetchone()["c"]
            trades = conn.execute(
                "SELECT COUNT(*) c FROM decisions WHERE timestamp >= ? AND final_decision IN ('LONG','SHORT')",
                (since_ts,),
            ).fetchone()["c"]
            rejection_rows = conn.execute(
                """SELECT rejection_stage, COUNT(*) c FROM decisions
                   WHERE timestamp >= ? AND rejection_stage IS NOT NULL
                   GROUP BY rejection_stage""", (since_ts,),
            ).fetchall()
            avg_confidence = conn.execute(
                "SELECT AVG(confidence) a FROM decisions WHERE timestamp >= ?", (since_ts,)
            ).fetchone()["a"]
            agent_rows = conn.execute(
                """SELECT agent_name, status, COUNT(*) c FROM agent_decisions
                   WHERE decision_id IN (SELECT decision_id FROM decisions WHERE timestamp >= ?)
                   GROUP BY agent_name, status""", (since_ts,),
            ).fetchall()

        agent_activity: Dict[str, Dict[str, int]] = {}
        for r in agent_rows:
            agent_activity.setdefault(r["agent_name"], {})[r["status"]] = r["c"]

        return {
            "setups": setups,
            "decisions": decisions,
            "trades": trades,
            "rejected_by_stage": {r["rejection_stage"]: r["c"] for r in rejection_rows},
            "average_confidence": round(avg_confidence, 1) if avg_confidence else None,
            "agent_activity": agent_activity,
        }
    except Exception:
        return {"error": "diagnostic query failed"}