"""AGENT 8 — TREND V2.

Rebuilt from a simple "is the EMA ribbon stacked right now + one slope
check" (max 4 discrete points, mapped linearly to confidence) into a
6-factor weighted model that measures genuine trend STRENGTH — how
separated the EMAs are relative to volatility, whether all three are
sloping the same way, whether the ribbon is expanding or contracting,
and how long the trend has actually persisted — rather than a single
snapshot of ribbon order.

Weights, per the original 7-factor design (Alignment 25 / Separation 20
/ Slope 20 / Price Location 10 / Ribbon Expansion 10 / Persistence 10 /
Structure Confirmation 5, sum 100):

STRUCTURE CONFIRMATION IS OMITTED, DELIBERATELY.
The Trend agent does not receive `market_state` — only the
`market_structure` agent itself does (see analysis_service.run_agents(),
which special-cases market_structure specifically). Duplicating
structure detection here would create two different definitions of
"structure" inside the same system, which is exactly the failure mode
to avoid — Market Structure stays the single authoritative source. So
this component is left out entirely, and its 5% weight is redistributed
proportionally across the other six (each scaled by 100/95) rather than
penalizing Trend for not having information it was never given.

Confidence and Strength are DIFFERENT things here, on purpose:
  - Strength = how strong the trend evidence is in the winning direction
    (the composite score itself)
  - Confidence = how much that reading can be trusted — requires several
    independent factors to actually agree, not just one strong one. A
    lone "EMA20 > EMA50 > EMA100" can no longer produce 85%+ confidence
    by itself; it needs separation, slope, persistence, etc. to also
    line up.

Every value here is computed from the SAME candle window passed in, at
the current call. No cross-call memory, no future data — the last N
candles the function is handed are the only data used at every step.
"""
import numpy as np
from ..contract import AgentResult, LONG, SHORT, NEUTRAL, neutral, clamp
from ..indicators import arrays, ema, atr

AGENT_ID = "trend"

MIN_CANDLES = 120  # true EMA100 needs real data behind it — no
# substituting a shorter EMA and calling it EMA100, unlike the old
# implementation's `ema(closes, min(80, len(closes)-1))` fallback.
SLOPE_LOOKBACK = 10
PERSISTENCE_LOOKBACK = 10

EMA_SEPARATION_WEAK = 0.10   # ATR-normalized separation below this = compressed/noise
EMA_SEPARATION_STRONG = 0.50  # at/above this = maximally separated

# Redistributed weights — Structure Confirmation's 5% spread
# proportionally across the remaining six, per the design's own explicit
# instruction (see module docstring). Computed as original% * (100/95).
W_ALIGNMENT = 25.0 * 100 / 95
W_SEPARATION = 20.0 * 100 / 95
W_SLOPE = 20.0 * 100 / 95
W_PRICE_LOCATION = 10.0 * 100 / 95
W_RIBBON_EXPANSION = 10.0 * 100 / 95
W_PERSISTENCE = 10.0 * 100 / 95

NET_SCORE_THRESHOLD = 15.0  # net (bull - bear) score must clear this to
# call a direction at all — a near-balanced read stays NEUTRAL rather
# than being forced one way or the other.


def _c01(x: float) -> float:
    return clamp(x, 0.0, 1.0)


def _trend_state(direction: str, score: float) -> str:
    if direction == NEUTRAL:
        return "NEUTRAL"
    prefix = "BULL" if direction == LONG else "BEAR"
    if score >= 70:
        return f"STRONG_{prefix}"
    if score >= 40:
        return prefix
    return f"WEAK_{prefix}"


