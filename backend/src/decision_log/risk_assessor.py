"""Risk metrics — LOG-ONLY, cannot block a trade.

IMPORTANT: no risk-gating stage currently exists anywhere in this
codebase (confirmed by inspection — position sizing only decides HOW
BIG a trade is, never WHETHER it fires). This module computes NEW risk
metrics that didn't exist before, purely for the decision log to record.

Every threshold and scoring choice in this file is illustrative and
UNVALIDATED — exactly the same caveat that already applies to Entry
Thresholds / HTF Gate / Conflict settings elsewhere in this codebase.
Nothing here has been backtested. Treat risk_score and risk_status as
"a reasonable-looking guess to look at while reviewing decisions," not
as ground truth, until it's been checked against real outcomes.

This function's return value must never be used to decide whether a
trade fires — only to log what the conditions looked like at the time.
"""
from typing import Dict, Optional

# Illustrative thresholds — tune per-symbol once real data exists to
# calibrate against. BTC-appropriate guesses, not measured.
VOLATILITY_LOW_ATR_PCT = 0.3
VOLATILITY_HIGH_ATR_PCT = 1.0
SPREAD_TIGHT_PCT = 0.02
SPREAD_WIDE_PCT = 0.05
MIN_ACCEPTABLE_RISK_REWARD = 1.2


def assess(price: float, atr: float, sl_atr_mult: float, tp_atr_mult: float,
           notional_usd: float, bid: Optional[float] = None,
           ask: Optional[float] = None) -> Dict:
    atr_pct = (atr / price * 100.0) if price > 0 else 0.0

    if atr_pct < VOLATILITY_LOW_ATR_PCT:
        volatility_status = "LOW"
    elif atr_pct > VOLATILITY_HIGH_ATR_PCT:
        volatility_status = "HIGH"
    else:
        volatility_status = "NORMAL"

    spread = None
    spread_pct = None
    spread_status = "UNKNOWN"
    if bid is not None and ask is not None and bid > 0:
        spread = ask - bid
        spread_pct = spread / bid * 100.0
        if spread_pct < SPREAD_TIGHT_PCT:
            spread_status = "TIGHT"
        elif spread_pct > SPREAD_WIDE_PCT:
            spread_status = "WIDE"
        else:
            spread_status = "NORMAL"

    stop_distance = sl_atr_mult * atr
    stop_distance_pct = (stop_distance / price * 100.0) if price > 0 else 0.0
    risk_reward = (tp_atr_mult / sl_atr_mult) if sl_atr_mult > 0 else 0.0

    # Position sizing in this codebase is currently a flat configured
    # notional_usd, not dynamic — reported as-is, not judged.
    position_size_status = f"FIXED ${notional_usd:.0f}"

    # Simple illustrative composite score — see module docstring caveat.
    score = 70.0
    if spread_status == "WIDE":
        score -= 20
    if volatility_status == "HIGH":
        score -= 15
    if volatility_status == "LOW":
        score -= 5  # too quiet can mean a stop that's too tight to be meaningful
    if risk_reward >= MIN_ACCEPTABLE_RISK_REWARD:
        score += 10
    else:
        score -= 15
    score = max(0.0, min(100.0, score))
    risk_status = "ACCEPTABLE" if score >= 50 else "CAUTION"

    return {
        "spread": spread, "spread_pct": spread_pct, "spread_status": spread_status,
        "atr_pct": round(atr_pct, 4), "volatility_status": volatility_status,
        "stop_distance": round(stop_distance, 6), "stop_distance_pct": round(stop_distance_pct, 4),
        "risk_reward": round(risk_reward, 3),
        "position_size_status": position_size_status,
        "risk_score": round(score, 1), "risk_status": risk_status,
    }