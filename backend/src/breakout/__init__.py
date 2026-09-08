"""AGENT 2 — BREAKOUT / BREAKDOWN.

7-factor weighted model (Level Break, Displacement, Body Quality, Volume,
Range Expansion, ATR Expansion, Follow-through), weights sum to 100.

FOLLOW-THROUGH — genuinely multi-candle, not faked.
Earlier versions of this file either faked follow-through by looking at
candles BEFORE the breakout (measuring the run-up, not confirmation), or
honestly reported it as always 0 (correct, but couldn't reward a
breakout that's already proven itself over the last candle or two).

This version solves it properly WITHOUT needing any new persistent
storage: every call already receives a large window of recent candles
(not just the newest one), so instead of only ever checking "did a
breakout happen on THIS candle," it scans a small number of recent
candles backward (MAX_FOLLOWTHROUGH_LOOKBACK) to find the most recent
BREAKOUT ORIGIN — the earliest candle within that small window where
price first closed beyond its at-the-time range — treating any candles
after it as follow-through confirmation, not fresh signals of their own.

The critical correctness requirement: every measurement (the range being
broken, ATR, volume baseline) must be computed using ONLY the data that
would have been available AT the origin candle's own point in time —
never anything from after it. This mirrors the exact anti-lookahead
principle already used in market_state/builder.py's BOS/CHoCH history
and the walk-forward backtest's HTF regime slicing. Getting this wrong
would let a breakout look artificially better than it really was, by
quietly using information that didn't exist yet when it happened.

If the price already reversed back inside the range at any point after
a candidate origin, that origin is treated as a FAILED breakout, not an
active one — the scan then looks for a more recent, still-holding origin
instead.
"""
import numpy as np
from ..contract import AgentResult, LONG, SHORT, NEUTRAL, neutral, clamp
from ..indicators import arrays, atr

AGENT_ID = "breakout"

LOOKBACK = 20
MAX_FOLLOWTHROUGH_LOOKBACK = 3  # how many recent candles to scan backward
# for an active breakout origin. Higher = recognizes older breakouts as
# still "active" for longer, but also more compute per call (each
# candidate re-slices and re-runs ATR/volume calcs).

# --- Normalization anchors — as unvalidated as any other threshold in
# this codebase; named here explicitly so they're easy to find and
# backtest against a prior version before trusting this is better, not
# just more detailed-looking. ---
LEVEL_BREAK_ATR_CAP = 1.5
DISPLACEMENT_ATR_CAP = 2.0
VOLUME_ZSCORE_CAP = 2.0
RANGE_ATR_CAP = 2.0
ATR_EXPANSION_LOW = 0.8
ATR_EXPANSION_HIGH = 1.5

W_LEVEL = 20.0
W_DISPLACEMENT = 20.0
W_BODY = 15.0
W_VOLUME = 15.0
W_RANGE = 10.0
W_ATR_EXPANSION = 10.0
W_FOLLOWTHROUGH = 10.0


def _volume_zscore(volume: np.ndarray) -> float:
    """Z-score of the LATEST element in the given array against the
    mean/stdev of the 20 before it. Caller controls "latest" by slicing
    the array before calling this — that's how this stays anti-lookahead
    safe when checking an origin candle from a few bars back."""
    if len(volume) < 21:
        return 0.0
    base = volume[-21:-1]
    mean = float(np.mean(base))
    std = float(np.std(base))
    if std <= 1e-9:
        return 0.0
    return (float(volume[-1]) - mean) / std


def _rolling_atr_series(high, low, close, period=14, count=21):
    """Local helper — the shared indicators.atr() only returns a single
    current scalar, not a series, and ATR Expansion needs a short history
    of past ATR readings to compare the current one against."""
    n = len(close)
    if n < period + count:
        count = max(0, n - period)
    series = []
    for end in range(n - count, n + 1):
        if end < period + 1:
            continue
        series.append(atr(high[:end], low[:end], close[:end], period))
    return np.array(series, dtype=float)


def _range_as_of(high, low, end_index):
    """The LOOKBACK-bar high/low as they would have appeared to someone
    standing at `end_index`, using only candles strictly before it —
    i.e. high[end_index - LOOKBACK : end_index], never end_index itself
    or anything after. This is what makes re-checking a candle from a
    few bars back safe: it reconstructs exactly what the range looked
    like at that moment, not what it looks like now."""
    start = end_index - LOOKBACK
    if start < 0:
        return None, None
    return float(np.max(high[start:end_index])), float(np.min(low[start:end_index]))


