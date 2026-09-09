"""PHASE G — BRAIN V2 (decision interpretation layer).

Consumes the standardized outputs of the 10 agents plus the HTF regime
and produces exactly one legacy state — LONG / SHORT / WAIT / AVOID —
with a fully traceable reasoning chain. The external contract (every
field the old Brain returned, and the exact meaning of `state`) is
unchanged: autotrader.py gates real trade execution on
`brain["state"] in ("LONG", "SHORT")` via a literal string match
(confirmed by direct inspection), so that value can never change shape.

What changed is the INTERPRETATION underneath it. The old Brain's core
bug (confirmed by direct inspection, and exactly matching this task's
own spec section 44): confidence was computed as
`|consensus| + confluence_bonus - conflict_penalty` — when strong LONG
and SHORT evidence roughly cancel out, |consensus| collapses toward
zero, and so did "confidence," even though real, strong evidence
existed on BOTH sides. Brain V2 keeps these as genuinely separate
numbers:

    directional_confidence  — confidence in the DIRECTION itself
    setup_score              — is a real, role-diverse setup forming
    trigger_score             — has an ACTUAL actionable event happened
    location_score            — is the trigger happening somewhere meaningful
    extension                 — is it still timely, or already chased
    brain_confidence          — confidence in THIS decision, which can be
                                 HIGH even during a contested WAIT (spec
                                 section 45's own worked example) — a lot
                                 of strong evidence on both sides is a
                                 confidently-contested market, not "no
                                 signal."

Elliott Wave is explicitly excluded from consensus/conflict/setup/
trigger math (spec section 2 — research/diagnostic only, zero
execution influence) but still appears in `contributions`/evidence for
UI and diagnostics.

Weighted evidence formula (spec section 5, already correct in the
prior version, kept unchanged and now explicitly documented + Elliott-
excluded):

    directional_evidence = direction_sign(agent) × agent.confidence × agent_weight
    base_consensus = Σ(directional_evidence) / Σ(agent_weight)   [-100..+100]

Agent logic, agent weights, Entry/Conflict/HTF numeric values, and
Execution Manager are all completely unchanged — only interpreted
differently, exactly as required.
"""
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

EXCLUDE_FROM_EXECUTION = scoring.EXCLUDE_FROM_EXECUTION  # ("elliott_wave",)

# Directional consensus bands (spec section 8) — deliberately separate
# from the Entry thresholds (direction_min_score etc.), which answer a
# different question ("should we trade") not ("which way does the
# evidence lean, and how strongly"). Illustrative starting values, same
# caveat as every threshold anywhere else in this codebase.
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
    """Base Consensus Score = Σ(direction × confidence × weight) / Σ weights.
    Elliott Wave excluded — spec section 2, zero execution influence."""
    num = 0.0
    den = 0.0
    contributions = []
    active = 0
    AGENT_WEIGHTS = weights_override if weights_override is not None else settings.weights()
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
            score = res.sign() * res.confidence * w
            num += score
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
        })
    base = num / den if den else 0.0
    return {"base_consensus": round(base, 1), "active_agents": active,
            "sum_weights": round(den, 2), "contributions": contributions,
            "long_evidence": round(long_evidence, 1), "short_evidence": round(short_evidence, 1),
            "neutral_agents": neutral_agents}


