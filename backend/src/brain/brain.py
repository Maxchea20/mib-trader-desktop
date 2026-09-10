"""PHASE G — BRAIN V2 (decision interpretation layer)."""
from typing import List, Dict, Optional
from ..config import CONFIG_VERSION, ASSUMPTIONS
from .. import settings
from ..contract import (LONG, SHORT, NEUTRAL, STATE_LONG, STATE_SHORT,
                        STATE_WAIT, STATE_AVOID, clamp)
from .confluence import find_confluence_zones
from .conflict import detect_conflict
from .mtf import apply_gate
from . import evidence as ev
from . import scoring
from .explanation import build_explanation, EXPLANATION_VERSION

EXCLUDE_FROM_EXECUTION = scoring.EXCLUDE_FROM_EXECUTION
_CONSENSUS_BANDS = [
    (60, "STRONG_LONG"), (30, "LONG"), (10, "WEAK_LONG"),
    (-10, "NEUTRAL"), (-30, "WEAK_SHORT"), (-60, "SHORT"),
]

def _consensus_band(consensus: float) -> str:
    for threshold, label in _CONSENSUS_BANDS:
        if consensus >= threshold:
            return label
    return "STRONG_SHORT"

def _base_consensus(agents: List, weights_override: Dict = None) -> Dict:
    AGENT_WEIGHTS = weights_override if weights_override is not None else settings.weights()
    contributions = []
    active = 0
    den = 0.0
    long_evidence = 0.0
    short_evidence = 0.0
    neutral_agents = []
    for res in agents:
        w = AGENT_WEIGHTS.get(res.agent, 1.0)
        excluded = res.agent in EXCLUDE_FROM_EXECUTION
        if not res.valid:
            continue
        if res.direction == NEUTRAL:
            neutral_agents.append(res.agent)
        if not excluded:
            active += 1
            den += w
            if res.direction == LONG:
                long_evidence += res.confidence * w
            elif res.direction == SHORT:
                short_evidence += res.confidence * w
        contributions.append({
            "agent": res.agent, "direction": res.direction,
            "confidence": round(res.confidence, 1), "weight": w,
            "contribution": round(res.sign() * res.confidence * w, 1) if not excluded else 0.0,
            "execution_influence": not excluded,
            "consensus_role": ev.consensus_role(res.agent) if not excluded else "EXCLUDED",
        })
    net = ev.role_net_evidence(agents, AGENT_WEIGHTS, exclude_agents=EXCLUDE_FROM_EXECUTION)
    return {"base_consensus": net["consensus"], "active_agents": active,
            "sum_weights": round(den, 2), "contributions": contributions,
            "long_evidence": round(long_evidence, 1), "short_evidence": round(short_evidence, 1),
            "neutral_agents": neutral_agents, "role_net": net}

def _brain_confidence(bias, consensus, base, conflict, setup, trigger, location, extension, gate):
    if bias == NEUTRAL:
        long_e, short_e = base["long_evidence"], base["short_evidence"]
        total_evidence = long_e + short_e
        n_directional = max(base["active_agents"] - len(base["neutral_agents"]), 1)
        avg_evidence = total_evidence / n_directional
        balance = 1.0 - abs(long_e - short_e) / max(total_evidence, 1e-9)
        conflict_component = 100.0 if conflict.get("veto") else (
            75.0 if conflict.get("severity") == "severe" else
            45.0 if conflict.get("severity") == "moderate" else 0.0)
        contested_component = clamp(avg_evidence, 0, 100) * balance
        return round(clamp(contested_component * 0.7 + conflict_component * 0.3, 0, 94), 1)
    conflict_penalty_component = clamp(conflict.get("penalty", 0.0) * 1.5, 0, 40)
    htf_component = 10.0 if gate.get("relation") == "aligned" else (
        -8.0 if gate.get("relation") == "counter-trend" else 0.0)
    extension_penalty = {"EARLY": 0.0, "TIMELY": 0.0, "LATE": -6.0, "EXTENDED": -18.0}.get(
        extension.get("state", "TIMELY"), 0.0)
    score = (
        clamp(abs(consensus), 0, 100) * 0.25 + setup["score"] * 0.20 + trigger["score"] * 0.20
        + location["score"] * 0.15 + max(0.0, 100.0 - conflict_penalty_component * 2.5) * 0.10
        + clamp(50 + htf_component, 0, 100) * 0.10
    )
    score += extension_penalty
    return round(clamp(score, 0, 94), 1)