def analyze(candles, timeframe: str) -> AgentResult:
    if len(candles) < MIN_CANDLES:
        return neutral(AGENT_ID, timeframe, "Not enough candles")

    a = arrays(candles)
    high, low, close = a["high"], a["low"], a["close"]
    e20 = ema(close, 20)
    e50 = ema(close, 50)
    e100 = ema(close, 100)

    e20_now, e50_now, e100_now = float(e20[-1]), float(e50[-1]), float(e100[-1])
    price = float(close[-1])
    _atr = atr(high, low, close, 14)
    if _atr <= 0:
        return neutral(AGENT_ID, timeframe, "Invalid ATR")

    # ---- 1. EMA Alignment ----
    bull_align = e20_now > e50_now > e100_now
    bear_align = e20_now < e50_now < e100_now

    # ---- 2. ATR-normalized EMA separation ----
    ema20_50_sep_atr = abs(e20_now - e50_now) / _atr
    ema50_100_sep_atr = abs(e50_now - e100_now) / _atr

    def _sep_component(sep_atr: float) -> float:
        return _c01((sep_atr - EMA_SEPARATION_WEAK) / (EMA_SEPARATION_STRONG - EMA_SEPARATION_WEAK))

    sep_component = (_sep_component(ema20_50_sep_atr) + _sep_component(ema50_100_sep_atr)) / 2.0
    bull_sep_score = sep_component if bull_align else 0.0
    bear_sep_score = sep_component if bear_align else 0.0

    # ---- 3. Multi-EMA slope ----
    if len(close) > SLOPE_LOOKBACK:
        slope20 = (e20_now - float(e20[-SLOPE_LOOKBACK - 1])) / _atr
        slope50 = (e50_now - float(e50[-SLOPE_LOOKBACK - 1])) / _atr
        slope100 = (e100_now - float(e100[-SLOPE_LOOKBACK - 1])) / _atr
    else:
        slope20 = slope50 = slope100 = 0.0

    def _slope_component(s: float) -> float:
        return _c01(abs(s) / EMA_SEPARATION_STRONG)

    bull_slope_score = sum(_slope_component(s) for s in (slope20, slope50, slope100) if s > 0) / 3.0
    bear_slope_score = sum(_slope_component(s) for s in (slope20, slope50, slope100) if s < 0) / 3.0

    # ---- 4. Price location relative to the ribbon ----
    ribbon_hi, ribbon_lo = max(e20_now, e50_now, e100_now), min(e20_now, e50_now, e100_now)
    if price > e20_now > e50_now > e100_now:
        location_state = "ABOVE_RIBBON"
        bull_loc_score, bear_loc_score = 1.0, 0.0
    elif price < e20_now < e50_now < e100_now:
        location_state = "BELOW_RIBBON"
        bull_loc_score, bear_loc_score = 0.0, 1.0
    elif ribbon_lo <= price <= ribbon_hi:
        location_state = "INSIDE_RIBBON"
        bull_loc_score, bear_loc_score = 0.3, 0.3
    else:
        location_state = "CROSSING_RIBBON"
        bull_loc_score = 0.5 if price > e50_now else 0.0
        bear_loc_score = 0.5 if price < e50_now else 0.0

    # ---- 5. Ribbon expansion / contraction ----
    ribbon_width_atr = (ribbon_hi - ribbon_lo) / _atr
    if len(close) > SLOPE_LOOKBACK:
        p20, p50, p100 = float(e20[-SLOPE_LOOKBACK - 1]), float(e50[-SLOPE_LOOKBACK - 1]), float(e100[-SLOPE_LOOKBACK - 1])
        prev_width_atr = (max(p20, p50, p100) - min(p20, p50, p100)) / _atr
    else:
        prev_width_atr = ribbon_width_atr
    width_change = ribbon_width_atr - prev_width_atr

    if width_change > 0.05:
        ribbon_state = "EXPANDING"
        expansion_component = _c01(width_change / 0.3)
    elif width_change < -0.05:
        ribbon_state = "CONTRACTING"
        expansion_component = 0.0
    else:
        ribbon_state = "STABLE"
        expansion_component = 0.3

    bull_expansion_score = expansion_component if bull_align else (0.3 if ribbon_state == "STABLE" else 0.0)
    bear_expansion_score = expansion_component if bear_align else (0.3 if ribbon_state == "STABLE" else 0.0)

    # ---- 6. Trend persistence ----
    lookback_n = min(PERSISTENCE_LOOKBACK, len(close) - 1, len(e50) - 1)
    if lookback_n > 0:
        bull_persistence = sum(1 for i in range(-lookback_n, 0) if close[i] > e50[i]) / lookback_n
        bear_persistence = sum(1 for i in range(-lookback_n, 0) if close[i] < e50[i]) / lookback_n
    else:
        bull_persistence = bear_persistence = 0.0

    bull_score = (
        (1.0 if bull_align else 0.0) * W_ALIGNMENT
        + bull_sep_score * W_SEPARATION
        + bull_slope_score * W_SLOPE
        + bull_loc_score * W_PRICE_LOCATION
        + bull_expansion_score * W_RIBBON_EXPANSION
        + bull_persistence * W_PERSISTENCE
    )
    bear_score = (
        (1.0 if bear_align else 0.0) * W_ALIGNMENT
        + bear_sep_score * W_SEPARATION
        + bear_slope_score * W_SLOPE
        + bear_loc_score * W_PRICE_LOCATION
        + bear_expansion_score * W_RIBBON_EXPANSION
        + bear_persistence * W_PERSISTENCE
    )

    net_score = bull_score - bear_score
    if net_score >= NET_SCORE_THRESHOLD:
        direction = LONG
    elif net_score <= -NET_SCORE_THRESHOLD:
        direction = SHORT
    else:
        direction = NEUTRAL

    directional_strength = abs(net_score)
    if direction == LONG:
        factor_values = [1.0 if bull_align else 0.0, bull_sep_score, bull_slope_score,
                         bull_loc_score, bull_expansion_score, bull_persistence]
        persistence_for_conf = bull_persistence
    elif direction == SHORT:
        factor_values = [1.0 if bear_align else 0.0, bear_sep_score, bear_slope_score,
                         bear_loc_score, bear_expansion_score, bear_persistence]
        persistence_for_conf = bear_persistence
    else:
        factor_values, persistence_for_conf = [], 0.0

    agreement = (sum(1 for v in factor_values if v > 0.5) / len(factor_values)) if factor_values else 0.0

    if direction == NEUTRAL:
        confidence = clamp(30 + directional_strength * 0.3, 0, 55)
    else:
        confidence = clamp(directional_strength * 0.6 + agreement * 25 + persistence_for_conf * 15, 0, 94)

    strength = clamp(max(bull_score, bear_score), 0, 100)
    trend_state = _trend_state(direction, strength)

    evidence = [
        f"EMA20={e20_now:.1f}, EMA50={e50_now:.1f}, EMA100={e100_now:.1f}",
        f"EMA alignment: {'BULLISH' if bull_align else ('BEARISH' if bear_align else 'MIXED')}",
        f"EMA separation: {ema20_50_sep_atr:.2f} ATR (20-50), {ema50_100_sep_atr:.2f} ATR (50-100)",
        f"EMA slopes: 20={slope20:+.2f} ATR, 50={slope50:+.2f} ATR, 100={slope100:+.2f} ATR",
        f"Price location: {location_state}",
        f"Ribbon state: {ribbon_state} (width {ribbon_width_atr:.2f} ATR)",
        f"Persistence: {int(round((bull_persistence if direction == LONG else bear_persistence) * lookback_n)) if direction != NEUTRAL else '-'}/{lookback_n} {'bullish' if direction == LONG else ('bearish' if direction == SHORT else '')}",
        f"Trend state: {trend_state}",
        "Structure confirmation: unavailable to this agent (Market Structure remains the sole authority — see module docstring), weight redistributed",
    ]

    key_levels = [
        {"label": "EMA20", "price": round(e20_now, 2), "type": "dynamic"},
        {"label": "EMA50", "price": round(e50_now, 2), "type": "dynamic"},
        {"label": "EMA100", "price": round(e100_now, 2), "type": "dynamic"},
    ]

    return AgentResult(AGENT_ID, direction, round(confidence, 1), round(strength, 1),
                       evidence, key_levels, timeframe, valid=True)


from .observe import observe  # Step 6F — AnalysisObservation producer
