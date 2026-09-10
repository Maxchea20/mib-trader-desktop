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

# Agents whose key_levels are treated as event origins when they also
# report an actionable trigger state. Lower rank wins. Location agents
# (S/R, FVG, Fib) are last so an incidental nearby level cannot steal
# the reference from a Structure BOS / Breakout origin.
_ORIGIN_AGENT_RANK = {
    "market_structure": 0,
    "breakout": 1,
    "pattern": 2,
    "momentum": 3,
    "volume": 4,
    "trend": 5,
    "support_resistance": 8,
    "fair_value_gap": 8,
    "fibonacci": 8,
}
_ORIGIN_LABEL_HINTS = (
    "BOS", "CHOCH", "BREAK", "BREAKOUT", "ORIGIN", "TRIGGER",
    "SWING", "IMPULSE", "LEVEL", "REF",
)



def setup_score(agents: List, bias: str) -> Dict:
    """A setup is directional AGREEMENT across independent ROLES, not a
    raw agent count (spec sections 13, 47) — five agents in the same
    role group agreeing is much weaker evidence of a real setup than
    three DIFFERENT roles agreeing, since the former is likely the same
    underlying phenomenon described five times (section 30)."""
    if bias not in (LONG, SHORT):
        return {"score": 0.0, "state": "NO_SETUP", "roles_aligned": [], "roles_opposed": [], "detail": "no directional bias"}

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

    # Full setup-state progression (spec section 13) — a setup is not
    # binary "exists or doesn't"; it develops.
    if score < 20:
        setup_state = "NO_SETUP"
    elif score < 45:
        setup_state = "SETUP_FORMING"
    elif score < 65:
        setup_state = "SETUP_DEVELOPING"
    else:
        setup_state = "SETUP_MATURE"

    return {
        "score": round(score, 1),
        "state": setup_state,
        "roles_aligned": aligned,
        "roles_opposed": opposed,
        "breadth": round(breadth, 2),
        "avg_role_strength": round(avg_strength, 1),
        "detail": f"{len(aligned)}/{n_roles} independent roles support {bias}",
    }


# Same-event clustering radius in ATR units.
_CLUSTER_ATR_MULT = 0.5


def _published_origin_price(res):
    """Read an origin from this agent's own key_levels only. Does not invent a price."""
    best = None
    for lv in (res.key_levels or []):
        raw = lv.get("price")
        if raw is None:
            continue
        try:
            level_price = float(raw)
        except (TypeError, ValueError):
            continue
        label = str(lv.get("label") or "")
        label_rank = 0 if any(h in label.upper() for h in _ORIGIN_LABEL_HINTS) else 1
        if best is None or label_rank < best[0]:
            best = (label_rank, level_price)
    return None if best is None else best[1]


def _same_event_origin(p1, p2, atr_value) -> bool:
    if p1 is None or p2 is None:
        return False
    if atr_value and atr_value > 0:
        return abs(p1 - p2) <= _CLUSTER_ATR_MULT * atr_value
    return abs(p1 - p2) <= 1e-9 * max(abs(p1), 1.0)


def _collect_trigger_members(agents: List, bias: str):
    members = []
    supporting = []
    for res in agents:
        if not res.valid or res.agent in EXCLUDE_FROM_EXECUTION:
            continue
        state = ev.extract_state(res.evidence)
        cls = ev.classify_state(state)
        if res.direction != bias:
            continue
        if cls["trigger"] > 0:
            members.append({
                "agent": res.agent,
                "role": ev.agent_role(res.agent),
                "state": state,
                "confidence": float(res.confidence),
                "origin_price": _published_origin_price(res),
                "origin_ts": None,
                "direction": bias,
            })
        elif not cls["context_only"]:
            supporting.append(res.agent)
    return members, supporting


def _cluster_trigger_members(members: List, atr_value) -> List[List]:
    clusters: List[List] = []
    unlocated = [m for m in members if m["origin_price"] is None]
    located = [m for m in members if m["origin_price"] is not None]
    located.sort(key=lambda m: m["origin_price"])
    for m in located:
        placed = False
        for cluster in clusters:
            if any(_same_event_origin(m["origin_price"], x["origin_price"], atr_value) for x in cluster):
                cluster.append(m)
                placed = True
                break
        if not placed:
            clusters.append([m])
    for m in unlocated:
        clusters.append([m])
    return clusters