def decide(agents: List, price: float, timeframe: str, htf: Dict, weights_override: Dict = None,
           entry_override: Dict = None, conflict_override: Dict = None,
           htf_gate_override: Dict = None, atr_value: Optional[float] = None) -> Dict:
    AGENT_WEIGHTS = weights_override if weights_override is not None else settings.weights()
    ENTRY = entry_override if entry_override is not None else settings.entry()
    base = _base_consensus(agents, weights_override)
    consensus = base["base_consensus"]
    consensus_band = _consensus_band(consensus)
    if consensus >= ENTRY["direction_min_score"]:
        bias = LONG
    elif consensus <= -ENTRY["direction_min_score"]:
        bias = SHORT
    else:
        bias = NEUTRAL
    zones = find_confluence_zones(agents, price)
    confluence_bonus = sum(z["bonus"] for z in zones[:3])
    execution_agents = [a for a in agents if a.agent not in EXCLUDE_FROM_EXECUTION]
    conflict = detect_conflict(execution_agents, bias, weights_override, conflict_override)
    directional_confidence = clamp(abs(consensus) + confluence_bonus - conflict["penalty"], 0, 100)
    regime = htf.get("regime", NEUTRAL)
    gate = apply_gate(bias, regime, htf_gate_override)
    entry_score_req = ENTRY["entry_min_score"] + gate["extra_score_required"]
    entry_conf_req = ENTRY["entry_min_confidence"] + gate["extra_confidence_required"]
    setup = scoring.setup_score(agents, bias)
    trigger = scoring.trigger_score(agents, bias, atr_value=atr_value, price=price)
    location = scoring.location_score(agents, price, bias, zones)
    max_ext = ENTRY.get("max_extension_pct", 1.2)
    if atr_value:
        extension = scoring.extension_timing(agents, price, bias, atr_value, max_ext)
    else:
        extension = {"state": "TIMELY", "extension_pct": 0.0,
                     "detail": "ATR not supplied to this call — extension not evaluated, assumed timely"}
    brain_confidence = _brain_confidence(bias, consensus, base, conflict, setup, trigger, location, extension, gate)
    reasons = []
    aligned = sorted([r for r in agents if r.valid and r.direction == bias and bias != NEUTRAL
                      and r.agent not in EXCLUDE_FROM_EXECUTION],
                     key=lambda r: r.confidence * AGENT_WEIGHTS.get(r.agent, 1), reverse=True)
    for r in aligned[:6]:
        head = r.evidence[0] if r.evidence else r.direction
        reasons.append(f"{_name(r.agent)}: direction {r.direction} {r.confidence:.0f}% | evidence: {head}")
    for z in zones[:2]:
        reasons.append(f"Confluence zone @ {z['price']:.1f} ({', '.join(_name(a) for a in z['agents'])}) +{z['bonus']}")
    reasons.append(f"HTF regime: {regime} ({htf.get('regime_score', 0):+.0f}) → {gate['relation']}")
    primary_agents = {p.split(" (")[0] for p in trigger.get("primary", [])}
    primary_evidence = [f"{_name(a)}: {r.evidence[0] if r.evidence else r.direction}"
                        for a in primary_agents for r in agents if r.agent == a]
    supporting_evidence = [f"{_name(r.agent)}: {r.direction} {r.confidence:.0f}%"
                           for r in aligned if r.agent not in primary_agents][:5]
    state, decision_state, why, blocking = _resolve_state(
        bias, consensus, directional_confidence, brain_confidence, base["active_agents"],
        conflict, gate, entry_score_req, entry_conf_req, regime, setup, trigger, location,
        extension, entry_override,
    )
    result = {
        "state": state, "direction": bias, "consensus_score": consensus,
        "confidence": round(directional_confidence, 1), "base_consensus": consensus,
        "confluence_bonus": round(confluence_bonus, 1), "confluence_zones": zones,
        "conflict": conflict, "htf_gate": gate, "htf_regime": htf,
        "entry_score_required": round(entry_score_req, 1),
        "entry_confidence_required": round(entry_conf_req, 1),
        "reasons": reasons, "why_state": why, "contributions": base["contributions"],
        "active_agents": base["active_agents"], "sum_weights": base["sum_weights"],
        "timeframe": timeframe, "config_version": CONFIG_VERSION, "assumptions": ASSUMPTIONS,
        "directional_consensus_band": consensus_band,
        "long_evidence": base["long_evidence"], "short_evidence": base["short_evidence"],
        "neutral_agents": base["neutral_agents"],
        "role_consensus": base["role_net"]["roles"],
        "role_net_contributions": base["role_net"]["contributions"],
        "long_role_evidence": base["role_net"]["long_role_evidence"],
        "short_role_evidence": base["role_net"]["short_role_evidence"],
        "setup_state": setup["state"], "setup_score": setup["score"], "setup_detail": setup,
        "trigger_state": trigger["state"], "trigger_score": trigger["score"], "trigger_detail": trigger,
        "location_score": location["score"], "location_detail": location,
        "timing_state": extension["state"], "extension_state": extension["state"],
        "extension_detail": extension, "brain_confidence": brain_confidence,
        "entry_readiness": state in (STATE_LONG, STATE_SHORT),
        "decision_state": decision_state, "primary_evidence": primary_evidence,
        "supporting_evidence": supporting_evidence, "blocking_reasons": blocking,
        "brain_version": "BRAIN_V2",
    }
    explanation = build_explanation(result, agents)
    result["explanation"] = explanation
    result["explanation_version"] = EXPLANATION_VERSION
    result["thesis"] = explanation["directional_thesis"]
    result["invalidation"] = explanation["invalidation"]
    return result

