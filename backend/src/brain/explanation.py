"""A4 — Honest reconstructable Brain explanation. No trading-logic changes."""
from typing import Dict, List, Optional
from ..contract import LONG, SHORT, NEUTRAL
from . import evidence as ev

EXPLANATION_VERSION = "BRAIN_EXPLANATION_V1"
_ROLE_ORDER = ("STRUCTURE", "DRIVE", "LOCATION", "PATTERN")
_NAMES = {
    "market_structure": "Market Structure", "breakout": "Breakout",
    "fibonacci": "Fibonacci", "elliott_wave": "Elliott Wave", "volume": "Volume",
    "momentum": "Momentum", "support_resistance": "Support/Resistance",
    "trend": "Trend", "pattern": "Pattern", "fair_value_gap": "FVG",
}
_STRENGTH = ((80, "strong"), (65, "moderate"), (45, "medium"), (0, "weak"))
_PRIMARY = {
    "WAIT_INSUFFICIENT_DATA": "insufficient valid agents",
    "WAIT_CONFLICT": "conflict veto or severe opposing role-mass",
    "NEUTRAL": "consensus inside the neutral band",
    "BIAS_LONG": "setup not yet developed",
    "BIAS_SHORT": "setup not yet developed",
    "SETUP_LONG": "no valid trigger event",
    "SETUP_SHORT": "no valid trigger event",
    "WAIT_POOR_LOCATION": "location quality too weak",
    "WAIT_EXTENDED": "price already extended from trigger origin",
    "WAIT_NO_EXTENSION_REF": "extension origin unknown",
    "WAIT_HTF": "counter-trend HTF gate raised the bar above current consensus",
    "WAIT_LOW_CONFIDENCE": "directional confidence below required",
}


def _name(aid):
    return _NAMES.get(aid, aid)


def _word(score):
    for cut, w in _STRENGTH:
        if score >= cut:
            return w
    return "weak"


def _find(agents, aid):
    for r in agents:
        if r.agent == aid:
            return r
    return None


def _parse_structure(res):
    lines = list(res.evidence or []) if res else []
    blob = " ".join(lines).upper()
    state = ev.extract_state(lines)
    cls = ev.classify_state(state)
    direction = res.direction if res else NEUTRAL
    latest = None
    for tok in ("CHOCH", "CHANGE OF CHARACTER", "BOS", "BREAK OF STRUCTURE"):
        if tok in blob:
            latest = "CHoCH" if "CHOCH" in tok or "CHANGE" in tok else "BOS"
            break
    if latest is None and state and any(k in state.upper() for k in ("TRIGGERED", "REVERSAL", "RECOVERY")):
        latest = state
    seq = []
    if any(p in blob for p in ("HIGHER HIGH", "HH")):
        seq.append("higher-high")
    if any(p in blob for p in ("HIGHER LOW", "HL")):
        seq.append("higher-low")
    if any(p in blob for p in ("LOWER HIGH", "LH")):
        seq.append("lower-high")
    if any(p in blob for p in ("LOWER LOW", "LL")):
        seq.append("lower-low")
    developing = bool(cls.get("context_only") or any(k in blob for k in ("DEVELOPING", "FORMING", "RECOVERY", "RETRACING", "CANDIDATE")))
    contra_seq = (
        (direction == SHORT and ("higher-high" in seq or "higher-low" in seq))
        or (direction == LONG and ("lower-high" in seq or "lower-low" in seq))
    )
    candidate = bool(any(k in blob for k in ("REVERSAL", "RECOVERY", "CHOCH", "CANDIDATE")) or contra_seq)
    confirmed = bool(state and "REVERSAL" in state.upper() and cls.get("trigger"))
    levels = []
    if res:
        for lv in (res.key_levels or []):
            raw = lv.get("price")
            if raw is None:
                continue
            try:
                levels.append({"label": lv.get("label") or None, "price": float(raw), "type": lv.get("type")})
            except (TypeError, ValueError):
                continue
    return {
        "direction": direction, "confidence": round(float(res.confidence), 1) if res else 0.0,
        "state": state, "latest_event": latest, "sequence": seq, "developing": developing,
        "reversal_candidate": candidate and not confirmed, "reversal_confirmed": confirmed,
        "trigger_class": cls, "levels": levels, "evidence_lines": lines,
    }


