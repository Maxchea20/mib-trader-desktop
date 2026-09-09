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

# --- Breakout Lifecycle V2 constants ---
# Illustrative starting values, same caveat as every threshold in this
# codebase — a starting point for later research, not validated.
BREAKOUT_MAX_LIFETIME_BARS = 12  # stale-protection only, NOT an entry
# timer — same style as MAX_REVERSAL_CANDIDATE_BARS / BOS_RECOVERY_MAX_BARS
# in Market Structure.
PULLBACK_MIN_ATR = 0.20      # minimum retracement from the best point
# reached so far, before it counts as a genuine pullback rather than noise
RETEST_ZONE_ATR = 0.30       # how close to the broken level counts as
# "returned to retest it"
CONTINUATION_MIN_ATR = 0.50  # how far beyond the breakout candle's own
# close counts as genuine continuation
EXHAUSTION_LOOKBACK = 3      # candles examined for the shrinking-
# displacement/fading-volume exhaustion check

W_LIFECYCLE_INITIAL = 30.0
W_LIFECYCLE_PULLBACK = 10.0
W_LIFECYCLE_RETEST = 20.0
W_LIFECYCLE_HOLD = 20.0
W_LIFECYCLE_CONTINUATION = 20.0


class BreakoutLifecycle:
    """Post-breakout state machine: does price actually DO anything
    healthy after breaking the level, or does it fail?

    NONE -> BREAKOUT_DETECTED -> [PULLBACK] -> [RETEST] -> LEVEL_HOLD ->
    CONTINUATION, or FAILED (a decisive close back through the level, at
    any point) or EXPIRED (stale — no meaningful development within
    BREAKOUT_MAX_LIFETIME_BARS).

    PULLBACK and RETEST are optional stepping stones, not requirements —
    a strong breakout can go straight to CONTINUATION without ever
    meaningfully pulling back (spec section 25A), matching Test 12's
    "immediate continuation without retest."

    Plain class, not @dataclass, to avoid adding a dataclasses import
    dependency to this file for one small addition — same information,
    just written directly.
    """
    def __init__(self):
        self.state = "NONE"
        self.direction = "NEUTRAL"
        self.breakout_level = None
        self.breakout_timestamp = None
        self.breakout_price = None
        self.breakout_quality_score = 0.0
        self.bars_since_breakout = 0

        self.pullback_detected = False
        self.pullback_depth_atr = 0.0
        self.pullback_timestamp = None

        self.retest_detected = False
        self.retest_distance_atr = 0.0
        self.retest_timestamp = None
        self.bars_since_retest = 0

        self.level_hold = False
        self.continuation_detected = False
        self.continuation_distance_atr = 0.0

        self.breakout_exhaustion = False
        self.breakout_failed = False
        self.breakout_failure_reason = ""
        self.breakout_expired = False

        self.breakout_lifecycle_score = 0.0
        self.breakout_lifecycle_confidence = 0.0


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


