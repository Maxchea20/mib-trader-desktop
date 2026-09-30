"""Read-only diagnosis of the AI's own behaviour: malformed or unsafe proposals, contradictions between what GPT said and what it did,
scalp-like geometry, flip-flopping, slow or failed calls, and exits that did not match its stated thesis.  Nothing here changes trading."""
import statistics
from collections import Counter
from typing import Any, Dict, List

from . import store
from .schema import AI_EXIT_REASONS

SCALP_TARGET_PCT = 1.5          # a swing target is expected to be further away than this
SLOW_CALL_SECONDS = 60
FLIP_MINUTES = 60
STALE_FILL_PCT = 0.10


def _pct(a, b):
    return abs(a - b) / b * 100.0 if a and b else None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _status(d: Dict[str, Any]):
    t = d.get("thesis") or ""
    return t[1:t.index("]")] if t.startswith("[") and "]" in t else None


def diagnose(limit: int = 100000) -> Dict[str, Any]:
    decs = sorted(store.decisions(limit), key=lambda d: d["id"])
    trades = store.trades(limit=limit)
    entries = [d for d in decs if d["kind"] == "ENTRY"]
    manages = [d for d in decs if d["kind"] == "MANAGE"]
    findings: List[Dict[str, Any]] = []

    def add(severity, code, msg, count, examples=()):
        if count:
            findings.append({"severity": severity, "code": code, "message": msg, "count": count, "examples": list(examples)[:5]})

    # --- calls that failed or were slow
    bad = [d for d in decs if d.get("error") or not d.get("valid")]
    add("HIGH", "AI_ERRORS", "calls that failed, were truncated or returned invalid JSON (treated as NO_TRADE / HOLD)", len(bad),
        [f"#{d['id']} {d.get('error')}" for d in bad])
    lat = [d["latency_ms"] / 1000 for d in decs if d.get("latency_ms")]
    slow = [d for d in decs if d.get("latency_ms") and d["latency_ms"] / 1000 > SLOW_CALL_SECONDS]
    add("MED", "SLOW_CALLS", f"calls slower than {SLOW_CALL_SECONDS}s (the market moves while GPT thinks)", len(slow),
        [f"#{d['id']} {d['latency_ms'] / 1000:.0f}s" for d in slow])

    # --- proposals the safety layer or the simulator refused
    proposals = [d for d in entries if d["decision"] in ("LONG", "SHORT")]
    rejected = [d for d in proposals if d.get("risk_ok") == 0]
    reasons = Counter(r.strip() for d in rejected for r in (d.get("risk_reasons") or "").split(";") if r.strip())
    add("HIGH", "PROPOSALS_REJECTED", "LONG/SHORT proposals refused (bad geometry, wrong side, unsimulatable)", len(rejected),
        [f"{n}x {r}" for r, n in reasons.most_common(5)])

    # --- proposal quality
    scalpy, low_rr, bad_type, flips, stale_hold = [], [], [], [], []
    for d in proposals:
        if not (d.get("entry") and d.get("sl") and d.get("tp")):
            continue
        tgt, stp = _pct(d["tp"], d["entry"]), _pct(d["sl"], d["entry"])
        if tgt is not None and tgt < SCALP_TARGET_PCT:
            scalpy.append(f"#{d['id']} {d['decision']} target {tgt:.2f}% stop {stp:.2f}%")
        rr = abs(d["tp"] - d["entry"]) / abs(d["entry"] - d["sl"]) if d["entry"] != d["sl"] else None
        if rr is not None and rr < 1.5:
            low_rr.append(f"#{d['id']} R:R {rr:.2f} (prompt says NO_TRADE below 1.5)")
        px = d.get("price")
        if px and d.get("entry_type") == "MARKET" and _pct(d["entry"], px) > 0.15:
            bad_type.append(f"#{d['id']} MARKET entry {d['entry']} vs price {px}")
        long_ = d["decision"] == "LONG"
        if px and d.get("entry_type") == "LIMIT" and ((long_ and d["entry"] > px * 1.0015) or (not long_ and d["entry"] < px * 0.9985)):
            bad_type.append(f"#{d['id']} LIMIT entry {d['entry']} on the aggressive side of {px}")
        inv = d.get("invalidation_price")
        if inv and ((long_ and inv >= d["entry"]) or (not long_ and inv <= d["entry"])):
            bad_type.append(f"#{d['id']} invalidation {inv} is on the wrong side of entry {d['entry']}")
    add("MED", "SCALP_GEOMETRY", f"proposals with a target under {SCALP_TARGET_PCT}% away (the prompt asks for swing targets)", len(scalpy), scalpy)
    add("MED", "LOW_RR", "proposals below the 1.5 reward/risk the prompt itself set", len(low_rr), low_rr)
    add("MED", "ORDER_TYPE_MISMATCH", "entry price does not match the order type or invalidation side GPT declared", len(bad_type), bad_type)

    # --- flip-flopping between sides in a short time
    last = None
    for d in entries:
        if d["decision"] in ("LONG", "SHORT"):
            if last and last["decision"] != d["decision"] and d["ts"] - last["ts"] < FLIP_MINUTES * 60:
                flips.append(f"#{last['id']} {last['decision']} -> #{d['id']} {d['decision']} in {(d['ts'] - last['ts']) / 60:.0f} min")
            last = d
    add("MED", "SIDE_FLIP", f"LONG then SHORT (or reverse) within {FLIP_MINUTES} min: the view is not stable", len(flips), flips)

    # --- management reviews that contradict themselves
    contra = [d for d in manages if (_status(d) in ("INVALID", "OPPOSITE_STRONG") and d["decision"] == "HOLD")
              or (_status(d) == "VALID" and d["decision"] == "EXIT")]
    add("MED", "MANAGE_CONTRADICTION", "management review: thesis INVALID but HOLD, or thesis VALID but EXIT", len(contra),
        [f"#{d['id']} [{_status(d)}] -> {d['decision']}" for d in contra])
    rejected_manage = [d for d in manages if d.get("risk_ok") == 0]
    add("LOW", "MANAGE_REJECTED", "MOVE_SL requests refused (stop on the wrong side of price)", len(rejected_manage),
        [f"#{d['id']} {d.get('risk_reasons')}" for d in rejected_manage])

    # --- trades
    closed = [t for t in trades if t["status"] == "CLOSED"]
    stale = [t for t in trades if t.get("fill_price") and t.get("plan_entry") and t["entry_type"] == "MARKET" and _pct(t["fill_price"], t["plan_entry"]) > STALE_FILL_PCT]
    add("MED", "STALE_MARKET_FILL", f"MARKET fills more than {STALE_FILL_PCT}% from the price GPT planned (latency)", len(stale),
        [f"#{t['id']} plan {t['plan_entry']} fill {t['fill_price']}" for t in stale])
    never = [t for t in trades if t["status"] in ("EXPIRED", "CANCELLED", "REPLACED")]
    add("LOW", "ORDERS_NOT_FILLED", "pending orders that expired or were cancelled before filling", len(never),
        [f"{n}x {r}" for r, n in Counter(t["status"] for t in never).most_common()])
    mism, early, valid_exit, cut_short = [], [], [], []
    for t in closed:
        long_ = t["side"] == "LONG"
        inv, sl0 = t.get("invalidation_price"), t.get("sl0")
        if inv and sl0 and ((long_ and inv < sl0) or (not long_ and inv > sl0)):
            mism.append(f"#{t['id']} {t['side']} stop {sl0} is tighter than its own invalidation {inv}: stopped out before the thesis was 'wrong'")
        held_h = ((t["closed_ts"] - t["opened_ts"]) / 3600) if t.get("closed_ts") and t.get("opened_ts") else None
        exp = t.get("expected_hold_hours")
        if held_h is not None and exp and held_h < exp * 0.25:
            early.append(f"#{t['id']} held {held_h:.1f}h vs expected {exp:.0f}h ({t['exit_reason']})")
        if t["exit_reason"] == "AI_EXIT_VALID":
            valid_exit.append(f"#{t['id']} r {t['r_net']:+.2f}")
        if t["exit_reason"] in AI_EXIT_REASONS and t.get("cf_r_net") is not None and t["cf_r_net"] > (t["r_net"] or 0) + 0.5:
            cut_short.append(f"#{t['id']} exited {t['r_net']:+.2f}R, holding would have been {t['cf_r_net']:+.2f}R ({t['exit_reason']})")
    add("MED", "STOP_INSIDE_INVALIDATION", "stop closer than GPT's own invalidation price", len(mism), mism)
    add("MED", "CLOSED_MUCH_EARLIER", "trades closed in under a quarter of the hold time GPT expected", len(early), early)
    add("LOW", "EXIT_WHILE_VALID", "GPT exited while still calling the thesis valid", len(valid_exit), valid_exit)
    add("MED", "EXIT_COST_MONEY", "AI exits where holding to the original stop/target would have done at least 0.5R better", len(cut_short), cut_short)

    # --- confidence that never varies is not information
    confs = [d["confidence"] for d in proposals if d.get("confidence") is not None]
    if len(confs) >= 8 and len({round(c, 2) for c in confs}) <= 2:
        add("LOW", "FLAT_CONFIDENCE", "confidence barely varies, so it cannot be calibrated", len(confs), [f"values: {sorted({round(c, 2) for c in confs})}"])

    order = {"HIGH": 0, "MED": 1, "LOW": 2}
    findings.sort(key=lambda f: (order[f["severity"]], -f["count"]))
    return {"decisions": len(decs), "entry_reviews": len(entries), "manage_reviews": len(manages), "proposals": len(proposals), "trades": len(trades),
            "closed": len(closed), "median_latency_s": _med(lat), "findings": findings}
