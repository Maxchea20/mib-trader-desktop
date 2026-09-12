"""AGENT 7 — SUPPORT / RESISTANCE V2.

Upgraded from "cluster pivots at a fixed 0.4% tolerance, rank by touch
count, near support => LONG / near resistance => SHORT" into a proper
zone-quality engine. The old version's core problem: proximity alone
determined direction — a strong support zone approached with zero
actual reaction still produced LONG, and the fixed percentage tolerance
made zones too tight in low volatility and too loose in high volatility.

Reuses the existing find_pivots() and cluster_levels() from
indicators.py completely unchanged — this version's job is to make the
CLUSTERING WIDTH volatility-aware (an ATR-derived tolerance_pct fed into
the same unmodified cluster_levels() call) and to build real zone
quality, reaction detection, and a state machine ON TOP of that
unchanged clustering, not to replace it.

Direction now depends on an actual, completed-candle REACTION at a zone
(rejection with follow-through), never merely being close to one.
Proximity alone produces NEUTRAL with informative evidence — exactly
the "strong resistance nearby, no rejection yet => NEUTRAL" case the
spec calls out explicitly.

S/R owns: zones, zone strength, recency, reaction/rejection quality,
proximity, broken-level/flip context. It does NOT duplicate Market
Structure's BOS/CHoCH or Breakout's full lifecycle — a "flip" here is
only a lightweight zone-role observation (former resistance, price
closed and held above => now potential support), not a breakout
lifecycle analysis.
"""
import numpy as np
from ..contract import AgentResult, LONG, SHORT, NEUTRAL, neutral, clamp
from ..indicators import arrays, atr, find_pivots, cluster_levels

AGENT_ID = "support_resistance"

MIN_CANDLES = 60
MIN_PIVOTS = 4
PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# --- Normalization anchors — illustrative starting values, same caveat
# as every other threshold in this codebase. ---
ATR_TOLERANCE_MULTIPLIER = 0.35  # how many ATRs wide a cluster
# tolerance band is, converted into the equivalent percentage fed to
# the existing cluster_levels() — this is what makes zone width
# volatility-aware instead of a fixed 0.4%.
MIN_TOLERANCE_PCT = 0.10
MAX_TOLERANCE_PCT = 1.20

APPROACH_ZONE_ATR = 0.6     # within this many ATRs = "approaching"
AT_ZONE_ATR = 0.15          # within this many ATRs = "at" the zone
REACTION_LOOKBACK = 4        # candles examined for a reaction at the nearest zone
REACTION_MIN_ATR = 0.35      # minimum displacement away from a zone to count as a reaction
FLIP_HOLD_BARS = 3           # bars price must hold beyond a broken level before calling it a flip
RECENCY_HALF_LIFE_BARS = 150  # zone recency decays to 50% weight after this many bars

W_ZONE_STRENGTH = 25.0
W_PROXIMITY = 15.0
W_RECENCY = 10.0
W_REACTION = 20.0
W_TOUCH_QUALITY = 10.0
W_ZONE_CONTEXT = 10.0
W_FLIP_CONTEXT = 10.0


def _atr_tolerance_pct(atr_value, price):
    """Converts the current ATR into an equivalent percentage-of-price
    value, fed straight into the existing, UNMODIFIED cluster_levels()
    — this is the one thing that makes clustering volatility-aware,
    without touching the shared indicators.py utility itself."""
    if price <= 0:
        return MIN_TOLERANCE_PCT
    raw_pct = (atr_value * ATR_TOLERANCE_MULTIPLIER) / price * 100.0
    return clamp(raw_pct, MIN_TOLERANCE_PCT, MAX_TOLERANCE_PCT)


