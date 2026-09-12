from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from ..indicators import atr as _shared_atr, find_pivots as _shared_find_pivots

from .models import (
    BosRecoveryState,
    LocationState,
    MarketState,
    ReversalCandidate,
    StructureEvent,
    StructureState,
    SwingPoint,
    VolatilityState,
)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------
# Dynamic pivot window
#
# A fixed candle-count window means something different on every
# timeframe: 5 candles on 1m is 5 minutes of noise-filtering, but 5
# candles on 1d is a full week. This scales the window so it represents
# a comparable SPAN OF REAL TIME across timeframes, anchored to 15m
# (the live trading timeframe) so nothing changes there.
#
# Anchor: 15m currently uses window=5, i.e. 5*15=75 minutes each side.
# Every other timeframe solves for the candle count that covers roughly
# that same 75-minute span, clamped to a sane range so 1m doesn't demand
# an absurdly long wait (75 candles) and 1d doesn't collapse to under 3
# (too few to call a real pivot).
# ---------------------------------------------------------------------
PIVOT_REFERENCE_MINUTES = 75
PIVOT_MIN_WINDOW = 3
PIVOT_MAX_WINDOW = 15

# --- Reversal Monitor V1 constants ---
# All illustrative/unvalidated starting parameters, same caveat as every
# other threshold in this codebase — named here so they're easy to find
# and tune once real data exists to calibrate against.
MAX_REVERSAL_CANDIDATE_BARS = 12  # max bars a candidate stays pending
# before expiring as FAILED — NOT a "wait N bars then confirm" timer,
# purely a stale-candidate safety net (see _track_reversal docstring).
MIN_MEANINGFUL_FOLLOWTHROUGH_ATR = 0.30
FOLLOWTHROUGH_FULL_ATR = 1.0     # follow-through score maxes out here
LEVEL_HOLD_FULL_ATR = 0.5        # level-hold score maxes out here
BREAK_QUALITY_FULL_ATR = 1.0     # break-quality score maxes out here
RETEST_TOLERANCE_ATR = 0.3       # price must come back within this much
# of the broken level (without closing through it) to count as a retest
REVERSAL_CONFIRMATION_THRESHOLD = 70.0
W_LEVEL_HOLD = 30.0
W_FOLLOWTHROUGH = 25.0
W_NEW_SWING = 30.0
W_BREAK_QUALITY = 15.0

# --- Early Reversal Detection (BOS Recovery) constants ---
# Illustrative starting values, same caveat as everywhere else — do not
# treat these as validated, they're a starting point for later research.
BOS_RECOVERY_MAX_BARS = 12  # stale-protection only, NOT an entry timer —
# a BOS that hasn't been reclaimed within this many bars stops being a
# valid reversal reference. Same style constant as MAX_REVERSAL_CANDIDATE_BARS.
BOS_MAX_ADVERSE_DISTANCE_ATR = 3.0  # if price travels this many ATRs
# past the BOS before recovering, treat the BOS as having represented a
# different market event entirely, not a simple pullback-and-reclaim.
EARLY_REVERSAL_TRIGGER_SCORE = 70.0
RECOVERY_DEVELOPING_SCORE = 40.0
RECOVERY_PENETRATION_FULL_ATR = 1.0
RECOVERY_DISPLACEMENT_FULL_ATR = 1.5
RECOVERY_FOLLOWTHROUGH_FULL_ATR = 1.0
W_RECOVERY_BODY = 20.0
W_RECOVERY_PENETRATION = 20.0
W_RECOVERY_DISPLACEMENT = 20.0
W_RECOVERY_VOLUME = 15.0
W_RECOVERY_FOLLOWTHROUGH = 10.0
W_RECOVERY_RETEST = 10.0
W_RECOVERY_STRUCTURAL = 5.0

_TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
}


def pivot_window_for_timeframe(timeframe: str) -> int:
    """Returns the pivot left/right window size (each side) for a given
    timeframe. See module docstring above for the reasoning."""
    minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if not minutes:
        return 5  # unknown timeframe string — fall back to the old fixed default
    raw = PIVOT_REFERENCE_MINUTES / minutes
    return max(PIVOT_MIN_WINDOW, min(PIVOT_MAX_WINDOW, round(raw)))


def _get_arrays(candles) -> Dict[str, np.ndarray]:
    return {
        "ts": np.asarray([float(c["ts"]) for c in candles], dtype=float),
        "open": np.asarray([float(c["open"]) for c in candles], dtype=float),
        "high": np.asarray([float(c["high"]) for c in candles], dtype=float),
        "low": np.asarray([float(c["low"]) for c in candles], dtype=float),
        "close": np.asarray([float(c["close"]) for c in candles], dtype=float),
        "volume": np.asarray(
            [float(c.get("volume", 0.0)) for c in candles],
            dtype=float,
        ),
    }