def _summarize_cluster(cluster: List) -> Dict:
    ranked = sorted(cluster, key=lambda m: _ORIGIN_AGENT_RANK.get(m["agent"], 9))
    origin = None
    for m in ranked:
        if m["origin_price"] is not None:
            origin = m["origin_price"]
            break
    max_conf = max(m["confidence"] for m in cluster)
    avg_conf = sum(m["confidence"] for m in cluster) / len(cluster)
    return {
        "direction": cluster[0]["direction"],
        "origin_price": None if origin is None else round(origin, 2),
        "sources": [m["agent"] for m in cluster],
        "roles": [m["role"] for m in cluster],
        "states": [m["state"] for m in cluster],
        "corroboration": len(cluster),
        "max_confidence": round(max_conf, 1),
        "avg_confidence": round(avg_conf, 1),
        "members": cluster,
    }


def trigger_score(agents: List, bias: str, atr_value: float = None, price: float = None) -> Dict:
    """A trigger is an ACTIONABLE event clustered by published origin.

    Multiple agents on the SAME origin are one event with corroboration.
    Early single-agent triggers still count. `price` is unused for clustering.
    """
    if bias not in (LONG, SHORT):
        return {
            "score": 0.0, "state": "TRIGGER_PENDING", "primary": [],
            "event_count": 0, "cluster_count": 0, "events": [],
            "trigger_sources": [], "trigger_origin": None,
            "trigger_direction": None, "detail": "no directional bias",
        }

    members, supporting = _collect_trigger_members(agents, bias)
    if not members:
        return {
            "score": 0.0, "state": "TRIGGER_PENDING", "primary": [],
            "supporting": supporting, "event_count": 0, "cluster_count": 0,
            "events": [], "trigger_sources": [], "trigger_origin": None,
            "trigger_direction": None,
            "detail": "no agent reports an actionable trigger state",
        }

    raw_clusters = _cluster_trigger_members(members, atr_value)
    events = [_summarize_cluster(c) for c in raw_clusters]
    n_events = len(events)
    best = max(events, key=lambda e: (e["corroboration"], e["max_confidence"]))

    event_term = n_events * 20.0
    conf_term = (sum(e["max_confidence"] for e in events) / n_events) * 0.5
    corr_term = sum(8.0 * min(e["corroboration"] - 1, 3) for e in events)
    score = min(100.0, event_term + conf_term + corr_term)
    trigger_state = "CONFIRMING" if any(e["corroboration"] >= 2 for e in events) else "TRIGGERED"

    primary = [f"{m['agent']} ({m['state']})" for m in members]
    parts = []
    for i, e in enumerate(events, 1):
        origin_txt = "no origin" if e["origin_price"] is None else f"@ {e['origin_price']}"
        parts.append(
            f"{i}. {e['direction']} {origin_txt} sources={','.join(e['sources'])} "
            f"corr={e['corroboration']} conf={e['max_confidence']}"
        )
    detail = f"{n_events} distinct {bias} trigger event(s); " + "; ".join(parts)
    return {
        "score": round(score, 1),
        "state": trigger_state,
        "primary": primary,
        "supporting": supporting,
        "event_count": n_events,
        "cluster_count": n_events,
        "events": [{k: v for k, v in e.items() if k != "members"} for e in events],
        "trigger_sources": best["sources"],
        "trigger_origin": best["origin_price"],
        "trigger_direction": bias,
        "detail": detail,
    }


def location_score(agents: List, price: float, bias: str, confluence_zones: List[Dict]) -> Dict:
    """Location asks: is this trigger happening somewhere meaningful?
    Reuses the EXISTING confluence-zone deduplication (confluence.py's
    diminishing-returns clustering already prevents FVG+Fibonacci+S/R
    describing the same zone from being triple-counted) rather than
    re-summing key_levels independently (section 17's explicit
    \"avoid double counting multiple agents describing the same level\")."""
    if bias not in (LONG, SHORT) or not confluence_zones:
        # No confluence zone found is an absence of information, not
        # evidence of BAD location — should read as neutral, not get
        # penalized the same way an actively unfavorable zone would.
        return {"score": 50.0, "detail": "no nearby confluence zone identified"}

    relevant = [z for z in confluence_zones
               if (z["side"] == "support" and bias == LONG) or (z["side"] == "resistance" and bias == SHORT)
               or (z["side"] == "resistance" and bias == LONG) or (z["side"] == "support" and bias == SHORT)]
    if not relevant:
        return {"score": 50.0, "detail": "no confluence zone on the relevant side"}

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