def _enrich_zones(zones, pivots, high, low, close, atr_value, current_idx):
    """cluster_levels() only returns {price, count, low, high} per zone
    — this matches each ORIGINAL pivot back to whichever zone its price
    falls into, so we can compute recency/reaction quality per zone
    without touching cluster_levels() itself."""
    enriched = []
    for z in zones:
        lo, hi = z["low"], z["high"]
        members = [p for p in pivots if lo - 1e-9 <= p["price"] <= hi + 1e-9]
        if not members:
            continue
        indices = [p["i"] for p in members]
        latest_idx = max(indices)
        first_idx = min(indices)
        age_bars = current_idx - first_idx
        recency_bars = current_idx - latest_idx
        recency_score = 0.5 ** (recency_bars / RECENCY_HALF_LIFE_BARS)

        # Reaction magnitude at each contributing pivot — how far price
        # moved AWAY from that pivot's price within the following few
        # candles (ATR-normalized). Only candles strictly AFTER the
        # pivot's own confirmation point are used, so this never uses
        # information the pivot itself wouldn't have had yet.
        reactions = []
        for p in members:
            pi = p["i"]
            end = min(pi + 6, current_idx + 1)
            if end <= pi + 1 or atr_value <= 0:
                continue
            if p["type"] == "H":
                move = float(p["price"]) - float(np.min(low[pi + 1:end]))
            else:
                move = float(np.max(high[pi + 1:end])) - float(p["price"])
            reactions.append(max(move, 0.0) / atr_value)
        avg_reaction_atr = float(np.mean(reactions)) if reactions else 0.0

        zone_type = "resistance" if z["price"] >= close[current_idx] else "support"
        enriched.append({
            "price": z["price"], "low": lo, "high": hi, "count": z["count"],
            "type": zone_type, "first_idx": first_idx, "latest_idx": latest_idx,
            "age_bars": age_bars, "recency_bars": recency_bars,
            "recency_score": recency_score, "avg_reaction_atr": avg_reaction_atr,
        })
    return enriched


def _zone_strength(z):
    touch_score = clamp(z["count"] / 5.0, 0, 1) * W_ZONE_STRENGTH * 0.4
    reaction_score = clamp(z["avg_reaction_atr"] / 1.2, 0, 1) * W_ZONE_STRENGTH * 0.4
    recency_score = z["recency_score"] * W_ZONE_STRENGTH * 0.2
    return round(touch_score + reaction_score + recency_score, 1)