def _find_pivots(
    highs: np.ndarray,
    lows: np.ndarray,
    left: int = 3,
    right: int = 3,
) -> List[Dict[str, Any]]:
    # Pivot centralization (2026-09-12 audit, Step 4): this was a
    # second, independent pivot-detection algorithm alongside
    # indicators.py::find_pivots() (also used directly by
    # support_resistance, pattern, elliott_wave, fibonacci). Verified
    # numerically IDENTICAL across 414 synthetic scenarios --
    # tie-heavy integer data, monotonic runs, flat arrays, spikes,
    # asymmetric left/right windows, and near-boundary short arrays
    # (see tests/test_pivot_centralization.py). The two only ever
    # differed in output SCHEMA, not in which bars get selected as
    # pivots: indicators.find_pivots() returns {"i","price","type":
    # "H"/"L"}, while every caller in THIS module expects {"index",
    # "price","type":"HIGH"/"LOW"} (see the SwingPoint construction a
    # few lines below build_market_state() calls this). Delegates to
    # the shared algorithm and translates the schema, rather than
    # forcing every call site in this file to change.
    raw = _shared_find_pivots(highs, lows, left=left, right=right)
    _TYPE_MAP = {"H": "HIGH", "L": "LOW"}
    return [
        {"index": p["i"], "price": p["price"], "type": _TYPE_MAP[p["type"]]}
        for p in raw
    ]


def _calculate_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> float:
    # ATR centralization (2026-09-12 audit, Step 3): this was a second,
    # independent reimplementation of indicators.py::atr() -- verified
    # numerically IDENTICAL to it across every input tested, including
    # n=0/1/2 and flat-candle edge cases (see
    # tests/test_atr_centralization.py). Delegates directly now instead
    # of carrying parallel math that has to be kept in sync by hand.
    return _shared_atr(highs, lows, closes, period)


def _calculate_swing_strength(
    price: float,
    surrounding_prices: List[float],
    atr: float,
) -> float:
    if atr <= 0 or not surrounding_prices:
        return 0.0

    distance = max(abs(price - p) for p in surrounding_prices)

    strength = (distance / atr) * 20.0

    return float(np.clip(strength, 0.0, 100.0))


def _bos_recovery_volume_zscore(volume: np.ndarray) -> float:
    """Fresh, local helper — deliberately NOT reused from the Breakout
    agent's identical-looking one. Same reasoning as the Reversal
    Monitor's own docstring: the general shape of "Z-score against a
    20-period baseline" is a reasonable idea to reuse conceptually, but
    the actual code stays separate per-module rather than importing
    across agent boundaries that should stay independent."""
    if len(volume) < 21:
        return 0.0
    base = volume[-21:-1]
    mean = float(np.mean(base))
    std = float(np.std(base))
    if std <= 1e-9:
        return 0.0
    return (float(volume[-1]) - mean) / std