def _brain_confidence(bias: str, consensus: float, base: Dict, conflict: Dict,
                      setup: Dict, trigger: Dict, location: Dict, extension: Dict,
                      gate: Dict) -> float:
    """Confidence in THIS decision — not a simple average of the pieces
    above, and NOT the same thing as directional strength (spec
    sections 21-22, 44-45). The NEUTRAL branch is the direct fix for
    the documented bug: a market with strong evidence on BOTH sides
    should read as confidently-contested, not "no signal" just because
    it nets to zero.
    """
    if bias == NEUTRAL:
        long_e, short_e = base["long_evidence"], base["short_evidence"]
        total_evidence = long_e + short_e
        n_directional = max(base["active_agents"] - len(base["neutral_agents"]), 1)
        avg_evidence = total_evidence / n_directional
        # How genuinely two-sided is this, independent of conflict.py's
        # own veto/severe thresholds (which exist for a different
        # purpose — deciding AVOID). A market with strong evidence on
        # BOTH sides is confidently contested even if it doesn't cross
        # those specific bureaucratic bars — that raw balance is the
        # actual signal spec section 45 cares about.
        balance = 1.0 - abs(long_e - short_e) / max(total_evidence, 1e-9)  # 1.0 = perfectly balanced
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
        clamp(abs(consensus), 0, 100) * 0.25
        + setup["score"] * 0.20
        + trigger["score"] * 0.20
        + location["score"] * 0.15
        + max(0.0, 100.0 - conflict_penalty_component * 2.5) * 0.10
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

    # PHASE F — directional bias (unchanged mechanism/thresholds)
    if consensus >= ENTRY["direction_min_score"]:
        bias = LONG
    elif consensus <= -ENTRY["direction_min_score"]:
        bias = SHORT
    else:
        bias = NEUTRAL

    # PHASE D — confluence (unchanged)
    zones = find_confluence_zones(agents, price)
    confluence_bonus = sum(z["bonus"] for z in zones[:3])

    # PHASE E — conflict. detect_conflict() itself has no Elliott-Wave
    # awareness, so the exclusion has to happen here, on the agent list
    # passed in — filtering before the call, not inside conflict.py,
    # keeps that file completely untouched per the "small controlled
    # changes" mandate.
    execution_agents = [a for a in agents if a.agent not in EXCLUDE_FROM_EXECUTION]
    conflict = detect_conflict(execution_agents, bias, weights_override, conflict_override)

    # Directional confidence — this IS what the old "confidence" field
    # measured. Kept, but no longer overloaded as Brain's overall
    # decision confidence (see brain_confidence below).
    directional_confidence = clamp(abs(consensus) + confluence_bonus - conflict["penalty"], 0, 100)

    # PHASE C — HTF gate (unchanged)
    regime = htf.get("regime", NEUTRAL)
    gate = apply_gate(bias, regime, htf_gate_override)
    entry_score_req = ENTRY["entry_min_score"] + gate["extra_score_required"]
    entry_conf_req = ENTRY["entry_min_confidence"] + gate["extra_confidence_required"]

    # --- NEW: setup / trigger / location / extension (spec sections 13-19) ---
    setup = scoring.setup_score(agents, bias)
    trigger = scoring.trigger_score(agents, bias)
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
        reasons.append(f"{_name(r.agent)}: {r.direction} {r.confidence:.0f}% — {head}")
    for z in zones[:2]:
        reasons.append(f"Confluence zone @ {z['price']:.1f} ({', '.join(_name(a) for a in z['agents'])}) +{z['bonus']}")
    reasons.append(f"HTF regime: {regime} ({htf.get('regime_score', 0):+.0f}) → {gate['relation']}")

    # Primary vs supporting evidence (spec section 29) — primary is
    # whichever agents report an actual TRIGGER; everything else aligned
    # is supporting context.
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

    return {
        "state": state,                    # LONG | SHORT | WAIT | AVOID — UNCHANGED CONTRACT,
        # autotrader.py's execution gate depends on this exact value/shape.
        "direction": bias,
        "consensus_score": consensus,
        "confidence": round(directional_confidence, 1),   # kept for backward compat — this is
        # DIRECTIONAL confidence specifically, see brain_confidence for the Brain's overall
        # decision confidence (spec sections 4, 21-22, 44).
        "base_consensus": consensus,
        "confluence_bonus": round(confluence_bonus, 1),
        "confluence_zones": zones,
        "conflict": conflict,
        "htf_gate": gate,
        "htf_regime": htf,
        "entry_score_required": round(entry_score_req, 1),
        "entry_confidence_required": round(entry_conf_req, 1),
        "reasons": reasons,
        "why_state": why,
        "contributions": base["contributions"],
        "active_agents": base["active_agents"],
        "sum_weights": base["sum_weights"],
        "timeframe": timeframe,
        "config_version": CONFIG_VERSION,
        "assumptions": ASSUMPTIONS,

        # --- New Brain V2 fields (additive, backward compatible) ---
        "directional_consensus_band": consensus_band,
        "long_evidence": base["long_evidence"],
        "short_evidence": base["short_evidence"],
        "neutral_agents": base["neutral_agents"],
        "setup_state": setup["state"],
        "setup_score": setup["score"],
        "setup_detail": setup,
        "trigger_state": trigger["state"],
        "trigger_score": trigger["score"],
        "trigger_detail": trigger,
        "location_score": location["score"],
        "location_detail": location,
        "timing_state": extension["state"],
        "extension_state": extension["state"],
        "extension_detail": extension,
        "brain_confidence": brain_confidence,
        "entry_readiness": state in (STATE_LONG, STATE_SHORT),
        "decision_state": decision_state,   # rich internal state (25-value machine) —
        # ADDITIVE ONLY, `state` above remains the legacy 4-value contract.
        "primary_evidence": primary_evidence,
        "supporting_evidence": supporting_evidence,
        "blocking_reasons": blocking,
        "brain_version": "BRAIN_V2",
    }