def _track_breakout_lifecycle(high, low, close, open_, volume, timestamps) -> "BreakoutLifecycle":
    """Breakout Agent V2 — post-breakout lifecycle.

    Separate, additive pass over the SAME candle window already used for
    origin detection above — reuses _range_as_of() for the exact same
    anti-lookahead re-detection logic, just walked over a much longer
    horizon (BREAKOUT_MAX_LIFETIME_BARS) than the small
    MAX_FOLLOWTHROUGH_LOOKBACK window used for the existing origin score.

    A fresh breakout always supersedes whatever lifecycle was being
    tracked (spec section 22, "newer breakout priority") — exactly the
    same rule already used in Market Structure's BOS Recovery tracker.

    State only ever progresses forward through the happy path
    (BREAKOUT_DETECTED -> PULLBACK -> RETEST -> LEVEL_HOLD ->
    CONTINUATION) or exits via FAILED/EXPIRED — this version does not
    regress a CONTINUATION back down to PULLBACK if price dips again
    later. That's a deliberate v1 scope decision: prove the core
    lifecycle and failure path first, per the spec's own final
    instruction, rather than also handling every possible re-weakening
    pattern in the first pass.
    """
    n = len(close)
    ts_to_idx = {int(t): i for i, t in enumerate(timestamps)}
    tracked = None  # {origin_idx, direction, level, atr, breakout_price, max_favorable}
    result = BreakoutLifecycle()

    STATE_ORDER = ["BREAKOUT_DETECTED", "PULLBACK", "RETEST", "LEVEL_HOLD", "CONTINUATION"]

    def _advance(new_state):
        if STATE_ORDER.index(new_state) > STATE_ORDER.index(result.state):
            result.state = new_state

    for i in range(LOOKBACK, n):
        as_of_high, as_of_low = _range_as_of(high, low, i)
        c = float(close[i])
        is_fresh_break = as_of_high is not None and (c > as_of_high or c < as_of_low)

        if is_fresh_break:
            direction = LONG if c > as_of_high else SHORT
            # "Newer breakout priority" (spec section 22) means a
            # genuinely different structural context — NOT every new
            # local high a strong continuation naturally makes along the
            # way. If a lifecycle in the SAME direction is already
            # actively developing, a fresh same-direction break here is
            # just that move continuing, which the CONTINUATION state
            # below already captures — resetting back to
            # BREAKOUT_DETECTED here would be throwing away real
            # progress over something that isn't actually a new event.
            # Only reset when the tracked lifecycle is untracked,
            # opposite-direction, or already terminal (FAILED/EXPIRED).
            should_supersede = (
                tracked is None
                or tracked["direction"] != direction
                or result.state in ("FAILED", "EXPIRED")
            )
            if should_supersede:
                level = as_of_high if direction == LONG else as_of_low
                atr_at_origin = atr(high[:i + 1], low[:i + 1], close[:i + 1], 14) if i >= 15 else 0.0
                tracked = {"origin_idx": i, "direction": direction, "level": level,
                          "atr": atr_at_origin, "breakout_price": c, "max_favorable": c}
                result = BreakoutLifecycle()
                result.state = "BREAKOUT_DETECTED"
                result.direction = direction
                result.breakout_level = level
                result.breakout_timestamp = int(timestamps[i])
                result.breakout_price = c
                continue
            # else: same-direction continuation of an already-tracked
            # lifecycle — fall through to the normal per-candle update
            # below instead of resetting.

        if tracked is None:
            continue

        atr_ref = tracked["atr"]
        if atr_ref <= 0:
            continue

        result.bars_since_breakout = i - tracked["origin_idx"]
        long_dir = tracked["direction"] == LONG

        if long_dir:
            tracked["max_favorable"] = max(tracked["max_favorable"], c)
        else:
            tracked["max_favorable"] = min(tracked["max_favorable"], c)

        # --- Hard failure: decisive close back through the level ---
        failed = (c < tracked["level"]) if long_dir else (c > tracked["level"])
        if failed:
            result.state = "FAILED"
            result.breakout_failed = True
            result.breakout_failure_reason = (
                "Price closed below broken resistance after retest" if long_dir
                else "Price closed above broken support after retest"
            )
            tracked = None
            continue

        # --- Pullback: meaningful retracement from the best point reached ---
        pullback_depth_atr = abs(tracked["max_favorable"] - c) / atr_ref
        if pullback_depth_atr >= PULLBACK_MIN_ATR:
            _advance("PULLBACK")
            if not result.pullback_detected:
                result.pullback_detected = True
                result.pullback_timestamp = int(timestamps[i])
            result.pullback_depth_atr = round(pullback_depth_atr, 3)

        # --- Retest: price has returned close to the broken level itself ---
        retest_distance_atr = abs(c - tracked["level"]) / atr_ref
        if retest_distance_atr <= RETEST_ZONE_ATR:
            _advance("RETEST")
            if not result.retest_detected:
                result.retest_detected = True
                result.retest_timestamp = int(timestamps[i])
            result.retest_distance_atr = round(retest_distance_atr, 3)

        if result.retest_detected:
            result.bars_since_retest = i - ts_to_idx.get(result.retest_timestamp, i)

        # --- Level hold: after a retest, price moves away from the level
        # again without having failed --- (only meaningful once a retest
        # has actually happened; a breakout that never came back to
        # retest can't yet be said to have "held" a retest that didn't occur)
        if result.retest_detected and retest_distance_atr > RETEST_ZONE_ATR:
            moved_away = (c > tracked["level"]) if long_dir else (c < tracked["level"])
            if moved_away:
                _advance("LEVEL_HOLD")
                result.level_hold = True

        # --- Continuation: meaningful extension beyond the ORIGINAL
        # breakout candle's own close, in the breakout direction. This is
        # reachable directly from BREAKOUT_DETECTED too (no forced retest
        # requirement) — spec section 25A / Test 12. ---
        continuation_distance_atr = (c - tracked["breakout_price"]) / atr_ref if long_dir \
            else (tracked["breakout_price"] - c) / atr_ref
        if continuation_distance_atr >= CONTINUATION_MIN_ATR:
            _advance("CONTINUATION")
            result.continuation_detected = True
            result.continuation_distance_atr = round(continuation_distance_atr, 3)

        # --- Exhaustion: valid but weakening — shrinking recent bodies,
        # even while technically still in CONTINUATION/LEVEL_HOLD. A
        # separate flag, not its own state, per spec section 20. ---
        if i >= EXHAUSTION_LOOKBACK:
            recent_bodies = [abs(close[k] - open_[k]) for k in range(i - EXHAUSTION_LOOKBACK + 1, i + 1)]
            if len(recent_bodies) >= 2 and recent_bodies[-1] < recent_bodies[0] * 0.5:
                result.breakout_exhaustion = True

        # --- Expiry: stale, no meaningful development within the max
        # lifetime window. Does not override an already-reached
        # LEVEL_HOLD/CONTINUATION — those are meaningful outcomes even if
        # they took a while to develop. ---
        if result.bars_since_breakout >= BREAKOUT_MAX_LIFETIME_BARS and result.state in ("BREAKOUT_DETECTED", "PULLBACK", "RETEST"):
            result.state = "EXPIRED"
            result.breakout_expired = True
            tracked = None
            continue

        # --- Lifecycle score (0-100), per spec section 16 ---
        initial_score = W_LIFECYCLE_INITIAL  # the origin candle already passed the existing 7-factor scoring to get here
        pullback_score = W_LIFECYCLE_PULLBACK if (result.pullback_detected and PULLBACK_MIN_ATR <= result.pullback_depth_atr <= 1.5) else 0.0
        retest_score = clamp(1.0 - (result.retest_distance_atr / RETEST_ZONE_ATR), 0, 1) * W_LIFECYCLE_RETEST if result.retest_detected else 0.0
        hold_score = W_LIFECYCLE_HOLD if result.level_hold else 0.0
        continuation_score = clamp(result.continuation_distance_atr / (CONTINUATION_MIN_ATR * 2), 0, 1) * W_LIFECYCLE_CONTINUATION if result.continuation_detected else 0.0

        result.breakout_lifecycle_score = round(initial_score + pullback_score + retest_score + hold_score + continuation_score, 1)
        result.breakout_lifecycle_confidence = round(clamp(30.0 + result.breakout_lifecycle_score * 0.6, 0, 94), 1)

    return result