def _structure_statements(p):
    out = []
    d = p["direction"]
    if d in (LONG, SHORT):
        out.append(f"Current structural direction is {d} ({'bullish' if d == LONG else 'bearish'}).")
    else:
        out.append("Market Structure is currently NEUTRAL.")
    if p["latest_event"]:
        extra = f" (state {p['state']})" if p["state"] else ""
        out.append(f"Latest named structural event: {p['latest_event']}{extra}.")
    elif p["state"]:
        out.append(f"Market Structure state token: {p['state']}.")
    if p["sequence"]:
        out.append("Observed sequence labels: " + ", ".join(p["sequence"]) + ".")
    if p["reversal_confirmed"]:
        out.append("A structural reversal is reported as confirmed by the agent state.")
    elif p["reversal_candidate"] or p["developing"]:
        if d == SHORT:
            out.append("Price may be recovering and a bullish sequence may be developing, but the bearish structural thesis remains active because bullish structural confirmation has not been recorded.")
        elif d == LONG:
            out.append("Price may be pulling back and a bearish sequence may be developing, but the bullish structural thesis remains active because bearish structural confirmation has not been recorded.")
        else:
            out.append("A structural change is developing and is not confirmed.")
    return out


def _role_line(role, rec):
    d = rec.get("direction")
    lean = "bullish" if d == LONG else ("bearish" if d == SHORT else "neutral")
    return f"{role}: {d} — {_word(rec.get('score', 0))} {lean} evidence (score {rec.get('score', 0)})."


def _agent_detail(agents):
    grouped = {r: [] for r in _ROLE_ORDER}
    for res in agents:
        if not res.valid:
            continue
        role = ev.consensus_role(res.agent)
        if role not in grouped:
            continue
        grouped[role].append({
            "agent": res.agent, "name": _name(res.agent), "direction": res.direction,
            "confidence": round(float(res.confidence), 1),
            "state": ev.extract_state(res.evidence),
            "evidence_head": (res.evidence[0] if res.evidence else None),
        })
    return grouped


def _trigger_block(trigger):
    events = []
    for i, e in enumerate(trigger.get("events") or [], 1):
        origin = e.get("origin_price")
        events.append({
            "index": i, "direction": e.get("direction"),
            "origin": "UNKNOWN" if origin is None else origin,
            "origin_unknown": origin is None,
            "sources": [_name(a) for a in e.get("sources") or []],
            "source_ids": list(e.get("sources") or []),
            "corroboration": e.get("corroboration"),
            "states": e.get("states") or [],
            "confidence": e.get("max_confidence"),
        })
    return {
        "state": trigger.get("state"), "score": trigger.get("score"),
        "event_count": trigger.get("event_count", 0), "events": events,
        "detail": trigger.get("detail"),
    }


def _location_timing(decision):
    loc = decision.get("location_detail") or {}
    ext = decision.get("extension_detail") or {}
    origin = ext.get("reference_price")
    unknown = ext.get("state") == "UNKNOWN"
    stmts = []
    if loc.get("detail"):
        stmts.append(loc["detail"])
    if unknown:
        stmts.append("Extension origin: UNKNOWN. Entry is blocked because extension cannot be safely evaluated. No reference price is assumed.")
    elif origin is not None:
        stmts.append(f"Extension origin: {origin} from {ext.get('reference_agent') or 'trigger agent'} ({ext.get('state')}, {ext.get('extension_pct')}%).")
    elif ext.get("detail"):
        stmts.append(ext["detail"])
    return {
        "location_score": decision.get("location_score"),
        "timing_state": decision.get("timing_state"),
        "extension_state": decision.get("extension_state"),
        "extension_origin": "UNKNOWN" if unknown else origin,
        "extension_origin_unknown": unknown,
        "zone_price": loc.get("zone_price"),
        "distance_pct": loc.get("distance_pct"),
        "statements": stmts,
    }


def _rank_blockers(decision):
    state = decision.get("decision_state")
    existing = list(decision.get("blocking_reasons") or [])
    primary = None if (state and str(state).startswith("FIRE_")) else _PRIMARY.get(state)
    if primary is None and existing:
        primary = existing[0]
    secondary = existing if (primary and primary not in existing and existing) else [b for b in existing if b != primary]
    return {"primary": primary, "secondary": secondary, "all": existing, "decision_state": state}


