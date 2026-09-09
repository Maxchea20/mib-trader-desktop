"""BRAIN V2 — Open-trade thesis tracking (spec sections 33-37).

Once Execution Manager opens a trade, Brain switches from ENTRY MODE to
POSITION MANAGEMENT MODE: it continuously re-evaluates the ORIGINAL
thesis against current evidence and recommends HOLD / REDUCE / EXIT /
PROTECT. Execution Manager remains the only thing that actually acts —
this module only recommends, exactly matching the spec's explicit
"Execution Manager executes, Brain does not" boundary. Nothing here
calls into paper_trading, autotrader, or any execution path.
"""
from typing import Dict, List
from ..contract import LONG, SHORT
from . import evidence as ev


def build_thesis(decision: Dict) -> Dict:
    """Snapshot the trade thesis at the moment of entry, from a FIRE
    decision dict (the return value of brain.decide())."""
    return {
        "direction": decision["direction"],
        "primary_evidence": list(decision.get("primary_evidence", [])),
        "supporting_evidence": list(decision.get("supporting_evidence", [])),
        "primary_agents": [p.split(":")[0].strip() for p in decision.get("primary_evidence", [])],
        "consensus_at_entry": decision["consensus_score"],
        "setup_score_at_entry": decision["setup_score"],
        "brain_confidence_at_entry": decision["brain_confidence"],
        "htf_regime_at_entry": decision["htf_regime"].get("regime"),
        "entry_price": decision.get("price"),
    }


_NAME_TO_AGENT = {
    "Structure": "market_structure", "Breakout": "breakout", "Fibonacci": "fibonacci",
    "Elliott Wave": "elliott_wave", "Volume": "volume", "Momentum": "momentum",
    "Support/Resistance": "support_resistance", "Trend": "trend",
    "Pattern": "pattern", "FVG": "fair_value_gap",
}


def evaluate_open_position(thesis: Dict, current_agents: List, current_decision: Dict) -> Dict:
    """Compares current evidence against the ORIGINAL thesis. Returns a
    recommendation (HOLD / REDUCE / EXIT / PROTECT) plus the reasoning —
    never an executed action. current_decision is a fresh
    brain.decide() call on the SAME direction context, used for its
    consensus/conflict/agent-state snapshot; current_agents is the raw
    AgentResult list for structural-failure checking against the
    thesis's own primary agents specifically.
    """
    direction = thesis["direction"]
    entry_consensus = thesis["consensus_at_entry"]
    current_consensus = current_decision["consensus_score"]

    # Thesis strength comparison (spec section 34) — "improving" means
    # the SAME-direction evidence got stronger, regardless of sign.
    if direction == LONG:
        delta = current_consensus - entry_consensus
    else:
        delta = entry_consensus - current_consensus
    if delta > 15:
        thesis_state = "STRENGTHENED"
    elif delta < -25:
        thesis_state = "INVALIDATED"
    elif delta < -10:
        thesis_state = "WEAKENING"
    else:
        thesis_state = "UNCHANGED"

    # Structural exit (spec section 35) — did any of the ORIGINAL
    # primary-evidence agents specifically flip to the opposing
    # direction, or report a FAILED/INVALID state? This is deliberately
    # scoped to the thesis's OWN primary agents, not "any agent
    # disagreeing" — a structural exit means the reasons you entered
    # broke, not that some unrelated agent is unhappy.
    primary_agent_ids = [_NAME_TO_AGENT.get(n, n) for n in thesis.get("primary_agents", [])]
    structural_failures = []
    for res in current_agents:
        if res.agent not in primary_agent_ids:
            continue
        state = ev.extract_state(res.evidence)
        cls = ev.classify_state(state)
        opposite = (LONG if direction == SHORT else SHORT)
        if res.direction == opposite or cls["negative"] > 0:
            structural_failures.append((res.agent, res.direction, state))

    structural_exit = len(structural_failures) >= 1 and thesis_state in ("WEAKENING", "INVALIDATED")

    # Profit protection (spec section 36) — favorable move + momentum
    # specifically weakening + some structural warning, even without a
    # full structural exit yet.
    momentum_res = next((r for r in current_agents if r.agent == "momentum"), None)
    momentum_weakening = False
    if momentum_res is not None:
        m_state = ev.extract_state(momentum_res.evidence) or ""
        momentum_weakening = "EXHAUSTING" in m_state.upper() or "DECELERATING" in m_state.upper()
    favorable_move = delta > 0

    if structural_exit or thesis_state == "INVALIDATED":
        recommendation = "EXIT"
    elif favorable_move and momentum_weakening and structural_failures:
        recommendation = "PROTECT"
    elif thesis_state == "WEAKENING" and momentum_weakening:
        recommendation = "REDUCE"
    else:
        recommendation = "HOLD"

    reasons = [f"Thesis {thesis_state.lower()} (consensus moved {delta:+.1f} in the thesis direction)"]
    if structural_failures:
        reasons.append("Structural warning: " + ", ".join(
            f"{a} now {d}" + (f" ({s})" if s else "") for a, d, s in structural_failures))
    if momentum_weakening:
        reasons.append("Momentum weakening on the position's own side")
    if recommendation == "HOLD":
        reasons.append("Original thesis remains intact — no structural or momentum warning")

    return {
        "recommendation": recommendation,
        "thesis_state": thesis_state,
        "consensus_delta": round(delta, 1),
        "structural_failures": [{"agent": a, "direction": d, "state": s} for a, d, s in structural_failures],
        "momentum_weakening": momentum_weakening,
        "reasons": reasons,
    }