"""BRAIN V2 — Evidence/state extraction.

Every upgraded agent (Market Structure, Breakout, Momentum, Volume,
Pattern) already computes a rich internal state (TRIGGERED, CONFIRMED,
LONG_ACCELERATING, BULLISH_ABSORPTION, etc.) — but AgentResult has no
structured field for it (see contract.py's new optional `state` field,
added for future agents but not required by anything today). Today,
that state only exists as a "State: X" line inside each agent's
evidence list.

This module reads it from there. That's a deliberate architecture
decision, not a workaround: the live path threads a shared MarketState
object into Market Structure specifically, but the backtest path does
NOT (confirmed by direct inspection of backtest_walkforward.py) — so
anything Brain reads must come from the ONE thing that's identical in
both: the AgentResult list itself. Evidence text is exactly that.

Also handles role grouping (spec section 30 — avoid double counting
correlated agents) and classifying whether a state represents an
actionable TRIGGER versus merely descriptive CONTEXT.
"""
import re
from typing import Dict, List, Optional

STATE_LINE_RE = re.compile(r"^State:\s*([A-Za-z_]+)")


def _get(res, key, default=None):
    if isinstance(res, dict):
        return res.get(key, default)
    return getattr(res, key, default)


ROLE_GROUPS = {
    "STRUCTURE": ("market_structure",),
    "TREND": ("trend",),
    "MOMENTUM": ("momentum",),
    "PARTICIPATION": ("volume",),
    "LOCATION": ("support_resistance", "fair_value_gap", "fibonacci"),
    "PATTERN": ("pattern",),
    "BREAKOUT": ("breakout",),
    "CONTEXT": ("elliott_wave",),
}
AGENT_ROLE = {a: role for role, agents in ROLE_GROUPS.items() for a in agents}

# V2.1 — TREND is its own role. High-confidence EMA must not share a
# bucket with Structure or drown Location/S/R.
CONSENSUS_ROLE_GROUPS = {
    "STRUCTURE": ("market_structure",),
    "TREND": ("trend",),
    "DRIVE": ("breakout", "momentum", "volume"),
    "LOCATION": ("support_resistance", "fair_value_gap", "fibonacci"),
    "PATTERN": ("pattern",),
}
TREND_ROLE_CAP = 50.0
CONSENSUS_AGENT_ROLE = {
    a: role for role, agents in CONSENSUS_ROLE_GROUPS.items() for a in agents
}
_ROLE_DIMINISH = 0.5

# V2.1 — ACCELERATING and BREAKOUT_DETECTED are context, not doors.
TRIGGER_KEYWORDS = (
    "TRIGGERED", "CONFIRMED", "REJECTION", "RECOVERY", "REVERSAL",
    "LEVEL_HOLD", "CONTINUATION", "ABSORPTION", "DIVERGENCE", "RETEST",
)
NEGATIVE_KEYWORDS = ("FAILED", "EXPIRED", "INVALID", "EXHAUSTING", "WEAK", "FAILURE")
CONTEXT_ONLY_KEYWORDS = (
    "FORMING", "DEVELOPING", "WATCH", "CANDIDATE", "NONE", "STABLE",
    "MATURE", "BUILDING", "NEUTRAL", "RETRACING", "PULLBACK", "RETEST",
    "ACCELERATING", "BREAKOUT_DETECTED",
)


def extract_state(evidence: List[str]) -> Optional[str]:
    for line in reversed(evidence or []):
        m = STATE_LINE_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


def classify_state(state: Optional[str]) -> Dict[str, float]:
    if not state:
        return {"trigger": 0.0, "negative": 0.0, "context_only": 0.0}
    s = state.upper()
    trigger = 1.0 if any(k in s for k in TRIGGER_KEYWORDS) else 0.0
    negative = 1.0 if any(k in s for k in NEGATIVE_KEYWORDS) else 0.0
    context_only = 1.0 if (not trigger and any(k in s for k in CONTEXT_ONLY_KEYWORDS)) else 0.0
    return {"trigger": trigger, "negative": negative, "context_only": context_only}


def agent_role(agent_id: str) -> str:
    return AGENT_ROLE.get(agent_id, "OTHER")