def _track_bos_recovery(events, high, low, close, open_, volume, timestamps,
                        current_regime: str, current_direction: str) -> "BosRecoveryState":
    """Early Reversal Detection via BOS Recovery.

    Distinct from, and additive to, _track_reversal() above:
    _track_reversal only starts watching once a CHoCH has already fired
    (a confirmed structural reversal signal). This tracks every BOS (a
    continuation break) as a potential EARLY reversal reference — if
    price later closes back through that level with enough quality, the
    Brain gets an early heads-up well before a full CHoCH would exist.

    "EARLY != FULL CONFIRMATION": TRIGGERED means the recovery is
    meaningful enough to evaluate, not that the reversal is proven. The
    original opposite swing (the LH/HL that a CHoCH would eventually
    need to break) remains available separately as stronger, later
    confirmation — see `opposite_structural_level` on the result.

    Only the single MOST RECENT BOS (per continuation direction) is ever
    tracked — a newer BOS immediately supersedes an older, not-yet-
    recovered one, so a stale reference can never independently fire a
    reversal once the market has moved on to a fresher structural break.

    Anti-lookahead: identical discipline to _track_reversal — at loop
    position i, only candles [0..i] are ever read; ATR is always
    recomputed sliced to i, never using the whole window's "current" ATR
    for a historical evaluation.
    """
    n = len(close)
    ts_to_idx = {int(ts): i for i, ts in enumerate(timestamps)}

    bos_by_idx = {}
    for e in events:
        if e.event == "BOS" and e.timestamp is not None:
            idx = ts_to_idx.get(int(e.timestamp))
            if idx is not None:
                bos_by_idx[idx] = e

    tracked = None  # {bos_idx, bos_level, bos_ts, direction, extreme}
    result = BosRecoveryState()

    for i in range(n):
        if i in bos_by_idx:
            evt = bos_by_idx[i]
            recovery_direction = "LONG" if evt.direction == "SHORT" else "SHORT"
            # A fresh BOS always supersedes whatever was being tracked —
            # "newer BOS priority" (spec section 3B): an older,
            # not-yet-recovered BOS becomes stale the moment a newer one
            # in the same family occurs, since the market has already
            # established a more current structural context.
            tracked = {
                "bos_idx": i, "bos_level": evt.reference_price,
                "bos_ts": int(timestamps[i]), "direction": recovery_direction,
                "extreme": float(close[i]),
            }
            result = BosRecoveryState()
            continue

        if tracked is None:
            continue

        c = float(close[i])
        if tracked["direction"] == "LONG":
            tracked["extreme"] = min(tracked["extreme"], c)
        else:
            tracked["extreme"] = max(tracked["extreme"], c)

        age = i - tracked["bos_idx"]
        atr_now = _calculate_atr(high[:i + 1], low[:i + 1], close[:i + 1], 14) if i >= 15 else 0.0

        # ---------------------------------------------------------
        # POST-TRIGGER: monitor for failure or strengthening
        # ---------------------------------------------------------
        if result.reversal_triggered:
            failed = (c < tracked["bos_level"]) if result.direction == "LONG" else (c > tracked["bos_level"])
            if failed:
                result.state = "FAILED"
                result.reason = "Price closed back through the recovered BOS level"
                tracked = None
                continue

            result.bars_since_trigger = i - ts_to_idx[result.reversal_trigger_timestamp]

            if result.direction == "LONG":
                followthrough = (c - result.reversal_trigger_price) / atr_now if atr_now > 0 else 0.0
                if not result.recovery_retest and atr_now > 0 and (c - tracked["bos_level"]) / atr_now <= 0.3:
                    result.recovery_retest = True
            else:
                followthrough = (result.reversal_trigger_price - c) / atr_now if atr_now > 0 else 0.0
                if not result.recovery_retest and atr_now > 0 and (tracked["bos_level"] - c) / atr_now <= 0.3:
                    result.recovery_retest = True
            result.recovery_followthrough = max(result.recovery_followthrough, followthrough)

            confirm_score = (
                clamp(result.recovery_followthrough / RECOVERY_FOLLOWTHROUGH_FULL_ATR, 0, 1) * 50.0
                + (25.0 if result.recovery_retest else 0.0)
                + (25.0 if ((result.direction == current_direction) if current_direction != "NEUTRAL" else False) else 0.0)
            )
            result.reversal_confirmation_score = confirm_score
            result.reversal_confirmation_confidence = clamp(30.0 + confirm_score * 0.6, 0, 94)
            result.state = "CONFIRMED" if confirm_score >= 70 else "CONFIRMING"
            continue

        # ---------------------------------------------------------
        # PRE-TRIGGER: has price recovered (BODY CLOSE only) yet?
        # ---------------------------------------------------------
        recovered = (c > tracked["bos_level"]) if tracked["direction"] == "LONG" else (c < tracked["bos_level"])
        if not recovered:
            if age >= BOS_RECOVERY_MAX_BARS:
                result.state = "EXPIRED"
                result.reason = "No recovery within max age"
                tracked = None
            continue

        # BOS relevance validation — BEFORE scoring quality, per spec
        # ordering. A recovery from a BOS that's too old or that price
        # traveled too far away from isn't treated as the same event.
        distance_atr = abs(tracked["extreme"] - tracked["bos_level"]) / atr_now if atr_now > 0 else 0.0
        result.bos_distance_from_extreme_atr = round(distance_atr, 3)
        relevant = age <= BOS_RECOVERY_MAX_BARS and distance_atr <= BOS_MAX_ADVERSE_DISTANCE_ATR
        if not relevant:
            result.state = "EXPIRED"
            result.bos_relevance_ok = False
            result.reason = (
                "BOS aged out beyond max bars" if age > BOS_RECOVERY_MAX_BARS
                else f"Price moved too far away ({distance_atr:.2f} ATR, max {BOS_MAX_ADVERSE_DISTANCE_ATR})"
            )
            tracked = None
            continue

        if not result.bos_recovery:
            result.direction = tracked["direction"]
            result.broken_bos_level = tracked["bos_level"]
            result.bos_timestamp = tracked["bos_ts"]
            result.bos_recovery = True
            result.bos_recovery_timestamp = int(timestamps[i])
            result.bos_recovery_price = c

        result.bos_recovery_age_bars = age
        result.bars_since_recovery = i - ts_to_idx[result.bos_recovery_timestamp]

        # --- Dynamic recovery quality (0-100), close-based only ---
        o = float(open_[i])
        h, l = float(high[i]), float(low[i])
        rng = max(h - l, 1e-9)
        body = abs(c - o)
        directional_body = (c > o) if result.direction == "LONG" else (c < o)
        body_ratio = body / rng
        body_score = body_ratio * W_RECOVERY_BODY if directional_body else body_ratio * W_RECOVERY_BODY * 0.3

        penetration_atr = abs(c - tracked["bos_level"]) / atr_now if atr_now > 0 else 0.0
        penetration_score = clamp(penetration_atr / RECOVERY_PENETRATION_FULL_ATR, 0, 1) * W_RECOVERY_PENETRATION

        displacement_atr = rng / atr_now if atr_now > 0 else 0.0
        displacement_score = clamp(displacement_atr / RECOVERY_DISPLACEMENT_FULL_ATR, 0, 1) * W_RECOVERY_DISPLACEMENT

        vol_z = _bos_recovery_volume_zscore(volume[:i + 1])
        # "If volume is unavailable or unreliable, do not heavily
        # penalize" — a non-positive/neutral Z-score contributes 0
        # rather than a negative score, it just doesn't help either.
        volume_score = clamp(vol_z / 2.0, 0, 1) * W_RECOVERY_VOLUME

        # Follow-through/retest are correctly ~0 on the trigger candle
        # itself — nothing has happened YET after it — but a strong
        # enough candle on factors A-D (+structure) can still reach the
        # trigger threshold without them, per the explicit "do not wait
        # for another candle" requirement.
        followthrough_score = clamp(result.recovery_followthrough / RECOVERY_FOLLOWTHROUGH_FULL_ATR, 0, 1) * W_RECOVERY_FOLLOWTHROUGH
        retest_score = W_RECOVERY_RETEST if result.recovery_retest else 0.0

        structural_agrees = (current_regime in ("BULLISH", "EXPANSION") if result.direction == "LONG"
                             else current_regime in ("BEARISH", "EXPANSION"))
        structural_score = W_RECOVERY_STRUCTURAL if structural_agrees else 0.0

        score = (body_score + penetration_score + displacement_score + volume_score
                + followthrough_score + retest_score + structural_score)

        result.recovery_quality_score = round(score, 1)
        result.recovery_penetration_atr = round(penetration_atr, 3)
        result.recovery_body_quality = round(body_ratio, 3)
        result.recovery_displacement_atr = round(displacement_atr, 3)
        result.recovery_volume_quality = round(vol_z, 3)

        if score >= EARLY_REVERSAL_TRIGGER_SCORE:
            result.state = "TRIGGERED"
            result.reversal_triggered = True
            result.reversal_trigger_price = c
            result.reversal_trigger_timestamp = int(timestamps[i])
            result.reversal_trigger_confidence = round(clamp(50.0 + score * 0.44, 0, 94), 1)
        elif score >= RECOVERY_DEVELOPING_SCORE:
            result.state = "DEVELOPING"
        else:
            result.state = "BOS_RECOVERY_WATCH"

    return result


