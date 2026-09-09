"""BRAIN V2 — Setup / Trigger / Location / Extension scoring.

Implements the spec's core distinction: SETUP != TRIGGER !=
CONFIRMATION (section 16), plus location (17), timing/extension (18-19,
the latter genuinely new — max_extension_pct exists in config but is
not consumed anywhere in the current codebase, confirmed by direct
inspection). Each function returns a score (0-100) plus the concrete
evidence behind it, since every number here must be explainable
(section 43).
"""
from typing import Dict, List
from . import evidence as ev
from ..contract import LONG, SHORT

EXCLUDE_FROM_EXECUTION = ("elliott_wave",)  # spec section 2 — research/
# diagnostic only, zero execution influence. Still visible in evidence/UI.


def setup_score(agents: List, bias: str) -> Dict:
    """A setup is directional AGREEMENT across independent ROLES, not a
    raw agent count (spec sections 13, 47) — five agents in the same
    role group agreeing is much weaker evidence of a real setup than
    three DIFFERENT roles agreeing, since the former is likely the same
    underlying phenomenon described five times (section 30)."""
    if bias not in (LONG, SHORT):
        return {"score": 0.0, "roles_aligned": [], "roles_opposed": [], "detail": "no directional bias"}

    groups = ev.role_grouped_evidence(agents, exclude_agents=EXCLUDE_FROM_EXECUTION)
    aligned, opposed = [], []
    total_aligned_conf = 0.0
    for role, g in groups.items():
        if g["lean"] == bias:
            aligned.append(role)
            total_aligned_conf += g["strongest_confidence"]
        elif g["lean"] != "NEUTRAL":
            opposed.append(role)

    n_roles = max(len(groups), 1)
    breadth = len(aligned) / n_roles  # how many independent roles agree
    avg_strength = (total_aligned_conf / len(aligned)) if aligned else 0.0

    score = min(100.0, breadth * 55.0 + (avg_strength / 100.0) * 45.0)
    return {
        "score": round(score, 1),
        "roles_aligned": aligned,
        "roles_opposed": opposed,
        "breadth": round(breadth, 2),
        "avg_role_strength": round(avg_strength, 1),
        "detail": f"{len(aligned)}/{n_roles} independent roles support {bias}",
    }


def trigger_score(agents: List, bias: str) -> Dict:
    """A trigger is an ACTIONABLE event, not just a directional read —
    extracted from each agent's own reported state (section 14). Early
    triggers count fully; this deliberately does NOT wait for the
    strongest possible confirmation state (section 15)."""
    if bias not in (LONG, SHORT):
        return {"score": 0.0, "primary": [], "detail": "no directional bias"}

    primary = []
    supporting = []
    total = 0.0
    for res in agents:
        if not res.valid or res.agent in EXCLUDE_FROM_EXECUTION:
            continue
        state = ev.extract_state(res.evidence)
        cls = ev.classify_state(state)
        if res.direction != bias:
            continue
        if cls["trigger"] > 0:
            primary.append((res.agent, state, res.confidence))
            total += res.confidence
        elif not cls["context_only"]:
            supporting.append(res.agent)

    # A genuine trigger needs at least one agent reporting an actionable
    # state in the bias direction — without that, this is a directional
    # READ, not a trigger, regardless of how many agents merely agree
    # on direction (this is exactly the setup-vs-trigger distinction).
    if not primary:
        return {"score": 0.0, "primary": [], "supporting": supporting,
                "detail": "no agent reports an actionable trigger state"}

    avg_conf = total / len(primary)
    score = min(100.0, len(primary) * 20.0 + avg_conf * 0.5)
    return {
        "score": round(score, 1),
        "primary": [f"{a} ({s})" for a, s, c in primary],
        "supporting": supporting,
        "detail": f"{len(primary)} agent(s) report an actionable {bias} trigger",
    }


def location_score(agents: List, price: float, bias: str, confluence_zones: List[Dict]) -> Dict:
    """Location asks: is this trigger happening somewhere meaningful?
    Reuses the EXISTING confluence-zone deduplication (confluence.py's
    diminishing-returns clustering already prevents FVG+Fibonacci+S/R
    describing the same zone from being triple-counted) rather than
    re-summing key_levels independently (section 17's explicit
    "avoid double counting multiple agents describing the same level")."""
    if bias not in (LONG, SHORT) or not confluence_zones:
        return {"score": 30.0, "detail": "no nearby confluence zone identified"}  # neutral-ish default, not zero

    relevant = [z for z in confluence_zones
               if (z["side"] == "support" and bias == LONG) or (z["side"] == "resistance" and bias == SHORT)
               or (z["side"] == "resistance" and bias == LONG) or (z["side"] == "support" and bias == SHORT)]
    if not relevant:
        return {"score": 30.0, "detail": "no confluence zone on the relevant side"}

    best = max(relevant, key=lambda z: z["count"])
    dist_pct = abs(best["price"] - price) / max(price, 1e-9) * 100
    proximity = max(0.0, 1.0 - dist_pct / 1.5)  # zones within ~1.5% matter most
    quality = min(1.0, best["count"] / 3.0)
    score = min(100.0, (proximity * 0.5 + quality * 0.5) * 100.0)
    favorable = (best["side"] == "support" and bias == LONG) or (best["side"] == "resistance" and bias == SHORT)
    if not favorable:
        score *= 0.5  # trigger sitting right under resistance (for LONG) or above support (for SHORT) is a weaker location
    return {
        "score": round(score, 1),
        "zone_price": best["price"], "zone_agents": best["agents"], "distance_pct": round(dist_pct, 2),
        "favorable_side": favorable,
        "detail": f"nearest relevant zone @ {best['price']:.1f} ({', '.join(best['agents'])}), {dist_pct:.2f}% away",
    }


def extension_timing(agents: List, price: float, bias: str, atr_value: float, max_extension_pct: float) -> Dict:
    """Genuinely new logic (max_extension_pct exists in config but is
    consumed nowhere today, confirmed by direct inspection) — extension
    is an entry-QUALITY filter, not a directional signal (section 19).
    Uses the nearest same-direction agent-reported key level as the
    reference point ("where did this move start from"), falling back to
    a neutral read if no reference is available rather than fabricating
    one."""
    if bias not in (LONG, SHORT) or atr_value <= 0:
        return {"state": "TIMELY", "extension_pct": 0.0, "detail": "no directional bias to evaluate extension against"}

    reference = None
    for res in agents:
        if not res.valid or res.direction != bias:
            continue
        for lv in res.key_levels:
            p = lv.get("price")
            if p is None:
                continue
            if (bias == LONG and p < price) or (bias == SHORT and p > price):
                if reference is None or abs(p - price) < abs(reference - price):
                    reference = p
    if reference is None:
        return {"state": "TIMELY", "extension_pct": 0.0, "detail": "no reference level available — assumed timely"}

    extension_pct = abs(price - reference) / max(reference, 1e-9) * 100
    ratio = extension_pct / max(max_extension_pct, 1e-9)
    if ratio <= 0.5:
        state = "EARLY"
    elif ratio <= 1.0:
        state = "TIMELY"
    elif ratio <= 1.5:
        state = "LATE"
    else:
        state = "EXTENDED"
    return {
        "state": state, "extension_pct": round(extension_pct, 2),
        "reference_price": round(reference, 2), "ratio_to_max": round(ratio, 2),
        "detail": f"{extension_pct:.2f}% from reference {reference:.1f} (max allowed {max_extension_pct:.1f}%)",
    }