def _find_active_breakout(high, low, close, n):
    """Scan up to MAX_FOLLOWTHROUGH_LOOKBACK recent candles, oldest to
    newest, for the earliest one that both (a) broke its at-the-time
    range and (b) hasn't been invalidated by a reversal on any candle
    since. Returns (origin_index, direction, level) or None.

    Scanning oldest-to-newest (not newest-to-oldest) is what lets a
    2-candle-old breakout get credit for 2 candles of follow-through,
    rather than the most recent candle always looking like a fresh,
    unconfirmed breakout even when it's really just holding an earlier
    one.
    """
    earliest = max(LOOKBACK, n - MAX_FOLLOWTHROUGH_LOOKBACK)
    for idx in range(earliest, n):
        as_of_high, as_of_low = _range_as_of(high, low, idx)
        if as_of_high is None:
            continue
        c = close[idx]
        if c > as_of_high:
            direction, level = LONG, as_of_high
        elif c < as_of_low:
            direction, level = SHORT, as_of_low
        else:
            continue

        # Has anything AFTER this origin already reversed back inside
        # the range? If so, this origin already failed — keep scanning
        # for a more recent, still-holding one instead.
        failed = False
        for j in range(idx + 1, n):
            if direction == LONG and close[j] < level:
                failed = True
                break
            if direction == SHORT and close[j] > level:
                failed = True
                break
        if failed:
            continue

        return idx, direction, level
    return None