def role_grouped_evidence(agents: List, exclude_agents: tuple = ()) -> Dict[str, Dict]:
    groups: Dict[str, List] = {}
    for res in agents:
        if not res.valid or res.agent in exclude_agents:
            continue
        role = agent_role(res.agent)
        groups.setdefault(role, []).append(res)
    out = {}
    for role, members in groups.items():
        long_w = sum(r.confidence for r in members if r.direction == "LONG")
        short_w = sum(r.confidence for r in members if r.direction == "SHORT")
        strongest = max(members, key=lambda r: r.confidence) if members else None
        out[role] = {
            "members": [r.agent for r in members],
            "long_weight": round(long_w, 1),
            "short_weight": round(short_w, 1),
            "lean": "LONG" if long_w > short_w else ("SHORT" if short_w > long_w else "NEUTRAL"),
            "strongest_agent": strongest.agent if strongest else None,
            "strongest_confidence": round(strongest.confidence, 1) if strongest else 0.0,
        }
    return out


def consensus_role(agent_id: str) -> str:
    return CONSENSUS_AGENT_ROLE.get(agent_id, "OTHER")


def _diminished_mass(weighted_values: List[float]) -> float:
    total = 0.0
    factor = 1.0
    for v in sorted(weighted_values, reverse=True):
        total += v * factor
        factor *= _ROLE_DIMINISH
    return total


def role_net_evidence(agents: List, weights: Dict, exclude_agents: tuple = ()) -> Dict:
    buckets: Dict[str, List] = {role: [] for role in CONSENSUS_ROLE_GROUPS}
    for res in agents:
        if not res.valid or res.agent in exclude_agents:
            continue
        role = consensus_role(res.agent)
        if role == "OTHER":
            continue
        buckets[role].append(res)
    roles = {}
    contributions = []
    long_role_scores = []
    short_role_scores = []
    for role, members in buckets.items():
        long_items = []
        short_items = []
        agents_out = []
        for res in members:
            w = float(weights.get(res.agent, 1.0))
            mass = float(res.confidence) * w
            entry = {
                "agent": res.agent,
                "direction": res.direction,
                "confidence": round(float(res.confidence), 1),
                "weight": w,
                "mass": round(mass, 2),
            }
            agents_out.append(entry)
            if res.direction == "LONG":
                long_items.append((mass, w, res.agent))
            elif res.direction == "SHORT":
                short_items.append((mass, w, res.agent))
        long_eff = _diminished_mass([m for m, _, _ in long_items])
        short_eff = _diminished_mass([m for m, _, _ in short_items])
        if long_eff <= 0 and short_eff <= 0:
            continue
        if long_eff >= short_eff:
            direction = "LONG"
            lean_items = long_items
        else:
            direction = "SHORT"
            lean_items = short_items
        cap = _diminished_mass([100.0 * w for _, w, _ in lean_items])
        quality = min(100.0, 100.0 * max(long_eff, short_eff) / max(cap, 1e-9))
        denom = long_eff + short_eff
        purity = abs(long_eff - short_eff) / denom if denom else 0.0
        score = quality * purity
        if role == "TREND":
            score = min(score, TREND_ROLE_CAP)
        signed = score if direction == "LONG" else -score
        rec = {
            "direction": direction,
            "score": round(score, 1),
            "signed": round(signed, 1),
            "quality": round(quality, 1),
            "agents": [e["agent"] for e in agents_out],
            "agent_detail": agents_out,
            "long_eff": round(long_eff, 2),
            "short_eff": round(short_eff, 2),
            "corroboration": len(lean_items),
            "purity": round(purity, 3),
        }
        roles[role] = rec
        contributions.append({
            "role": role,
            "direction": direction,
            "score": rec["score"],
            "signed": rec["signed"],
            "corroboration": rec["corroboration"],
            "agents": rec["agents"],
        })
        if direction == "LONG":
            long_role_scores.append(score)
        else:
            short_role_scores.append(score)
    n = len(contributions)
    consensus = (sum(c["signed"] for c in contributions) / n) if n else 0.0
    return {
        "roles": roles,
        "contributions": contributions,
        "consensus": round(consensus, 1),
        "long_role_evidence": round(sum(long_role_scores), 1),
        "short_role_evidence": round(sum(short_role_scores), 1),
        "n_long_roles": len(long_role_scores),
        "n_short_roles": len(short_role_scores),
        "n_directional_roles": n,
    }