def _track_reversal(events, swing_highs, swing_lows, high, low, close,
                     timestamps, pivot_right: int) -> "ReversalCandidate":
    """Reversal Monitor V1.

    A CHoCH is an early warning, not a confirmed reversal. This walks the
    SAME candle window that already produced `events` a second time,
    tracking whatever the most recent CHoCH has done since it fired:
    does price hold beyond the broken level, does it show real
    follow-through, and — the requirement that actually distinguishes a
    genuine transition from a false alarm — does a NEW confirmed swing
    subsequently form agreeing with the new direction (a fresh Higher
    Low after a bullish CHoCH, a fresh Lower High after a bearish one).

    This is a SEPARATE, ADDITIVE pass — it does not modify, and does not
    need to touch, the existing BOS/CHoCH detection loop at all. It only
    reads that loop's output (`events`) plus the already-confirmed swing
    lists, so the existing, tested detection logic is completely
    unaffected by this addition.

    Anti-lookahead: at loop position i, only candles [0..i] are ever
    read. A swing point only counts as "known" once its own pivot_right
    confirmation lag has passed (s.index + pivot_right <= i) — the exact
    same confirmation-timing rule the existing event loop already uses
    for activating swing highs/lows, applied here too so a reversal is
    never confirmed using a swing that wouldn't actually have been known
    yet at that point in history.

    Deliberately NOT the Breakout agent's follow-through code — the
    question here is different ("did structure actually transition,"
    not "did price clear a level convincingly"), so this is written
    fresh, even though the general shape (walk the given window forward
    once, no external state needed) is the same reasonable idea.
    """
    n = len(close)
    ts_to_idx = {int(ts): i for i, ts in enumerate(timestamps)}
    event_by_idx = {}
    for e in events:
        if e.timestamp is not None:
            idx = ts_to_idx.get(int(e.timestamp))
            if idx is not None:
                event_by_idx[idx] = e

    candidate = ReversalCandidate()

    for i in range(n):
        evt = event_by_idx.get(i)

        if evt is not None and evt.event == "CHoCH":
            # A fresh CHoCH always starts a NEW candidate — structure
            # has moved on again, so any prior still-pending candidate
            # is superseded rather than left dangling.
            atr_at_choch = _calculate_atr(high[:i + 1], low[:i + 1], close[:i + 1], 14) if i >= 15 else 0.0
            candidate = ReversalCandidate(
                state="CANDIDATE",
                direction=evt.direction,
                choch_timestamp=int(timestamps[i]),
                choch_price=float(close[i]),
                broken_level=evt.reference_price,
                origin_structure_direction=("SHORT" if evt.direction == "LONG" else "LONG"),
                atr_at_choch=atr_at_choch,
                choch_distance_atr=evt.distance_atr,
            )
            continue  # the CHoCH candle itself isn't "subsequent" yet

        if candidate.state != "CANDIDATE":
            continue

        choch_idx = ts_to_idx.get(candidate.choch_timestamp)
        if choch_idx is None or i <= choch_idx:
            continue

        c = float(close[i])
        candidate.candles_since_choch = i - choch_idx
        atr_ref = candidate.atr_at_choch

        if candidate.direction == "LONG":
            excursion = c - candidate.choch_price
            candidate.max_favorable_excursion = max(candidate.max_favorable_excursion, excursion)
            candidate.max_adverse_excursion = min(candidate.max_adverse_excursion, excursion)

            # A) Broken level hold — hard fail on close-through, per
            # spec: "do not treat a wick alone as confirmation" applies
            # symmetrically here — a wick below the level doesn't fail
            # the candidate either, only a CLOSE does.
            if c < candidate.broken_level:
                candidate.state = "FAILED"
                candidate.reason = "Close returned below broken LH"
                continue

            if atr_ref > 0 and not candidate.retested and (c - candidate.broken_level) / atr_ref <= RETEST_TOLERANCE_ATR:
                candidate.retested = True

            candidate.followthrough_atr = (c - candidate.choch_price) / atr_ref if atr_ref > 0 else 0.0
        else:  # SHORT
            excursion = candidate.choch_price - c
            candidate.max_favorable_excursion = max(candidate.max_favorable_excursion, excursion)
            candidate.max_adverse_excursion = min(candidate.max_adverse_excursion, excursion)

            if c > candidate.broken_level:
                candidate.state = "FAILED"
                candidate.reason = "Close returned above broken HL"
                continue

            if atr_ref > 0 and not candidate.retested and (candidate.broken_level - c) / atr_ref <= RETEST_TOLERANCE_ATR:
                candidate.retested = True

            candidate.followthrough_atr = (candidate.choch_price - c) / atr_ref if atr_ref > 0 else 0.0

        # C) New structural swing — must be CONFIRMED as of candle i
        # (respecting the same pivot_right lag the rest of the system
        # uses), and must itself form the correct HL/LH relative to the
        # swing immediately before it in the sequence.
        if not candidate.new_swing_confirmed:
            if candidate.direction == "LONG":
                relevant = [s for s in swing_lows if s.index > choch_idx and s.index + pivot_right <= i]
                if relevant:
                    newest = relevant[-1]
                    prior = [s for s in swing_lows if s.index < newest.index]
                    if prior and newest.price > prior[-1].price:
                        candidate.new_swing_confirmed = True
            else:
                relevant = [s for s in swing_highs if s.index > choch_idx and s.index + pivot_right <= i]
                if relevant:
                    newest = relevant[-1]
                    prior = [s for s in swing_highs if s.index < newest.index]
                    if prior and newest.price < prior[-1].price:
                        candidate.new_swing_confirmed = True

        # D) Confirmation score — a quality measurement, not a
        # replacement for the structural swing requirement below.
        level_hold_score = clamp(
            abs(c - candidate.broken_level) / atr_ref / LEVEL_HOLD_FULL_ATR, 0, 1
        ) * W_LEVEL_HOLD if atr_ref > 0 else 0.0
        followthrough_score = clamp(candidate.followthrough_atr / FOLLOWTHROUGH_FULL_ATR, 0, 1) * W_FOLLOWTHROUGH
        new_swing_score = W_NEW_SWING if candidate.new_swing_confirmed else 0.0
        break_quality_score = clamp(candidate.choch_distance_atr / BREAK_QUALITY_FULL_ATR, 0, 1) * W_BREAK_QUALITY

        candidate.score = level_hold_score + followthrough_score + new_swing_score + break_quality_score

        # Confirmation requires BOTH the score threshold AND the
        # structural swing condition — score alone is not sufficient,
        # per the explicit requirement that this not become "high score
        # without the market actually having transitioned."
        if candidate.score >= REVERSAL_CONFIRMATION_THRESHOLD and candidate.new_swing_confirmed:
            candidate.state = "CONFIRMED"
            candidate.reason = "Reversal confirmed"
            continue

        # Stale-candidate protection — a maximum validity window, not a
        # blind "wait N bars then confirm" timer. Confirmation still
        # only happens from the market-behavior conditions above; this
        # only prevents an indefinitely-pending candidate.
        if candidate.candles_since_choch >= MAX_REVERSAL_CANDIDATE_BARS:
            candidate.state = "FAILED"
            candidate.reason = "REVERSAL_CANDIDATE_EXPIRED"
            continue

    # Confidence/strength — deliberately different concepts. Confidence
    # never jumps straight to a high number just because a CHoCH fired;
    # it has to earn it through the score. Strength reflects the actual
    # size of the move achieved so far (MFE), independent of how
    # confident the evidence is that it's real.
    if candidate.state == "CANDIDATE":
        candidate.confidence = clamp(15.0 + candidate.score * 0.55, 0, 70)
    elif candidate.state == "CONFIRMED":
        candidate.confidence = clamp(50.0 + candidate.score * 0.44, 0, 94)
    else:
        candidate.confidence = 0.0

    if candidate.atr_at_choch > 0:
        candidate.strength = clamp(abs(candidate.max_favorable_excursion) / candidate.atr_at_choch * 25.0, 0, 100)
    else:
        candidate.strength = 0.0

    return candidate