def _resolve_state(bias, consensus, directional_confidence, brain_confidence, active, conflict, gate,
                   score_req, conf_req, regime, setup, trigger, location, extension, entry_override=None):
    """A genuine step-by-step walk through the decision hierarchy (spec
    section 23) — each step can be the one that stops progress, and the
    internal decision_state reflects exactly which step that was. The
    external `state` (LONG/SHORT/WAIT/AVOID) is derived from this but
    stays the unchanged 4-value contract autotrader.py depends on."""
    ENTRY = entry_override if entry_override is not None else settings.entry()
    why = []
    blocking = []

    # STEP 1-2: data validity / valid agent count
    if active < ENTRY["min_valid_agents"]:
        msg = f"Only {active} valid agents (min {ENTRY['min_valid_agents']}) — poor conditions"
        why.append(msg); blocking.append(msg)
        return STATE_AVOID, "WAIT_INSUFFICIENT_DATA", why, blocking

    # STEP 4: conflict — hard vetoes only; moderate conflict never
    # auto-vetoes (spec section 11)
    if conflict["veto"]:
        msg = f"Hard veto: opposing evidence ratio {conflict['opposing_ratio']:.0%}"
        why.append(msg); blocking.append(msg)
        return STATE_AVOID, "WAIT_CONFLICT", why, blocking
    if conflict["severity"] == "severe":
        msg = f"Severe two-sided conflict ({', '.join(conflict['opposing_agents'])})"
        why.append(msg); blocking.append(msg)
        return STATE_AVOID, "WAIT_CONFLICT", why, blocking

    # STEP 3: directional consensus
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

    # STEP 6: setup formation — a directional lean exists, but has it
    # actually developed into a real, role-diverse setup yet?
    if setup["state"] in ("NO_SETUP", "SETUP_FORMING"):
        why.append(f"{bias} directional lean present (consensus {consensus:+.0f}) but setup "
                  f"only {setup['state']} ({setup['score']:.0f}/100) — {setup['detail']}")
        blocking.append("setup not yet developed")
        return STATE_WAIT, f"BIAS_{bias_word}", why, blocking

    # STEP 7: entry trigger — setup exists, but has an actual actionable
    # event happened? (spec sections 13-16 — this is the core setup vs
    # trigger distinction)
    if trigger["score"] <= 0:
        why.append(f"{bias} setup {setup['state']} (score {setup['score']:.0f}) but no actionable "
                  f"trigger has occurred yet — {trigger['detail']}")
        blocking.append("no valid entry trigger")
        return STATE_WAIT, f"SETUP_{bias_word}", why, blocking

    # STEP 8: location — is the trigger happening somewhere meaningful?
    if location["score"] < 35:
        why.append(f"{bias} trigger valid ({trigger['detail']}) but location quality is weak "
                  f"({location['score']:.0f}/100) — {location.get('detail', '')}")
        blocking.append("poor location")
        return STATE_WAIT, "WAIT_POOR_LOCATION", why, blocking

    # STEP 9: timing / extension — genuinely new logic (max_extension_pct
    # previously unused anywhere in the codebase, confirmed by direct
    # inspection)
    if extension["state"] == "EXTENDED":
        why.append(f"{bias} trigger valid but price already extended — {extension.get('detail', '')}")
        blocking.append("extended")
        return STATE_WAIT, "WAIT_EXTENDED", why, blocking

    # HTF as an explicit gating step (spec section 12) — counter-trend
    # is allowed, never a hard block, but called out on its own when
    # it's specifically the reason the entry bar isn't cleared yet.
    htf_is_blocking = gate["relation"] == "counter-trend" and mag < score_req

    # STEP 10-11: Brain confidence + entry score/confidence thresholds
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

    # Setup + trigger + location + timing all pass, but entry
    # thresholds not yet met → WAIT, with the SPECIFIC blocking reason
    # identified rather than a generic catch-all.
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