def _lifecycle_evidence(lc: "BreakoutLifecycle") -> list:
    """Builds the lifecycle evidence lines matching the format in spec
    section 28, from whatever the tracker actually found — never
    inventing a stage that wasn't reached."""
    if lc.state == "NONE":
        return []
    dir_word = "Bullish" if lc.direction == LONG else "Bearish"
    lines = [f"{dir_word} breakout lifecycle: {lc.state}",
             f"Breakout level: {lc.breakout_level:.1f} ({lc.bars_since_breakout} bar(s) ago)"]
    if lc.pullback_detected:
        lines.append(f"Pullback detected: {lc.pullback_depth_atr:.2f} ATR depth")
    if lc.retest_detected:
        lines.append(f"Retest: {lc.retest_distance_atr:.2f} ATR from level, {lc.bars_since_retest} bar(s) since")
    if lc.level_hold:
        lines.append("Level hold: broken level respected on retest")
    if lc.continuation_detected:
        lines.append(f"Continuation: {lc.continuation_distance_atr:.2f} ATR beyond breakout price")
    if lc.breakout_exhaustion:
        lines.append("Exhaustion warning: recent candles weakening (still valid, not failed)")
    if lc.breakout_failed:
        lines.append(f"Breakout FAILED: {lc.breakout_failure_reason}")
    if lc.breakout_expired:
        lines.append("Breakout lifecycle EXPIRED: no meaningful development within max lifetime")
    if lc.state not in ("FAILED", "EXPIRED", "NONE"):
        lines.append(f"Lifecycle score: {lc.breakout_lifecycle_score:.0f}/100, confidence {lc.breakout_lifecycle_confidence:.0f}%")
    return lines


def analyze(candles, timeframe: str) -> AgentResult:
    if len(candles) < 45:
        return neutral(AGENT_ID, timeframe, "Not enough candles")

    a = arrays(candles)
    high, low, close, open_, volume = a["high"], a["low"], a["close"], a["open"], a["volume"]
    ts = a["ts"]

    # Breakout Lifecycle V2 — runs independently of the origin-scoring
    # below, over a much longer horizon (BREAKOUT_MAX_LIFETIME_BARS vs
    # the tight MAX_FOLLOWTHROUGH_LOOKBACK used for origin quality). This
    # means it can report an in-progress PULLBACK/RETEST from a breakout
    # that happened several candles ago, even on a candle where the
    # origin-scoring below sees no fresh/active breakout of its own —
    # complementary information, not a duplicate. Failure here must
    # never affect the existing scoring, hence the isolated try/except.
    lifecycle = BreakoutLifecycle()
    try:
        lifecycle = _track_breakout_lifecycle(high, low, close, open_, volume, ts)
    except Exception:
        pass
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
        evidence += _lifecycle_evidence(lifecycle)

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
    evidence += _lifecycle_evidence(lifecycle)

    key_levels = [{"label": "Breakout Level", "price": round(level, 2), "type": "breakout"}]

    return AgentResult(AGENT_ID, direction, round(confidence, 1), round(strength, 1),
                       evidence, key_levels, timeframe, valid=valid)