def _build_structure(
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
    close: float,
    atr: float,
    candles=None,
    pivot_right: int = 5,
) -> StructureState:
    state = StructureState()

    if len(swing_highs) >= 2:
        previous_high = swing_highs[-2]
        last_high = swing_highs[-1]

        state.previous_high = previous_high.price
        state.last_high = last_high.price

        state.hh = last_high.price > previous_high.price
        state.lh = last_high.price < previous_high.price

    if len(swing_lows) >= 2:
        previous_low = swing_lows[-2]
        last_low = swing_lows[-1]

        state.previous_low = previous_low.price
        state.last_low = last_low.price

        state.hl = last_low.price > previous_low.price
        state.ll = last_low.price < previous_low.price

    # ---------------------------------------------------------
    # Structural regime — purely from the swing SEQUENCE (HH/HL/
    # LH/LL). This is a classification of the swing SHAPE, and is
    # intentionally independent of directional bias — see below.
    # ---------------------------------------------------------

    if state.hh and state.hl:
        state.regime = "BULLISH"
    elif state.lh and state.ll:
        state.regime = "BEARISH"
    elif state.lh and state.hl:
        state.regime = "COMPRESSION"
    elif state.hh and state.ll:
        state.regime = "EXPANSION"
    else:
        state.regime = "TRANSITION"

    # ---------------------------------------------------------
    # Structure sequence
    # ---------------------------------------------------------

    if state.hh and state.hl:
        state.structure_sequence = "HH_HL"
    elif state.lh and state.ll:
        state.structure_sequence = "LH_LL"
    elif state.lh and state.hl:
        state.structure_sequence = "LH_HL"
    elif state.hh and state.ll:
        state.structure_sequence = "HH_LL"
    else:
        state.structure_sequence = "MIXED"

        # ---------------------------------------------------------
    # Historical BOS / CHoCH events
    #
    # Pivot-cross model:
    # - each confirmed swing becomes an active structural level
    # - a level can trigger only once
    # - close must CROSS the level
    # - current structure direction determines BOS vs CHoCH
    # ---------------------------------------------------------

    # REGIME != DIRECTION.
    #
    # A market can be structurally EXPANDING (HH+LL) or COMPRESSING
    # (LH+HL) while still carrying a clear directional bias from its
    # most recently confirmed break — regime describes the swing SHAPE,
    # direction describes CURRENT BIAS, and they are genuinely
    # independent questions. Previously this variable lived only inside
    # the `if candles:` block below, and its final value was discarded
    # entirely — direction was instead hardcoded to "NEUTRAL" for any
    # regime except pure BULLISH/BEARISH, silently throwing away exactly
    # the information this variable already tracked correctly.
    #
    # Equivalent to LuxAlgo's swingTrend.bias.
    structure_direction = "NEUTRAL"

    events: List[StructureEvent] = []

    if candles:
        closes = np.asarray(
            [float(c["close"]) for c in candles],
            dtype=float,
        )

        timestamps = np.asarray(
            [int(c["ts"]) for c in candles],
            dtype=np.int64,
        )

        # pivot_right now comes in as a function parameter — see the
        # module-level pivot_window_for_timeframe() and the caller in
        # build_market_state(). Previously this was a second, separately
        # hardcoded `= 5` here that had to be manually kept in sync with
        # the window passed to _find_pivots() elsewhere — an easy thing
        # to accidentally drift out of sync. Now there's exactly one
        # source of truth for this value.

        # Active structural pivots.
        active_high = None
        active_low = None

        high_crossed = False
        low_crossed = False

        previous_close = None

        # Process candles chronologically.
        for i in range(len(candles)):

            # -------------------------------------------------
            # Activate newly confirmed swing high.
            # -------------------------------------------------
            confirmed_highs = [
                s for s in swing_highs
                if s.index + pivot_right == i
            ]

            if confirmed_highs:
                active_high = confirmed_highs[-1]
                high_crossed = False

            # -------------------------------------------------
            # Activate newly confirmed swing low.
            # -------------------------------------------------
            confirmed_lows = [
                s for s in swing_lows
                if s.index + pivot_right == i
            ]

            if confirmed_lows:
                active_low = confirmed_lows[-1]
                low_crossed = False

            close_now = closes[i]

            if previous_close is None:
                previous_close = close_now
                continue

            # -------------------------------------------------
            # Bullish break of active swing high
            #
            # Previous close <= level
            # Current close > level
            #
            # Bearish structure -> CHoCH
            # Otherwise         -> BOS
            # -------------------------------------------------
            if (
                active_high is not None
                and not high_crossed
                and previous_close <= active_high.price
                and close_now > active_high.price
            ):
                event_type = (
                    "CHoCH"
                    if structure_direction == "SHORT"
                    else "BOS"
                )

                distance_atr = (
                    abs(close_now - active_high.price) / atr
                    if atr > 0
                    else 0.0
                )

                events.append(
                    StructureEvent(
                        event=event_type,
                        direction="LONG",
                        price=float(close_now),
                        timestamp=int(timestamps[i]),
                        reference_price=active_high.price,
                        swing_index=active_high.index,
                        distance_atr=round(
                            distance_atr,
                            3,
                        ),
                    )
                )

                high_crossed = True
                structure_direction = "LONG"

            # -------------------------------------------------
            # Bearish break of active swing low
            #
            # Previous close >= level
            # Current close < level
            #
            # Bullish structure -> CHoCH
            # Otherwise         -> BOS
            # -------------------------------------------------
            if (
                active_low is not None
                and not low_crossed
                and previous_close >= active_low.price
                and close_now < active_low.price
            ):
                event_type = (
                    "CHoCH"
                    if structure_direction == "LONG"
                    else "BOS"
                )

                distance_atr = (
                    abs(close_now - active_low.price) / atr
                    if atr > 0
                    else 0.0
                )

                events.append(
                    StructureEvent(
                        event=event_type,
                        direction="SHORT",
                        price=float(close_now),
                        timestamp=int(timestamps[i]),
                        reference_price=active_low.price,
                        swing_index=active_low.index,
                        distance_atr=round(
                            distance_atr,
                            3,
                        ),
                    )
                )

                low_crossed = True
                structure_direction = "SHORT"

            previous_close = close_now

        # Keep latest useful structural events.
        state.events = events[-50:]

    # ---------------------------------------------------------
    # Direction — resolved from the latest confirmed BOS/CHoCH when one
    # exists (that's what `structure_direction` ends up holding after
    # the event loop above), falling back to swing-sequence inference
    # only when no directional event has occurred at all. This is the
    # fix: direction now reflects actual recent price action, not just
    # which of the four swing-shape buckets the regime happens to fall
    # into.
    # ---------------------------------------------------------

    if structure_direction != "NEUTRAL":
        state.direction = structure_direction
    elif state.hh and state.hl:
        state.direction = "LONG"
    elif state.lh and state.ll:
        state.direction = "SHORT"
    else:
        state.direction = "NEUTRAL"

    # ---------------------------------------------------------
    # Current/latest structure event
    # ---------------------------------------------------------

    if state.events:
        state.event = state.events[-1]
        state.break_distance_atr = state.event.distance_atr
    else:
        state.event = StructureEvent(
            event="NONE",
            direction=state.direction,
            price=close,
            timestamp=(
                int(swing_highs[-1].timestamp)
                if swing_highs
                else (
                    int(swing_lows[-1].timestamp)
                    if swing_lows
                    else None
                )
            ),
        )
        state.break_distance_atr = 0.0

    return state


