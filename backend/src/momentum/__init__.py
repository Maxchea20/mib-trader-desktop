"""AGENT 6 — MOMENTUM V2.

Upgraded from a simple weighted-sum composite (0.30*RSI + 0.30*MACD +
0.20*ROC + 0.20*EMA-slope, each independently normalized) into a genuine
momentum-BEHAVIOR engine. The old version's real failure mode: RSI and
MACD could both read bullish while ROC and EMA slope read bearish,
netting out to a small, uninformative composite score that describes
none of the four inputs accurately — a coin-flip direction from
contradictory evidence, not a coherent read on what price is actually
doing.

This version keeps RSI/MACD/ROC/EMA-slope as real inputs (same
indicators.py helpers, nothing new implemented there) but organizes them
into 7 genuinely different QUESTIONS about price behavior, per the
spec's own framing — Momentum's job is "how much force is behind this
move, and is that force growing, stable, weakening, exhausting,
retracing, or reversing," not just "LONG or SHORT":

    A. VELOCITY               — how fast is price moving right now
    B. ACCELERATION           — is that speed increasing or decreasing
    C. IMPULSE                — did a genuinely forceful single/short
                                 burst of movement just happen
    D. EXPANSION/CONTRACTION  — is momentum itself growing or shrinking
                                 over the last several bars
    E. EXHAUSTION             — multi-factor evidence (NOT "RSI>70"),
                                 price still extending while the
                                 underlying force behind it fades
    F. DIVERGENCE             — price vs. RSI disagreeing at comparable
                                 local extremes (a lightweight, LOCAL
                                 pivot check computed fresh here — this
                                 does NOT reuse or duplicate Market
                                 Structure's swing detection; it is a
                                 momentum-specific, much simpler check
                                 solely for divergence purposes)
    G. RETRACEMENT vs REVERSAL — does an opposing move look like a
                                 normal pullback within the prevailing
                                 direction, or a persistent, strengthening
                                 move against it

Momentum does not decide whether HH/HL/LH/LL structure has changed —
that remains Market Structure's exclusive domain. Momentum only reports
on the FORCE and BEHAVIOR of price movement.

Confidence and Strength are different concepts here, on purpose:
Strength = how much force is actually behind the current move right now.
Confidence = how coherent/reliable the read is — several dimensions
agreeing raises confidence even when strength itself is fading (e.g. a
system can be highly CONFIDENT that bullish momentum is EXHAUSTING while
the STRENGTH of that momentum is already low).
"""
import numpy as np
from ..contract import AgentResult, LONG, SHORT, NEUTRAL, clamp, neutral
from ..indicators import arrays, atr, rsi, macd, roc, ema

AGENT_ID = "momentum"

MIN_CANDLES = 60  # longest input here is MACD (needs slow=26 + signal=9
# already, plus we need several bars of HISTORY of that to detect
# acceleration/expansion/divergence) — meaningfully more than the old
# version's 40, since this version needs a genuine short history of
# indicator values, not just their current snapshot.

# --- Normalization anchors. Same caveat as every other threshold in
# this codebase: illustrative starting points, not validated, named here
# so they're easy to find and tune once real data exists to calibrate
# against. ---
ACCEL_LOOKBACK = 3        # bars back to compare ROC/MACD-hist against, for acceleration
EXPANSION_LOOKBACK = 4    # bars of MACD-hist history examined for expansion/contraction
IMPULSE_ATR_CAP = 1.5     # candle body/ATR at which impulse score maxes out
DISPLACEMENT_ATR_CAP = 2.0  # 5-bar ATR-normalized displacement cap
DIVERGENCE_LOOKBACK = 20  # bars searched for the two local extremes compared for divergence
RETRACEMENT_LOOKBACK = 10  # bars used to establish the "prevailing direction"
EXHAUSTION_MIN_STREAK = 3  # consecutive same-direction candles considered for exhaustion

W_VELOCITY = 20.0
W_ACCELERATION = 20.0
W_IMPULSE = 15.0
W_EXPANSION = 15.0
W_EXHAUSTION = 10.0
W_DIVERGENCE = 10.0
W_RETRACEMENT = 10.0


def _safe(x, default=0.0):
    return default if (x is None or not np.isfinite(x)) else float(x)