def _why_fire(decision):
    if not str(decision.get("decision_state") or "").startswith("FIRE_"):
        return None
    trig = decision.get("trigger_detail") or {}
    cf = decision.get("conflict") or {}
    gate = decision.get("htf_gate") or {}
    return [
        f"Directional consensus passed threshold: {decision.get('consensus_score'):+.1f} >= required {decision.get('entry_score_required'):+.1f}.",
        f"Directional confidence passed threshold: {decision.get('confidence'):.1f} >= required {decision.get('entry_confidence_required'):.1f}.",
        f"Setup is {decision.get('setup_state')} (score {decision.get('setup_score')}).",
        f"Valid trigger event count: {trig.get('event_count', 0)} (score {decision.get('trigger_score')}).",
        f"Location score: {decision.get('location_score')}.",
        f"Extension: {decision.get('extension_state')} (not UNKNOWN / not EXTENDED).",
        f"Conflict severity: {cf.get('severity')} (veto={cf.get('veto')}).",
        f"HTF gate relation: {gate.get('relation')} — trade permitted.",
        f"Brain confidence: {decision.get('brain_confidence')} (separate from consensus).",
    ]


def _why_wait(decision, blockers):
    if str(decision.get("decision_state") or "").startswith("FIRE_"):
        return None
    if decision.get("state") not in ("WAIT", "AVOID"):
        return None
    ds = decision.get("decision_state")
    lines = [f"Decision: {decision.get('state')} ({ds})."]
    if blockers.get("primary"):
        lines.append(f"PRIMARY BLOCKER: {blockers['primary']}.")
    if ds == "NEUTRAL":
        lines.append(f"Consensus: {decision.get('consensus_score'):+.1f} (direction threshold uses |consensus| vs {decision.get('entry_score_required')}).")
    if ds in ("BIAS_LONG", "BIAS_SHORT"):
        lines.append(f"Setup is {decision.get('setup_state')} ({decision.get('setup_score')}); setup is the blocker, not the trigger.")
    if ds in ("SETUP_LONG", "SETUP_SHORT"):
        lines.append(f"Setup is {decision.get('setup_state')} ({decision.get('setup_score')}), but no valid trigger event is currently confirmed.")
    if ds == "WAIT_NO_EXTENSION_REF":
        lines.append("Extension origin: UNKNOWN. The Brain cannot safely determine how far price has already travelled. No reference price is assumed.")
    if ds == "WAIT_EXTENDED":
        lines.append((decision.get("extension_detail") or {}).get("detail") or "Price is extended from the trigger origin.")
    if "CONFLICT" in str(ds) or (decision.get("conflict") or {}).get("veto"):
        cf = decision.get("conflict") or {}
        lines.append(f"Opposing role-mass ratio {cf.get('opposing_ratio')} (opposing roles: {', '.join(cf.get('opposing_roles') or []) or 'none'}).")
    if ds == "WAIT_LOW_CONFIDENCE":
        lines.append(f"Directional confidence {decision.get('confidence')} < required {decision.get('entry_confidence_required')}.")
    if ds == "WAIT_HTF":
        lines.append(f"Consensus {abs(decision.get('consensus_score') or 0):.1f} < HTF-adjusted required {decision.get('entry_score_required')}.")
    for extra in blockers.get("secondary") or []:
        lines.append(f"SECONDARY: {extra}.")
    return lines


def _invalidation(decision, parsed):
    bias = decision.get("direction")
    if bias not in (LONG, SHORT):
        return {"direction": bias, "level": "UNKNOWN", "conditions": ["No directional thesis to invalidate."]}
    levels = parsed.get("levels") or []
    conditions = []
    pick = None
    if bias == LONG:
        supports = [lv for lv in levels if str(lv.get("type") or "").lower() in ("support", "bos", "break")]
        pick = supports[0] if supports else (levels[0] if levels else None)
        conditions = [
            "Bearish structural break below the relevant bullish structural level.",
            "Bullish setup failure.",
            "Trigger failure or expiry of the current long event.",
        ]
    else:
        resists = [lv for lv in levels if str(lv.get("type") or "").lower() in ("resistance", "bos", "break")]
        pick = resists[0] if resists else (levels[0] if levels else None)
        conditions = [
            "Bullish structural break above the relevant bearish structural level.",
            "Bearish setup failure.",
            "Trigger failure or expiry of the current short event.",
        ]
    if pick:
        conditions.insert(0, f"Published structural reference: {pick.get('label') or 'level'} @ {pick['price']}.")
        level = pick["price"]
    else:
        conditions.append("Invalidation level: UNKNOWN (no published structural price on Market Structure).")
        level = "UNKNOWN"
    cf = decision.get("conflict") or {}
    if cf.get("opposing_roles"):
        conditions.append("Role-level opposition from: " + ", ".join(cf["opposing_roles"]) + ".")
    return {"direction": bias, "level": level, "conditions": conditions}