def _resolve_state(bias, consensus, directional_confidence, brain_confidence, active, conflict, gate,
                   score_req, conf_req, regime, setup, trigger, location, extension, entry_override=None):
    ENTRY = entry_override if entry_override is not None else settings.entry()
    why = []
    blocking = []
    if active < ENTRY["min_valid_agents"]:
        msg = f"Only {active} valid agents (min {ENTRY['min_valid_agents']}) — poor conditions"
        why.append(msg); blocking.append(msg)
        return STATE_AVOID, "WAIT_INSUFFICIENT_DATA", why, blocking
    if conflict["veto"]:
        msg = f"Hard veto: opposing evidence ratio {conflict['opposing_ratio']:.0%}"
        why.append(msg); blocking.append(msg)
        return STATE_AVOID, "WAIT_CONFLICT", why, blocking
    if conflict["severity"] == "severe":
        msg = f"Severe two-sided conflict ({', '.join(conflict['opposing_agents'])})"
        why.append(msg); blocking.append(msg)
        return STATE_AVOID, "WAIT_CONFLICT", why, blocking
    if bias == NEUTRAL:
        contested = conflict.get("conflict") or conflict.get("opposing_ratio", 0.0) >= 0.35
        if contested:
            why.append("Strong opposing evidence on both sides creates high conflict "
                      f"(consensus {consensus:+.0f}, contested rather than absent)")
        else:
            why.append(f"No directional bias (consensus {consensus:+.0f} within neutral band)")
        blocking.append("no directional consensus")
        return STATE_WAIT, "NEUTRAL", why, blocking
    mag = abs(consensus)
    bias_word = "LONG" if bias == LONG else "SHORT"
    if setup["state"] in ("NO_SETUP", "SETUP_FORMING"):
        why.append(f"{bias} directional lean present (consensus {consensus:+.0f}) but setup "
                  f"only {setup['state']} ({setup['score']:.0f}/100) — {setup['detail']}")
        blocking.append("setup not yet developed")
        return STATE_WAIT, f"BIAS_{bias_word}", why, blocking
    if trigger["score"] <= 0:
        why.append(f"{bias} setup {setup['state']} (score {setup['score']:.0f}) but no actionable "
                  f"trigger has occurred yet — {trigger['detail']}")
        blocking.append("no valid entry trigger")
        return STATE_WAIT, f"SETUP_{bias_word}", why, blocking
    if location["score"] < 35:
        why.append(f"{bias} trigger valid ({trigger['detail']}) but location quality is weak "
                  f"({location['score']:.0f}/100) — {location.get('detail', '')}")
        blocking.append("poor location")
        return STATE_WAIT, "WAIT_POOR_LOCATION", why, blocking
    if extension["state"] == "EXTENDED":
        why.append(f"{bias} trigger valid but price already extended — {extension.get('detail', '')}")
        blocking.append("extended")
        return STATE_WAIT, "WAIT_EXTENDED", why, blocking
    if extension["state"] == "UNKNOWN":
        why.append(f"{bias} trigger has no verifiable origin — {extension.get('detail', '')}")
        blocking.append("extension origin unknown")
        return STATE_WAIT, "WAIT_NO_EXTENSION_REF", why, blocking
    htf_is_blocking = gate["relation"] == "counter-trend" and mag < score_req
    ready = mag >= score_req and directional_confidence >= conf_req
    if ready:
        why.append(f"Consensus {consensus:+.0f} ≥ required {score_req:.0f}")
        why.append(f"Directional confidence {directional_confidence:.0f}% ≥ required {conf_req:.0f}%")
        why.append(f"Valid {bias} trigger: {trigger['detail']}")
        why.append(f"Setup quality {setup['score']:.0f} ({setup['state']}), location {location['score']:.0f}, "
                  f"Brain confidence {brain_confidence:.0f}%")
        if gate["relation"] == "aligned":
            why.append(f"Aligned with HTF regime ({regime})")
        elif gate["relation"] == "counter-trend":
            why.append(f"Counter-trend to HTF regime ({regime}) — passed the higher bar anyway")
        return (STATE_LONG if bias == LONG else STATE_SHORT), f"FIRE_{bias_word}", why, blocking
    if htf_is_blocking:
        why.append(f"Counter-trend to HTF regime ({regime}) — higher bar applied "
                  f"(required {score_req:.0f}, have {mag:.0f})")
        blocking.append("counter-trend HTF gate")
        return STATE_WAIT, "WAIT_HTF", why, blocking
    if mag < score_req:
        msg = f"{bias} bias but consensus strength {mag:.0f} < required {score_req:.0f}"
        why.append(msg); blocking.append(msg)
    if directional_confidence < conf_req:
        msg = f"Directional confidence {directional_confidence:.0f}% < required {conf_req:.0f}%"
        why.append(msg); blocking.append(msg)
        return STATE_WAIT, "WAIT_LOW_CONFIDENCE", why, blocking
    if conflict["severity"] == "moderate":
        why.append(f"Moderate conflict ({', '.join(conflict['opposing_agents'])}) reduced confidence")
    return STATE_WAIT, f"VALIDATING_{bias_word}", why, blocking

_NAMES = {
    "market_structure": "Structure", "breakout": "Breakout", "fibonacci": "Fibonacci",
    "elliott_wave": "Elliott Wave", "volume": "Volume", "momentum": "Momentum",
    "support_resistance": "Support/Resistance", "trend": "Trend",
    "pattern": "Pattern", "fair_value_gap": "FVG",
}

def _name(agent_id: str) -> str:
    return _NAMES.get(agent_id, agent_id)