def _roc_series(closes, period, count):
    """A short history of roc() values — the shared indicators.roc()
    only returns the current scalar, same limitation _rolling_atr_series
    solves for Breakout. Computed by slicing progressively, exactly the
    same anti-lookahead-safe pattern already used there."""
    n = len(closes)
    out = []
    for end in range(max(period + 1, n - count), n + 1):
        out.append(roc(closes[:end], period))
    return np.array(out, dtype=float)


def _macd_hist_series(closes, count):
    n = len(closes)
    out = []
    min_needed = 26 + 9
    for end in range(max(min_needed, n - count), n + 1):
        _, _, h = macd(closes[:end])
        out.append(h)
    return np.array(out, dtype=float)


def _local_extremes(values, lookback):
    """Simple LOCAL pivot detection, for divergence only — a much
    lighter-weight version of Market Structure's pivot logic, purely to
    compare price's extreme against the momentum indicator's value at
    that same point. Not a structural swing system, not reused from or
    duplicating Market Structure."""
    n = len(values)
    if n < 5:
        return None, None
    window = values[-lookback:]
    hi_idx = int(np.argmax(window))
    lo_idx = int(np.argmin(window))
    return (n - len(window) + hi_idx), (n - len(window) + lo_idx)


def analyze(candles, timeframe: str) -> AgentResult:
    if len(candles) < MIN_CANDLES:
        r = neutral(AGENT_ID, timeframe, "Not enough candles for Momentum V2 analysis")
        r.valid = False
        return r

    a = arrays(candles)
    high, low, close, open_ = a["high"], a["low"], a["close"], a["open"]
    n = len(close)

    _atr = atr(high, low, close, 14)
    if _atr <= 0 or not np.isfinite(_atr):
        r = neutral(AGENT_ID, timeframe, "Invalid ATR")
        r.valid = False
        return r

    # --- Base indicators (unchanged helpers, same as before) ---
    _rsi = _safe(rsi(close, 14), 50.0)
    _, _, hist = macd(close)
    hist = _safe(hist)
    roc5 = _safe(roc(close, 5))
    roc12 = _safe(roc(close, 12))
    roc20 = _safe(roc(close, 20)) if n >= 21 else 0.0
    e20 = ema(close, 20)
    slope_atr = _safe((e20[-1] - e20[-5]) / _atr) if len(e20) >= 5 else 0.0

    # --- A. VELOCITY (20 pts) ---
    displacement_atr = _safe((close[-1] - close[-6]) / _atr) if n >= 6 else 0.0
    velocity_raw = 0.5 * clamp(abs(roc12) / 1.5, 0, 1) + 0.5 * clamp(abs(displacement_atr) / DISPLACEMENT_ATR_CAP, 0, 1)
    velocity_bullish = roc12 > 0
    velocity_score = velocity_raw * W_VELOCITY

    # --- B. ACCELERATION (20 pts) ---
    roc_hist = _roc_series(close, 12, ACCEL_LOOKBACK + 2)
    if len(roc_hist) > ACCEL_LOOKBACK:
        accel_raw = roc_hist[-1] - roc_hist[-1 - ACCEL_LOOKBACK]
    else:
        accel_raw = 0.0
    accelerating = (accel_raw > 0) == (roc12 > 0) and abs(accel_raw) > 1e-9
    acceleration_score = clamp(abs(accel_raw) / 1.0, 0, 1) * W_ACCELERATION

    # --- C. IMPULSE (15 pts) ---
    body_atr = _safe(abs(close[-1] - open_[-1]) / _atr)
    range_atr = _safe((high[-1] - low[-1]) / _atr)
    impulse_bullish = close[-1] > open_[-1]
    impulse_score = clamp(((body_atr + range_atr) / 2.0) / IMPULSE_ATR_CAP, 0, 1) * W_IMPULSE

    # --- D. EXPANSION / CONTRACTION (15 pts) ---
    hist_series = _macd_hist_series(close, EXPANSION_LOOKBACK)
    if len(hist_series) >= 2:
        mag_series = np.abs(hist_series)
        expanding = mag_series[-1] > mag_series[0]
        expansion_change = float(mag_series[-1] - mag_series[0])
    else:
        expanding = False
        expansion_change = 0.0
    denom = max(abs(float(np.mean(np.abs(hist_series)))) if len(hist_series) else 1e-9, 1e-9)
    expansion_score = clamp(abs(expansion_change) / (denom * 2.0), 0, 1) * W_EXPANSION

    # --- Consecutive directional candles (feeds impulse context and exhaustion) ---
    streak = 0
    streak_dir = None
    for k in range(n - 1, max(n - 15, 0), -1):
        d = close[k] > open_[k]
        if streak_dir is None:
            streak_dir = d
            streak = 1
        elif d == streak_dir:
            streak += 1
        else:
            break

    # --- E. EXHAUSTION (10 pts) — multi-factor, NOT "RSI>70" ---
    price_extending = False
    if n >= 9 and streak_dir is not None:
        if streak_dir:
            price_extending = close[-1] >= float(np.max(close[-8:-1]))
        else:
            price_extending = close[-1] <= float(np.min(close[-8:-1]))
    exhaustion_evidence = 0
    if streak >= EXHAUSTION_MIN_STREAK:
        exhaustion_evidence += 1
    if not expanding and len(hist_series) >= 2:
        exhaustion_evidence += 1
    if price_extending:
        exhaustion_evidence += 1
    if (streak_dir and _rsi > 65) or (streak_dir is False and _rsi < 35):
        exhaustion_evidence += 1  # RSI is ONE of four pieces of evidence, never used alone
    # 3-tier classification: LOW (0-1 factors), DEVELOPING (2 factors),
    # HIGH (3-4 factors). `exhausting` (used for state-machine/strength
    # penalty purposes below) stays True only for the HIGH tier — a
    # DEVELOPING read is worth surfacing as evidence but shouldn't yet
    # carry the same strength penalty or force the *_EXHAUSTING state.
    if exhaustion_evidence >= 3:
        exhaustion_state = "HIGH"
    elif exhaustion_evidence == 2:
        exhaustion_state = "DEVELOPING"
    else:
        exhaustion_state = "LOW"
    exhausting = exhaustion_state == "HIGH"
    exhaustion_score = (exhaustion_evidence / 4.0) * W_EXHAUSTION

    # --- F. DIVERGENCE (10 pts) — local price vs RSI extremes only, no
    # lookahead. Both REGULAR divergence (price vs momentum disagree —
    # a reversal warning) and HIDDEN divergence (price makes a smaller
    # extreme but momentum makes a BIGGER one in the trend direction —
    # a continuation signal, the opposite meaning) are checked. ---
    rsi_series = []
    for end in range(max(15, n - DIVERGENCE_LOOKBACK), n + 1):
        rsi_series.append(_safe(rsi(close[:end], 14), 50.0))
    rsi_series = np.array(rsi_series)
    price_window = close[-len(rsi_series):]
    hi_idx, lo_idx = _local_extremes(price_window, DIVERGENCE_LOOKBACK)
    divergence = None
    if hi_idx is not None and lo_idx is not None and len(rsi_series) > max(hi_idx, lo_idx):
        mid = len(price_window) // 2
        if mid > 0:
            recent_high_idx = int(np.argmax(price_window[mid:])) + mid
            early_high_idx = int(np.argmax(price_window[:mid]))
            recent_low_idx = int(np.argmin(price_window[mid:])) + mid
            early_low_idx = int(np.argmin(price_window[:mid]))

            price_higher_high = price_window[recent_high_idx] > price_window[early_high_idx]
            price_lower_high = price_window[recent_high_idx] < price_window[early_high_idx]
            price_lower_low = price_window[recent_low_idx] < price_window[early_low_idx]
            price_higher_low = price_window[recent_low_idx] > price_window[early_low_idx]

            rsi_lower_high = rsi_series[recent_high_idx] < rsi_series[early_high_idx] - 3
            rsi_higher_high = rsi_series[recent_high_idx] > rsi_series[early_high_idx] + 3
            rsi_higher_low = rsi_series[recent_low_idx] > rsi_series[early_low_idx] + 3
            rsi_lower_low = rsi_series[recent_low_idx] < rsi_series[early_low_idx] - 3

            # Regular divergence — price and momentum disagree (reversal warning).
            if price_higher_high and rsi_lower_high:
                divergence = "bearish"
            if divergence is None and price_lower_low and rsi_higher_low:
                divergence = "bullish"

            # Hidden divergence — checked only if no regular divergence
            # already found; opposite meaning (continuation, not reversal).
            if divergence is None and price_higher_low and rsi_lower_low:
                divergence = "hidden_bullish"
            if divergence is None and price_lower_high and rsi_higher_high:
                divergence = "hidden_bearish"
    divergence_score = W_DIVERGENCE if divergence else 0.0

    # --- G. RETRACEMENT vs REVERSAL (10 pts) ---
    prevailing_bullish = (roc(close[:-1], RETRACEMENT_LOOKBACK) > 0) if n > RETRACEMENT_LOOKBACK + 1 else (roc12 > 0)
    recent_opposes = ((roc5 > 0) != prevailing_bullish) and abs(roc5) > 0.05
    retrace_or_reversal = None
    if recent_opposes:
        opposing_strength = clamp(abs(roc5) / 1.0, 0, 1)
        if opposing_strength > 0.6 and streak >= EXHAUSTION_MIN_STREAK and streak_dir != prevailing_bullish:
            retrace_or_reversal = "reversal"
        else:
            retrace_or_reversal = "retracement"
    retracement_score = W_RETRACEMENT if retrace_or_reversal == "retracement" else (W_RETRACEMENT * 0.3 if retrace_or_reversal == "reversal" else 0.0)

    # --- Combine into bull/bear composite ---
    bull_votes = sum([
        1 if velocity_bullish else 0,
        1 if (accelerating and roc12 > 0) else 0,
        1 if (impulse_bullish and roc12 > 0) else 0,
        1 if hist > 0 else 0,
    ])
    bear_votes = 4 - bull_votes
    net_bullish = bull_votes > bear_votes

    strength_raw_max = W_VELOCITY + W_ACCELERATION + W_IMPULSE + W_EXPANSION
    strength = clamp(velocity_score + acceleration_score + impulse_score + expansion_score, 0, strength_raw_max) / strength_raw_max * 100.0
    if exhausting:
        strength *= 0.55  # exhausting force is, by definition, weaker than raw velocity alone suggests

    coherence_score = velocity_score + acceleration_score + impulse_score + expansion_score + exhaustion_score + divergence_score + retracement_score
    confidence = clamp(30.0 + coherence_score * 0.65, 0, 94)

    if abs(roc12) < 0.05 and abs(displacement_atr) < 0.2:
        direction = NEUTRAL
    elif net_bullish:
        direction = LONG
    else:
        direction = SHORT

    # --- State machine ---
    if direction == NEUTRAL:
        state = "NEUTRAL"
    elif retrace_or_reversal == "reversal":
        state = "REVERSAL_LONG" if direction == LONG else "REVERSAL_SHORT"
    elif retrace_or_reversal == "retracement":
        state = "RETRACING_LONG" if direction == LONG else "RETRACING_SHORT"
    elif exhausting:
        state = "LONG_EXHAUSTING" if direction == LONG else "SHORT_EXHAUSTING"
    elif accelerating:
        state = "LONG_ACCELERATING" if direction == LONG else "SHORT_ACCELERATING"
    elif strength < 30:
        state = "BUILDING_LONG" if direction == LONG else "BUILDING_SHORT"
    elif abs(accel_raw) > 1e-9:
        state = "LONG_DECELERATING" if direction == LONG else "SHORT_DECELERATING"
    else:
        state = "STRONG_LONG" if direction == LONG else "STRONG_SHORT"

    evidence = [
        f"RSI(14)={_rsi:.1f}",
        f"ROC5={roc5:+.2f}%, ROC12={roc12:+.2f}%, ROC20={roc20:+.2f}%",
        f"MACD histogram={hist:+.3f} ({'expanding' if expanding else 'contracting'})",
        f"Velocity: {'bullish' if velocity_bullish else 'bearish'} ({velocity_score:.1f}/{W_VELOCITY:.0f})",
        f"Acceleration: {'strengthening' if accelerating else 'weakening'} ({acceleration_score:.1f}/{W_ACCELERATION:.0f})",
        f"Impulse: body {body_atr:.2f} ATR, range {range_atr:.2f} ATR ({impulse_score:.1f}/{W_IMPULSE:.0f})",
        f"Momentum {'expansion' if expanding else 'contraction'} detected ({expansion_score:.1f}/{W_EXPANSION:.0f})",
        f"{streak} consecutive {'bullish' if streak_dir else 'bearish'} candle(s)",
        f"Exhaustion: {exhaustion_state} ({exhaustion_evidence}/4 factors)",
        f"Divergence: {(divergence.replace('_', ' ') if divergence else 'none')}",
        f"Retracement/reversal: {retrace_or_reversal or 'none'}",
        f"State: {state}",
    ]

    valid = strength >= 15.0 or direction == NEUTRAL

    return AgentResult(AGENT_ID, direction, round(confidence, 1), round(strength, 1),
                       evidence, [], timeframe, valid=valid)


from .observe import observe  # Step 6E — AnalysisObservation producer