def _htf_line(decision):
    regime = (decision.get("htf_regime") or {}).get("regime", NEUTRAL)
    rel = (decision.get("htf_gate") or {}).get("relation")
    bias = decision.get("direction")
    ctx = "HTF context: neutral." if regime == NEUTRAL else ("HTF context: bullish." if regime == LONG else "HTF context: bearish.")
    if bias == NEUTRAL:
        return f"{ctx} Current timeframe has no directional thesis."
    if rel == "aligned":
        return f"{ctx} Current timeframe evidence is aligned with HTF."
    if rel == "counter-trend":
        return f"{ctx} Current timeframe evidence is {bias}. Counter-trend condition: permitted with a raised bar (existing HTF gate), not auto-blocked."
    return f"{ctx} HTF relation: {rel}."


def _overall(roles):
    if not roles:
        return "No directional role evidence."
    longs = [r for r, rec in roles.items() if rec.get("direction") == LONG]
    shorts = [r for r, rec in roles.items() if rec.get("direction") == SHORT]
    if longs and not shorts:
        return "Overall role-level evidence favors LONG."
    if shorts and not longs:
        return "Overall role-level evidence favors SHORT."
    if longs and shorts:
        return "Overall evidence is contested: " + ", ".join(f"{r} {roles[r]['direction']}" for r in _ROLE_ORDER if r in roles) + "."
    return "Role-level evidence is present but not one-sided."


def build_explanation(decision: Dict, agents: List) -> Dict:
    roles = decision.get("role_consensus") or {}
    parsed = _parse_structure(_find(agents, "market_structure"))
    trend = _find(agents, "trend")
    blockers = _rank_blockers(decision)
    bias = decision.get("direction")
    supporting, opposing = [], []
    for role in _ROLE_ORDER:
        rec = roles.get(role)
        if not rec:
            continue
        if rec.get("direction") == bias and bias in (LONG, SHORT):
            supporting.append(role)
        elif rec.get("direction") in (LONG, SHORT) and rec.get("direction") != bias:
            opposing.append(role)
    thesis = {
        "bias": bias, "consensus": decision.get("consensus_score"),
        "brain_confidence": decision.get("brain_confidence"),
        "directional_confidence": decision.get("confidence"),
        "roles": [_role_line(r, roles[r]) for r in _ROLE_ORDER if r in roles],
        "supporting_roles": supporting, "opposing_roles": opposing,
        "overall": _overall(roles),
    }
    return {
        "explanation_version": EXPLANATION_VERSION,
        "market_state": {
            "direction": bias,
            "regime": (decision.get("htf_regime") or {}).get("regime"),
            "consensus_band": decision.get("directional_consensus_band"),
            "structure": parsed,
            "trend_direction": trend.direction if trend else None,
            "trend_confidence": round(float(trend.confidence), 1) if trend else None,
            "htf": _htf_line(decision),
            "statements": _structure_statements(parsed),
        },
        "directional_thesis": thesis,
        "agent_detail": _agent_detail(agents),
        "setup": {
            "state": decision.get("setup_state"), "score": decision.get("setup_score"),
            "roles_aligned": (decision.get("setup_detail") or {}).get("roles_aligned"),
            "roles_opposed": (decision.get("setup_detail") or {}).get("roles_opposed"),
            "detail": (decision.get("setup_detail") or {}).get("detail"),
        },
        "trigger": _trigger_block(decision.get("trigger_detail") or {}),
        "location_timing": _location_timing(decision),
        "supporting_roles": supporting,
        "blocking": blockers,
        "invalidation": _invalidation(decision, parsed),
        "why_fire": _why_fire(decision),
        "why_wait": _why_wait(decision, blockers),
        "final": {
            "state": decision.get("state"), "decision_state": decision.get("decision_state"),
            "direction": bias, "consensus_score": decision.get("consensus_score"),
            "brain_confidence": decision.get("brain_confidence"),
            "directional_confidence": decision.get("confidence"),
        },
    }