def analyze(candles, timeframe: str) -> AgentResult:
    if len(candles) < 45:
        return neutral(AGENT_ID, timeframe, "Not enough candles")

    a = arrays(candles)
    high, low, close, open_, volume = a["high"], a["low"], a["close"], a["open"], a["volume"]
    n = len(close)

    found = _find_active_breakout(high, low, close, n)
    if found is None:
        recent_high = float(np.max(high[-LOOKBACK - 1:-1]))
        recent_low = float(np.min(low[-LOOKBACK - 1:-1]))
        c = float(close[-1])
        o_last = float(open_[-1])
        h_last, l_last = float(high[-1]), float(low[-1])
        v_last = volume
        pos = (c - recent_low) / max(recent_high - recent_low, 1e-9)

        _atr_neutral = atr(high, low, close, 14)

        # A wick that crosses the level without a confirming close is
        # real, distinct information — not the same thing as price
        # sitting quietly mid-range. Surfacing it separately is exactly
        # what distinguishes "an attempted breakout that got rejected"
        # from "nothing happening," per spec sections 6/13.
        rejection_lines = []
        if h_last > recent_high and c <= recent_high:
            rejection_lines = [
                "Bullish breakout rejection — price wicked above resistance but closed back inside range",
                f"Resistance: {recent_high:.1f}, wick high: {h_last:.1f}, close: {c:.1f}",
            ]
        elif l_last < recent_low and c >= recent_low:
            rejection_lines = [
                "Bearish breakdown rejection — price wicked below support but closed back inside range",
                f"Support: {recent_low:.1f}, wick low: {l_last:.1f}, close: {c:.1f}",
            ]

        # Full ATR-normalized diagnostic detail, matching the original
        # spec's own "inside range" example — this was previously only a
        # single generic line, even though the active LONG/SHORT path
        # already had this level of detail. Genuinely no breakout
        # occurred here, so there's nothing to compute Level/Displacement/
        # Body-quality/Follow-through FROM (those describe a specific
        # breakout candle, which doesn't exist in this branch) — Follow-
        # through is correctly "N/A", not a fabricated zero.
        if _atr_neutral > 0:
            dist_to_resistance_atr = (recent_high - c) / _atr_neutral
            dist_to_support_atr = (c - recent_low) / _atr_neutral
            candle_range_atr = (h_last - l_last) / _atr_neutral
            vol_z = _volume_zscore(v_last)
            atr_series = _rolling_atr_series(high, low, close, period=14, count=21)
            if len(atr_series) >= 2:
                atr_baseline = float(np.mean(atr_series[:-1]))
                atr_expansion = (_atr_neutral / atr_baseline) if atr_baseline > 0 else 1.0
            else:
                atr_expansion = 1.0
        else:
            dist_to_resistance_atr = dist_to_support_atr = candle_range_atr = vol_z = 0.0
            atr_expansion = 1.0

        evidence = (rejection_lines if rejection_lines else
                   [f"Price inside range ({recent_low:.1f}–{recent_high:.1f})", "No confirmed breakout"])
        evidence += [
            f"Distance to resistance: {dist_to_resistance_atr:.2f} ATR",
            f"Distance to support: {dist_to_support_atr:.2f} ATR",
            f"Volume Z-score: {vol_z:+.2f}",
            f"Candle range: {candle_range_atr:.2f} ATR",
            f"ATR expansion: {atr_expansion:.2f}x",
            "Follow-through: N/A",
        ]

        return AgentResult(AGENT_ID, NEUTRAL, clamp(30 + abs(pos - 0.5) * 20, 0, 60),
                           20, evidence,
                           [{"label": "Range High", "price": round(recent_high, 2), "type": "resistance"},
                            {"label": "Range Low", "price": round(recent_low, 2), "type": "support"}],
                           timeframe, valid=True)

    origin_idx, direction, level = found
    bars_since_origin = (n - 1) - origin_idx  # 0 = fresh this candle, 1 or 2 = held for that many bars since

    # Everything below is computed AS OF the origin candle — using only
    # data up to and including it — so a breakout's scored quality
    # reflects how it actually happened, not what unrelated later
    # candles happen to look like.
    o_high, o_low, o_close, o_open = high[:origin_idx + 1], low[:origin_idx + 1], close[:origin_idx + 1], open_[:origin_idx + 1]
    o_volume = volume[:origin_idx + 1]
    _atr = atr(o_high, o_low, o_close, 14)
    if _atr <= 0:
        return neutral(AGENT_ID, timeframe, "Invalid ATR")

    c = float(close[origin_idx])
    o = float(open_[origin_idx])
    h = float(high[origin_idx])
    l = float(low[origin_idx])

    # --- 1. Level Break (20 pts) ---
    penetration_atr = (c - level) / _atr if direction == LONG else (level - c) / _atr
    level_score = clamp(penetration_atr / LEVEL_BREAK_ATR_CAP, 0, 1) * W_LEVEL

    # --- 2. Displacement (20 pts) ---
    displacement_atr = abs(c - o) / _atr
    displacement_score = clamp(displacement_atr / DISPLACEMENT_ATR_CAP, 0, 1) * W_DISPLACEMENT

    # --- 3. Body Quality (15 pts) ---
    candle_range = max(h - l, 1e-9)
    body_ratio = abs(c - o) / candle_range
    close_location = (c - l) / candle_range if direction == LONG else (h - c) / candle_range
    body_score = clamp(0.5 * body_ratio + 0.5 * close_location, 0, 1) * W_BODY

    # --- 4. Volume (15 pts) ---
    vol_z = _volume_zscore(o_volume)
    volume_score = clamp(vol_z / VOLUME_ZSCORE_CAP, 0, 1) * W_VOLUME

    # --- 5. Range Expansion (10 pts) ---
    range_atr = candle_range / _atr
    range_score = clamp(range_atr / RANGE_ATR_CAP, 0, 1) * W_RANGE

    # --- 6. ATR Expansion (10 pts) ---
    atr_series = _rolling_atr_series(o_high, o_low, o_close, period=14, count=21)
    if len(atr_series) >= 2:
        atr_baseline = float(np.mean(atr_series[:-1]))
        atr_expansion = (_atr / atr_baseline) if atr_baseline > 0 else 1.0
    else:
        atr_expansion = 1.0
    atr_expansion_score = clamp(
        (atr_expansion - ATR_EXPANSION_LOW) / (ATR_EXPANSION_HIGH - ATR_EXPANSION_LOW), 0, 1
    ) * W_ATR_EXPANSION

    # --- 7. Follow-through (10 pts) — the genuinely multi-candle part.
    # 0 confirming candles since origin = 0 pts (fresh, matches the
    # honest "awaiting confirmation" behavior from before).
    # 1 confirming candle = half credit. 2+ = full credit. Each
    # confirming candle must have CLOSED further in the breakout's
    # direction than the origin candle did, not just stayed above the
    # level — a candle that holds but goes nowhere is weaker evidence
    # than one that keeps extending.
    confirmations = 0
    for j in range(origin_idx + 1, n):
        extended = close[j] > c if direction == LONG else close[j] < c
        if extended:
            confirmations += 1
    followthrough_score = clamp(confirmations / 2.0, 0, 1) * W_FOLLOWTHROUGH

    confidence = clamp(
        level_score + displacement_score + body_score + volume_score
        + range_score + atr_expansion_score + followthrough_score,
        0, 100,
    )
    strength = confidence

    valid = penetration_atr >= 0.5 or vol_z >= 1.0

    age_note = "this candle" if bars_since_origin == 0 else f"{bars_since_origin} candle(s) ago"
    evidence = [
        f"{'Bullish' if direction == LONG else 'Bearish'} break of {level:.1f} ({age_note})",
        f"Level penetration: {penetration_atr:.2f} ATR → {level_score:.1f}/{W_LEVEL:.0f}",
        f"Displacement: {displacement_atr:.2f} ATR → {displacement_score:.1f}/{W_DISPLACEMENT:.0f}",
        f"Body ratio {body_ratio:.0%}, close location {close_location:.0%} → {body_score:.1f}/{W_BODY:.0f}",
        f"Volume Z-score {vol_z:+.2f} → {volume_score:.1f}/{W_VOLUME:.0f}",
        f"Range expansion {range_atr:.2f} ATR → {range_score:.1f}/{W_RANGE:.0f}",
        f"ATR expansion {atr_expansion:.2f}x → {atr_expansion_score:.1f}/{W_ATR_EXPANSION:.0f}",
        (f"Follow-through: {confirmations} confirming candle(s) → {followthrough_score:.1f}/{W_FOLLOWTHROUGH:.0f}"
         if bars_since_origin > 0 else f"Follow-through: awaiting confirmation → 0.0/{W_FOLLOWTHROUGH:.0f}"),
    ]
    if not valid:
        evidence.append("Marked low-reliability (weak penetration, no volume confirmation)")

    key_levels = [{"label": "Breakout Level", "price": round(level, 2), "type": "breakout"}]

    return AgentResult(AGENT_ID, direction, round(confidence, 1), round(strength, 1),
                       evidence, key_levels, timeframe, valid=valid)