def analyze(candles, timeframe: str) -> AgentResult:
    if len(candles) < MIN_CANDLES:
        return neutral(AGENT_ID, timeframe, "Not enough candles")

    a = arrays(candles)
    high, low, close, open_ = a["high"], a["low"], a["close"], a["open"]
    n = len(close)
    idx = n - 1
    c = float(close[idx])

    _atr = atr(high, low, close, 14)
    if _atr <= 0 or not np.isfinite(_atr):
        return neutral(AGENT_ID, timeframe, "Invalid ATR")

    piv = find_pivots(high, low, left=PIVOT_LEFT, right=PIVOT_RIGHT)
    if len(piv) < MIN_PIVOTS:
        return neutral(AGENT_ID, timeframe, "Too few pivots")

    prices = [p["price"] for p in piv]
    tolerance_pct = _atr_tolerance_pct(_atr, c)
    zones = cluster_levels(prices, tolerance_pct)
    zones = _enrich_zones(zones, piv, high, low, close, _atr, idx)
    if not zones:
        return neutral(AGENT_ID, timeframe, "No valid zones after enrichment")

    for z in zones:
        z["strength"] = _zone_strength(z)

    supports = sorted([z for z in zones if z["price"] < c], key=lambda z: -z["price"])
    resistances = sorted([z for z in zones if z["price"] >= c], key=lambda z: z["price"])

    nearest_support = supports[0] if supports else None
    nearest_resistance = resistances[0] if resistances else None

    dist_sup_atr = (c - nearest_support["price"]) / _atr if nearest_support else 999.0
    dist_res_atr = (nearest_resistance["price"] - c) / _atr if nearest_resistance else 999.0

    zone, zone_dist_atr, zone_side = None, 999.0, None
    if dist_sup_atr <= dist_res_atr and nearest_support is not None:
        zone, zone_dist_atr, zone_side = nearest_support, dist_sup_atr, "support"
    elif nearest_resistance is not None:
        zone, zone_dist_atr, zone_side = nearest_resistance, dist_res_atr, "resistance"

    reaction = None
    reaction_displacement_atr = 0.0
    broken = False
    if zone is not None:
        if zone_side == "support":
            entered = float(np.min(low[-REACTION_LOOKBACK:])) <= zone["high"]
            closed_back_above = c > zone["high"]
            reaction_displacement_atr = (c - zone["low"]) / _atr
            if entered and closed_back_above and reaction_displacement_atr >= REACTION_MIN_ATR:
                reaction = "bullish_rejection"
            broken = c < zone["low"] - (0.1 * _atr)
        else:
            entered = float(np.max(high[-REACTION_LOOKBACK:])) >= zone["low"]
            closed_back_below = c < zone["low"]
            reaction_displacement_atr = (zone["high"] - c) / _atr
            if entered and closed_back_below and reaction_displacement_atr >= REACTION_MIN_ATR:
                reaction = "bearish_rejection"
            broken = c > zone["high"] + (0.1 * _atr)

    flip_context = None
    if zone is not None and broken and n > FLIP_HOLD_BARS:
        held = True
        for k in range(idx - FLIP_HOLD_BARS + 1, idx + 1):
            if zone_side == "support" and close[k] >= zone["low"]:
                held = False
                break
            if zone_side == "resistance" and close[k] <= zone["high"]:
                held = False
                break
        if held:
            flip_context = ("former support, now potential resistance" if zone_side == "support"
                            else "former resistance, now potential support")

    if zone is None:
        state = "NO_NEARBY_LEVEL"
    elif broken and flip_context:
        state = "RETESTING_BROKEN_SUPPORT" if zone_side == "support" else "RETESTING_BROKEN_RESISTANCE"
    elif broken:
        state = "SUPPORT_BROKEN" if zone_side == "support" else "RESISTANCE_BROKEN"
    elif reaction == "bullish_rejection":
        state = "SUPPORT_REJECTION"
    elif reaction == "bearish_rejection":
        state = "RESISTANCE_REJECTION"
    elif zone_dist_atr <= AT_ZONE_ATR:
        state = "AT_SUPPORT" if zone_side == "support" else "AT_RESISTANCE"
    elif zone_dist_atr <= APPROACH_ZONE_ATR:
        state = "APPROACHING_SUPPORT" if zone_side == "support" else "APPROACHING_RESISTANCE"
    else:
        state = "BETWEEN_LEVELS"

    if reaction == "bullish_rejection":
        direction = LONG
    elif reaction == "bearish_rejection":
        direction = SHORT
    else:
        direction = NEUTRAL

    zone_strength_score = zone["strength"] if zone else 0.0
    proximity_score = clamp(1.0 - (zone_dist_atr / APPROACH_ZONE_ATR), 0, 1) * W_PROXIMITY if zone else 0.0
    recency_score = (zone["recency_score"] * W_RECENCY) if zone else 0.0
    reaction_score = clamp(reaction_displacement_atr / 1.0, 0, 1) * W_REACTION if reaction else 0.0
    touch_quality_score = clamp((zone["count"] if zone else 0) / 5.0, 0, 1) * W_TOUCH_QUALITY
    zone_context_score = W_ZONE_CONTEXT if zone and zone["count"] >= 3 else (W_ZONE_CONTEXT * 0.4 if zone else 0.0)
    flip_score = W_FLIP_CONTEXT if flip_context else 0.0

    total_score = (zone_strength_score + proximity_score + recency_score + reaction_score
                  + touch_quality_score + zone_context_score + flip_score)
    confidence = clamp(25.0 + total_score * 0.65, 0, 94)
    strength = clamp((zone["strength"] if zone else 0) + reaction_score, 0, 100)

    evidence = [f"{len(zones)} clustered zone(s) detected (tolerance {tolerance_pct:.2f}%, ATR-derived)"]
    if zone is not None:
        label = "resistance" if zone_side == "resistance" else "support"
        evidence.append(f"Nearest {label}: {zone['low']:.1f}\u2013{zone['high']:.1f}")
        evidence.append(f"{label.capitalize()} strength: {zone['strength']:.0f}/100")
        evidence.append(f"{zone['count']} touch(es), recency score {zone['recency_score']:.2f}")
        evidence.append(f"Distance: {zone_dist_atr:.2f} ATR")
        evidence.append(f"State: {state}")
        if reaction:
            evidence.append(f"{'Bullish' if reaction == 'bullish_rejection' else 'Bearish'} rejection detected, "
                            f"displacement {reaction_displacement_atr:.2f} ATR")
        elif broken:
            evidence.append(f"{label.capitalize()} zone broken" + (f" \u2014 {flip_context}" if flip_context else " \u2014 not yet held long enough to confirm a role flip"))
        elif zone_dist_atr <= APPROACH_ZONE_ATR:
            evidence.append(f"No confirmed {'bullish' if zone_side == 'support' else 'bearish'} rejection yet")
    else:
        evidence.append("No nearby zone identified")

    key_levels = []
    for z in supports[:3]:
        key_levels.append({"label": f"Support ({z['count']}x, str {z['strength']:.0f})",
                           "price": round(z["price"], 2), "type": "support",
                           "touches": z["count"], "strength": z["strength"],
                           "low": round(z["low"], 2), "high": round(z["high"], 2)})
    for z in resistances[:3]:
        key_levels.append({"label": f"Resistance ({z['count']}x, str {z['strength']:.0f})",
                           "price": round(z["price"], 2), "type": "resistance",
                           "touches": z["count"], "strength": z["strength"],
                           "low": round(z["low"], 2), "high": round(z["high"], 2)})

    valid = zone is not None

    return AgentResult(AGENT_ID, direction, round(confidence, 1), round(strength, 1),
                       evidence, key_levels, timeframe, valid=valid)


from .observe import observe  # Step 6B — AnalysisObservation producer