def _build_location(
    price: float,
    atr: float,
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
) -> LocationState:
    support = swing_lows[-1].price if swing_lows else None
    resistance = swing_highs[-1].price if swing_highs else None

    distance_support = None
    distance_resistance = None

    near_support = False
    near_resistance = False

    if atr > 0 and support is not None:
        distance_support = abs(price - support) / atr
        near_support = distance_support <= 1.0

    if atr > 0 and resistance is not None:
        distance_resistance = abs(price - resistance) / atr
        near_resistance = distance_resistance <= 1.0

    return LocationState(
        support=round(support, 6) if support is not None else None,
        resistance=round(resistance, 6)
        if resistance is not None
        else None,
        distance_to_support_atr=(
            round(distance_support, 3)
            if distance_support is not None
            else None
        ),
        distance_to_resistance_atr=(
            round(distance_resistance, 3)
            if distance_resistance is not None
            else None
        ),
        near_support=near_support,
        near_resistance=near_resistance,
        liquidity_high=(
            round(resistance, 6)
            if resistance is not None
            else None
        ),
        liquidity_low=(
            round(support, 6)
            if support is not None
            else None
        ),
    )


def _detect_market_phase(
    structure: StructureState,
    volatility: VolatilityState,
) -> str:
    if structure.regime == "COMPRESSION":
        return "COMPRESSION"

    if structure.event.event in ("BOS", "CHoCH"):
        return "EXPANSION"

    if structure.regime == "BULLISH":
        return "TREND_UP"

    if structure.regime == "BEARISH":
        return "TREND_DOWN"

    return "TRANSITION"