def _is_trigger_agent(res, bias: str) -> bool:
    """Same classification trigger_score() uses, read-only.
    Does not change trigger scoring — only identifies which agents
    own the event whose origin extension must measure."""
    if not res.valid or res.agent in EXCLUDE_FROM_EXECUTION:
        return False
    if res.direction != bias:
        return False
    state = ev.extract_state(res.evidence)
    return ev.classify_state(state)["trigger"] > 0


def _level_behind_move(price: float, level_price: float, bias: str) -> bool:
    if bias == LONG:
        return level_price < price
    return level_price > price


def _pick_origin_from_trigger_agents(agents: List, price: float, bias: str):
    """Return (origin_price, source_agent, source_label) from TRIGGER
    agents only. Never inspects key_levels on non-trigger agents.

    When several trigger agents publish origins, prefer Structure /
    Breakout over location agents, then the farthest level (impulse
    start) — never the nearest wallpaper level."""
    candidates = []
    for res in agents:
        if not _is_trigger_agent(res, bias):
            continue
        rank = _ORIGIN_AGENT_RANK.get(res.agent, 9)
        for lv in (res.key_levels or []):
            raw = lv.get("price")
            if raw is None:
                continue
            try:
                level_price = float(raw)
            except (TypeError, ValueError):
                continue
            if not _level_behind_move(price, level_price, bias):
                continue
            label = str(lv.get("label") or "")
            label_bonus = 0 if any(h in label.upper() for h in _ORIGIN_LABEL_HINTS) else 1
            distance = abs(price - level_price)
            candidates.append(((rank, label_bonus, -distance), level_price, res.agent, label))
    if not candidates:
        return None, None, None
    candidates.sort(key=lambda c: c[0])
    _, origin, agent, label = candidates[0]
    return origin, agent, label


def extension_timing(agents: List, price: float, bias: str, atr_value: float, max_extension_pct: float) -> Dict:
    """Extension is an entry-QUALITY filter, not a directional signal.

    Reference MUST be the origin of the actionable trigger event that
    armed the setup (Structure BOS, Breakout level, etc.). It must NOT
    be the nearest same-direction key_level from an unrelated agent
    (e.g. an S/R print sitting 50 points under price while the BOS
    that actually triggered was 12% ago).

    If a directional trigger exists but no origin can be read from
    those trigger agents, state is UNKNOWN — never silently TIMELY.
    """
    if bias not in (LONG, SHORT) or atr_value <= 0:
        return {"state": "TIMELY", "extension_pct": 0.0, "detail": "no directional bias to evaluate extension against"}

    has_trigger = any(_is_trigger_agent(res, bias) for res in agents)
    reference, source_agent, source_label = _pick_origin_from_trigger_agents(agents, price, bias)

    if reference is None:
        if has_trigger:
            detail = ("trigger present but no valid origin on the trigger "
                      "agent(s) — extension cannot be verified; refusing silent TIMELY fallback")
        else:
            detail = "no trigger origin available — extension cannot be verified; refusing silent TIMELY fallback"
        return {
            "state": "UNKNOWN",
            "extension_pct": 0.0,
            "reference_price": None,
            "reference_agent": None,
            "reference_label": None,
            "ratio_to_max": None,
            "detail": detail,
        }

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
    src = source_agent or "trigger"
    lbl = f" ({source_label})" if source_label else ""
    return {
        "state": state, "extension_pct": round(extension_pct, 2),
        "reference_price": round(reference, 2), "ratio_to_max": round(ratio, 2),
        "reference_agent": source_agent,
        "reference_label": source_label or None,
        "detail": (f"{extension_pct:.2f}% from {src} origin {reference:.1f}{lbl} "
                   f"(max allowed {max_extension_pct:.1f}%)"),
    }