def build_market_state(
    candles,
    symbol: str,
    timeframe: str,
    pivot_window_override: Optional[int] = None,
) -> MarketState:
    if len(candles) < 30:
        raise ValueError(
            f"MarketState requires at least 30 candles, got {len(candles)}"
        )

    a = _get_arrays(candles)

    close = float(a["close"][-1])
    timestamp = int(a["ts"][-1])

    # ---------------------------------------------------------
    # ATR
    # ---------------------------------------------------------

    atr = _calculate_atr(
        a["high"],
        a["low"],
        a["close"],
        period=14,
    )

    atr_pct = (atr / close * 100.0) if close > 0 else 0.0

    # ---------------------------------------------------------
    # Swing detection
    #
    # Window size is dynamic by default — scaled per-timeframe by
    # pivot_window_for_timeframe() rather than a single fixed number
    # applied everywhere (see that function's docstring for why). Pass
    # pivot_window_override to force the OLD fixed-5 behavior instead,
    # e.g. for an A/B backtest comparing dynamic vs fixed.
    # ---------------------------------------------------------

    pivot_window = (
        pivot_window_override
        if pivot_window_override is not None
        else pivot_window_for_timeframe(timeframe)
    )

    pivots = _find_pivots(
        a["high"],
        a["low"],
        left=pivot_window,
        right=pivot_window,
    )

    swing_highs = [
        SwingPoint(
            index=p["index"],
            timestamp=int(a["ts"][p["index"]]),
            price=p["price"],
            kind="HIGH",
        )
        for p in pivots
        if p["type"] == "HIGH"
    ]

    swing_lows = [
        SwingPoint(
            index=p["index"],
            timestamp=int(a["ts"][p["index"]]),
            price=p["price"],
            kind="LOW",
        )
        for p in pivots
        if p["type"] == "LOW"
    ]

    # ---------------------------------------------------------
    # Swing strength
    # ---------------------------------------------------------

    if swing_highs:
        surrounding = [
            s.price for s in swing_highs[-5:-1]
        ]

        swing_highs[-1].strength = round(
            _calculate_swing_strength(
                swing_highs[-1].price,
                surrounding,
                atr,
            ),
            2,
        )

    if swing_lows:
        surrounding = [
            s.price for s in swing_lows[-5:-1]
        ]

        swing_lows[-1].strength = round(
            _calculate_swing_strength(
                swing_lows[-1].price,
                surrounding,
                atr,
            ),
            2,
        )

    # ---------------------------------------------------------
    # Structure
    # ---------------------------------------------------------

    structure = _build_structure(
    swing_highs=swing_highs,
    swing_lows=swing_lows,
    close=close,
    atr=atr,
    candles=candles,
    pivot_right=pivot_window,
)

    # Reversal Monitor V1 — separate, additive pass reading the events
    # _build_structure() just produced. Never modifies anything about
    # the existing structure/event computation above; if this ever
    # threw, it would only affect state.structure.reversal, not
    # direction/regime/event/hh/hl/lh/ll or the events list itself.
    if candles and len(candles) >= 30:
        try:
            _ts = np.asarray([int(c["ts"]) for c in candles], dtype=np.int64)
            structure.reversal = _track_reversal(
                events=structure.events,
                swing_highs=swing_highs,
                swing_lows=swing_lows,
                high=a["high"], low=a["low"], close=a["close"],
                timestamps=_ts,
                pivot_right=pivot_window,
            )
        except Exception:
            pass  # reversal monitor failure must never break structure itself

        try:
            structure.bos_recovery = _track_bos_recovery(
                events=structure.events,
                high=a["high"], low=a["low"], close=a["close"], open_=a["open"],
                volume=a["volume"], timestamps=_ts,
                current_regime=structure.regime, current_direction=structure.direction,
            )
        except Exception:
            pass  # same isolation guarantee — a bug here can never affect structure itself

    if swing_highs:
        structure.swing_high_strength = swing_highs[-1].strength

    if swing_lows:
        structure.swing_low_strength = swing_lows[-1].strength

    # ---------------------------------------------------------
    # Range
    # ---------------------------------------------------------

    lookback = min(20, len(a["close"]))

    range_high = float(np.max(a["high"][-lookback:]))
    range_low = float(np.min(a["low"][-lookback:]))

    range_size = range_high - range_low

    if range_size > 0:
        range_position_pct = (
            (close - range_low) / range_size
        ) * 100.0
    else:
        range_position_pct = 50.0

    # Compression compares current range against
    # the previous equivalent range.

    compression_pct = 0.0

    if len(a["close"]) >= 40:
        current_range = (
            np.max(a["high"][-20:])
            - np.min(a["low"][-20:])
        )

        previous_range = (
            np.max(a["high"][-40:-20])
            - np.min(a["low"][-40:-20])
        )

        if previous_range > 0:
            compression_pct = (
                1.0
                - current_range / previous_range
            ) * 100.0

    volatility = VolatilityState(
        atr=round(atr, 6),
        atr_pct=round(atr_pct, 6),
        range_high=round(range_high, 6),
        range_low=round(range_low, 6),
        range_position_pct=round(
            float(np.clip(range_position_pct, 0.0, 100.0)),
            2,
        ),
        compression_pct=round(
            float(np.clip(compression_pct, -100.0, 100.0)),
            2,
        ),
    )

    # ---------------------------------------------------------
    # Location
    # ---------------------------------------------------------

    location = _build_location(
        price=close,
        atr=atr,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
    )

    # ---------------------------------------------------------
    # Market phase
    # ---------------------------------------------------------

    market_phase = _detect_market_phase(
        structure,
        volatility,
    )

    # ---------------------------------------------------------
    # Final shared state
    # ---------------------------------------------------------

    return MarketState(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        price=close,
        structure=structure,
        volatility=volatility,
        location=location,
        swing_highs=swing_highs[-20:],
        swing_lows=swing_lows[-20:],
        support=(
            [round(swing_lows[-1].price, 6)]
            if swing_lows
            else []
        ),
        resistance=(
            [round(swing_highs[-1].price, 6)]
            if swing_highs
            else []
        ),
        market_phase=market_phase,
        metadata={
            "candle_count": len(candles),
            "pivot_count": len(pivots),
            "range_lookback": lookback,
        },
    )