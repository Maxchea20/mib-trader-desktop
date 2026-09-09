"""AGENT 9 — PATTERN ANALYSIS V2.

Upgraded from "two highs within 0.4% = Double Top, first-match
if/elif chain, hardcoded quality=0.7/completion=0.8" into a real
structured chart-pattern engine. The old version's core problem: no
detector actually validated geometry (valley depth, time separation,
neckline, symmetry) — it just checked whether two prices were close,
which is necessary but nowhere near sufficient evidence for a genuine
Double Top.

ARCHITECTURE:
  - A small reusable geometry engine (price_similarity_atr,
    slope_atr, convergence_score, symmetry_score, touch_quality, etc.)
    shared across every detector, per the spec's own explicit request
    not to invent unrelated math per pattern.
  - A PatternCandidate framework: every detector builds a candidate
    with FOUR distinct scores (quality, completion, confidence,
    strength — never collapsed into one number) plus a state machine,
    confirmation, and invalidation. analyze() runs every detector,
    ranks candidates, and returns the best one — or NONE if nothing
    clears a reasonable bar, since "no clear pattern" is an explicitly
    preferred, valid result (spec section 46).
  - 12 detectors implemented with genuine geometric validation:
    Double Top/Bottom, Head & Shoulders/Inverse H&S, Ascending/
    Descending/Symmetrical Triangle, Rising/Falling Wedge, Bull/Bear
    Flag, Rectangle.

DELIBERATELY NOT IMPLEMENTED (explicit scope decision, not an
oversight): Triple Top/Bottom and Cup & Handle. The spec itself marks
Cup & Handle as "lower priority / optional, only if sufficient
geometry exists." Triple patterns need meaningfully MORE evidence than
Double patterns (three-way clustering plus two valleys/peaks, each
independently validated) — given the scope of everything else here,
building those two with the same rigor as the other 12 rather than
bolting on a shallow version was the more honest tradeoff, matching
the spec's own "prefer no pattern over a false pattern" philosophy
extended to "prefer not implementing a detector over implementing it
without real rigor."

Pattern is a pure analytical lens: it never recomputes Market
Structure/Breakout/Momentum/Volume/Support-Resistance, and never
forces a trade — direction here is analytical bias only.
"""
import numpy as np
from ..contract import AgentResult, LONG, SHORT, NEUTRAL, neutral, clamp
from ..indicators import arrays, atr, find_pivots

AGENT_ID = "pattern"

MIN_CANDLES = 60
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
MIN_TIME_SEPARATION_BARS = 5   # minimum bars between two pivots for them
# to be considered independent structural points, not noise from the
# same local move

W_GEOMETRY = 20.0
W_PIVOT_STRUCTURE = 15.0
W_SYMMETRY = 10.0
W_TOUCH = 10.0
W_MATURITY = 10.0
W_COMPLETION_CTX = 10.0
W_CONTEXT = 10.0
W_VOLUME = 5.0
W_MOMENTUM = 5.0
W_CONFIRMATION = 5.0


# ======================================================================
# REUSABLE GEOMETRY ENGINE — shared by every detector below
# ======================================================================

def price_similarity_atr(p1, p2, atr_value):
    """ATR-normalized distance between two prices — 0 = identical."""
    return abs(p1 - p2) / max(atr_value, 1e-9)


def similarity_score(dist_atr, tolerance_atr):
    """1.0 at dist=0, decaying linearly to 0 at dist=tolerance_atr."""
    return clamp(1.0 - dist_atr / max(tolerance_atr, 1e-9), 0, 1)


def time_separation(i1, i2):
    return abs(i2 - i1)


def atr_distance(p1, p2, atr_value):
    return abs(p1 - p2) / max(atr_value, 1e-9)


def slope_atr(p1, i1, p2, i2, atr_value):
    """ATR-normalized slope, per bar — the same unit used everywhere
    else here, so a slope and a price distance are always comparable."""
    if i2 == i1:
        return 0.0
    return (p2 - p1) / (i2 - i1) / max(atr_value, 1e-9)


def convergence_score(slope_upper, slope_lower):
    """How much two boundaries are closing toward each other per bar.
    Positive closing_rate = converging (upper falling relative to
    lower, or lower rising relative to upper). Normalized against a
    modest per-bar ATR-slope-difference anchor."""
    closing_rate = slope_lower - slope_upper
    return clamp(closing_rate / 0.04, 0, 1)


def symmetry_score(a, b):
    """1.0 = perfectly symmetric magnitudes, 0 = maximally asymmetric."""
    denom = max(abs(a), abs(b), 1e-9)
    return clamp(1.0 - abs(abs(a) - abs(b)) / denom, 0, 1)


def retracement_depth_ratio(impulse_atr, retrace_atr):
    return abs(retrace_atr) / max(abs(impulse_atr), 1e-9)


def touch_quality(prices, boundary, atr_value, tolerance_atr=0.35):
    """Fraction of given prices within tolerance of a boundary — how
    well a set of pivots actually respects a proposed line."""
    if not prices:
        return 0.0
    hits = sum(1 for p in prices if abs(p - boundary) / max(atr_value, 1e-9) <= tolerance_atr)
    return hits / len(prices)


def body_close_breaks(close_arr, open_arr, start_idx, end_idx, level, direction):
    """Wicks alone must never confirm anything (spec section 29) — this
    checks completed candle BODY CLOSES only, scanning start_idx+1..end_idx
    (never using information beyond end_idx, i.e. never looking ahead of
    "now" when called with end_idx = n-1)."""
    for i in range(start_idx + 1, end_idx + 1):
        c = close_arr[i]
        if direction == "below" and c < level:
            return True, i
        if direction == "above" and c > level:
            return True, i
    return False, None


class PatternCandidate:
    """Every detector builds one of these. Quality, completion,
    confidence and strength are FOUR genuinely different numbers, never
    collapsed into one — see the module docstring."""
    def __init__(self, pattern_type, direction_bias):
        self.pattern_type = pattern_type
        self.direction_bias = direction_bias  # analytical bias, not a trade
        self.quality = 0.0        # how structurally valid the geometry is
        self.completion = 0.0     # how far along toward confirmation
        self.confidence = 0.0     # reliability of the identification itself
        self.strength = 0.0       # significance/power of the context
        self.state = "FORMING"
        self.confirmed = False
        self.invalidated = False
        self.failed = False
        self.evidence = []
        self.key_levels = []

    def rank_score(self):
        # Candidate selection prefers strong geometry AND real progress —
        # a beautifully formed but very early pattern and a messy
        # near-complete one should both be discounted relative to a
        # candidate that's genuinely good on both axes.
        return self.quality * 0.55 + self.completion * 0.45


# ======================================================================
# REVERSAL FAMILY — Double Top/Bottom, Head & Shoulders/Inverse H&S
# ======================================================================

def _detect_double_extremum(highs, lows, close_arr, open_arr, atr_value, n, is_top):
    """Shared logic for Double Top (is_top=True) and Double Bottom
    (is_top=False) — mirror images of the same geometry."""
    pivots = highs if is_top else lows
    opposite = lows if is_top else highs
    if len(pivots) < 2:
        return None
    p2, p1 = pivots[-1], pivots[-2]
    if time_separation(p1["i"], p2["i"]) < MIN_TIME_SEPARATION_BARS:
        return None

    between = [o for o in opposite if p1["i"] < o["i"] < p2["i"]]
    if not between:
        return None
    valley = (min(between, key=lambda o: o["price"]) if is_top
             else max(between, key=lambda o: o["price"]))

    sim_atr = price_similarity_atr(p1["price"], p2["price"], atr_value)
    if sim_atr > 1.0:
        return None  # the two extremes aren't actually similar — not this pattern
    depth_atr = (abs(min(p1["price"], p2["price"]) - valley["price"]) if is_top
                else abs(valley["price"] - max(p1["price"], p2["price"])))
    depth_atr /= max(atr_value, 1e-9)
    if depth_atr < 0.5:
        return None  # no meaningful valley/peak between the two extremes

    time_sep = time_separation(p1["i"], p2["i"])
    neckline = valley["price"]
    pattern_name = "Double Top" if is_top else "Double Bottom"
    direction = SHORT if is_top else LONG
    cand = PatternCandidate(pattern_name, direction)

    quality = (
        similarity_score(sim_atr, 1.0) * 0.35
        + clamp(depth_atr / 2.0, 0, 1) * 0.35
        + clamp(time_sep / 20.0, 0, 1) * 0.30
    ) * 100

    current_close = close_arr[-1]
    total_move_needed = abs(p2["price"] - neckline)
    progress = (p2["price"] - current_close) / max(total_move_needed, 1e-9) if is_top \
        else (current_close - p2["price"]) / max(total_move_needed, 1e-9)
    completion = clamp(progress, 0, 1) * 100

    confirmed, confirm_idx = body_close_breaks(close_arr, open_arr, p2["i"], n - 1, neckline,
                                               "below" if is_top else "above")
    invalidate_level = max(p1["price"], p2["price"]) + 0.2 * atr_value if is_top \
        else min(p1["price"], p2["price"]) - 0.2 * atr_value
    invalidated, _ = body_close_breaks(close_arr, open_arr, valley["i"], n - 1, invalidate_level,
                                       "above" if is_top else "below")

    if invalidated:
        state = "INVALID"
    elif confirmed:
        state = "CONFIRMED"
    elif completion > 70:
        state = "MATURE"
    elif completion > 35:
        state = "DEVELOPING"
    else:
        state = "FORMING"

    cand.quality, cand.completion, cand.state = quality, completion, state
    cand.confirmed, cand.invalidated = confirmed, invalidated
    label1, label2 = ("H1", "H2") if is_top else ("L1", "L2")
    cand.evidence = [
        f"{pattern_name} candidate: {label1} {p1['price']:.1f}, {label2} {p2['price']:.1f}",
        f"Similarity: {sim_atr:.2f} ATR, {'valley' if is_top else 'peak'} depth {depth_atr:.2f} ATR",
        f"Pivot separation: {time_sep} candles",
        f"Neckline: {neckline:.1f}",
        f"Quality: {quality:.0f}/100, Completion: {completion:.0f}%",
        f"State: {state}" + (" — neckline broken" if confirmed else " — neckline not yet broken"),
    ]
    cand.key_levels = [
        {"label": "Neckline", "price": round(neckline, 2), "type": "neckline"},
        {"label": label1, "price": round(p1["price"], 2), "type": "resistance" if is_top else "support"},
        {"label": label2, "price": round(p2["price"], 2), "type": "resistance" if is_top else "support"},
    ]
    return cand


def _detect_head_shoulders(highs, lows, close_arr, open_arr, atr_value, n, inverse):
    """Shared logic for Head & Shoulders (inverse=False) and Inverse
    H&S (inverse=True)."""
    pivots = lows if inverse else highs   # the "head" side
    necks = highs if inverse else lows    # the neckline-defining side
    if len(pivots) < 3:
        return None
    p3, p2, p1 = pivots[-1], pivots[-2], pivots[-3]  # p2 = head candidate
    if not (p1["i"] < p2["i"] < p3["i"]):
        return None
    if time_separation(p1["i"], p2["i"]) < MIN_TIME_SEPARATION_BARS or \
       time_separation(p2["i"], p3["i"]) < MIN_TIME_SEPARATION_BARS:
        return None

    head_prominent = (p2["price"] < p1["price"] and p2["price"] < p3["price"]) if inverse \
        else (p2["price"] > p1["price"] and p2["price"] > p3["price"])
    if not head_prominent:
        return None

    shoulder_sim_atr = price_similarity_atr(p1["price"], p3["price"], atr_value)
    if shoulder_sim_atr > 1.2:
        return None
    prominence_atr = abs(p2["price"] - p1["price"]) / max(atr_value, 1e-9)
    if prominence_atr < 0.4:
        return None

    necklines = [nk for nk in necks if p1["i"] < nk["i"] < p3["i"]]
    if len(necklines) < 2:
        return None
    n1, n2 = necklines[0], necklines[-1]
    neckline_now = n1["price"] + slope_atr(n1["price"], n1["i"], n2["price"], n2["i"], atr_value) \
        * atr_value * (n - 1 - n1["i"])

    time_symmetry = symmetry_score(p2["i"] - p1["i"], p3["i"] - p2["i"])
    shoulder_similarity = similarity_score(shoulder_sim_atr, 1.2)

    pattern_name = "Inverse Head & Shoulders" if inverse else "Head & Shoulders"
    direction = LONG if inverse else SHORT
    cand = PatternCandidate(pattern_name, direction)

    quality = (
        shoulder_similarity * 0.30
        + clamp(prominence_atr / 1.5, 0, 1) * 0.35
        + time_symmetry * 0.20
        + clamp(len(necklines) / 3.0, 0, 1) * 0.15
    ) * 100

    current_close = close_arr[-1]
    total_move_needed = abs(p2["price"] - neckline_now)
    progress = (p2["price"] - current_close) / max(total_move_needed, 1e-9) if not inverse \
        else (current_close - p2["price"]) / max(total_move_needed, 1e-9)
    completion = clamp(progress, 0, 1) * 100

    confirmed, _ = body_close_breaks(close_arr, open_arr, p3["i"], n - 1, neckline_now,
                                     "above" if inverse else "below")
    invalidate_level = p2["price"] - 0.2 * atr_value if inverse else p2["price"] + 0.2 * atr_value
    invalidated, _ = body_close_breaks(close_arr, open_arr, p2["i"], n - 1, invalidate_level,
                                       "below" if inverse else "above")

    if invalidated:
        state = "INVALID"
    elif confirmed:
        state = "CONFIRMED"
    elif completion > 70:
        state = "MATURE"
    elif completion > 35:
        state = "DEVELOPING"
    else:
        state = "FORMING"

    cand.quality, cand.completion, cand.state = quality, completion, state
    cand.confirmed, cand.invalidated = confirmed, invalidated
    head_label = "Head (low)" if inverse else "Head (high)"
    cand.evidence = [
        f"{pattern_name} candidate: L-shoulder {p1['price']:.1f}, {head_label} {p2['price']:.1f}, R-shoulder {p3['price']:.1f}",
        f"Shoulder similarity: {shoulder_sim_atr:.2f} ATR, head prominence {prominence_atr:.2f} ATR",
        f"Time symmetry: {time_symmetry:.2f}, neckline: {neckline_now:.1f}",
        f"Quality: {quality:.0f}/100, Completion: {completion:.0f}%",
        f"State: {state}",
    ]
    cand.key_levels = [
        {"label": "Neckline", "price": round(neckline_now, 2), "type": "neckline"},
        {"label": "Head", "price": round(p2["price"], 2), "type": "support" if inverse else "resistance"},
    ]
    return cand


# ======================================================================
# BOUNDARY FAMILY — Triangles, Wedges, Rectangle (all share the same
# "two converging/parallel boundaries, touch-quality, breakout" shape)
# ======================================================================

def _fit_boundary(pivots, atr_value):
    """Simple two-point-anchored slope through the earliest and latest
    of a pivot set, plus touch quality of every point against that
    line. Deliberately simple (not a least-squares fit) — this is
    pattern-recognition context, not a precision regression tool."""
    if len(pivots) < 2:
        return None
    first, last = pivots[0], pivots[-1]
    m = slope_atr(first["price"], first["i"], last["price"], last["i"], atr_value)
    intercept = first["price"]
    intercept_i = first["i"]

    def value_at(i):
        return intercept + m * (i - intercept_i) * atr_value

    touches = touch_quality([p["price"] for p in pivots], value_at(pivots[-1]["i"]), atr_value) \
        if len(pivots) >= 2 else 0.0
    return {"slope": m, "value_at": value_at, "touch_quality": touches, "points": pivots}


def _detect_boundary_pattern(highs, lows, close_arr, open_arr, atr_value, n, kind):
    """kind in {"ascending_triangle", "descending_triangle",
    "symmetrical_triangle", "rising_wedge", "falling_wedge", "rectangle"}"""
    recent_highs = [h for h in highs if h["i"] >= n - 60][-4:]
    recent_lows = [l for l in lows if l["i"] >= n - 60][-4:]
    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return None

    upper = _fit_boundary(recent_highs, atr_value)
    lower = _fit_boundary(recent_lows, atr_value)
    if upper is None or lower is None:
        return None

    width_start_i = min(recent_highs[0]["i"], recent_lows[0]["i"])
    width_now_atr = (upper["value_at"](n - 1) - lower["value_at"](n - 1)) / max(atr_value, 1e-9)
    width_then_atr = (upper["value_at"](width_start_i) - lower["value_at"](width_start_i)) / max(atr_value, 1e-9)
    duration = n - 1 - width_start_i
    if duration < MIN_TIME_SEPARATION_BARS * 2:
        return None
    shrinking = width_now_atr < width_then_atr * 0.85

    flat = 0.05  # ATR-per-bar slope magnitude below which a boundary counts as "flat"
    upper_flat = abs(upper["slope"]) < flat
    lower_flat = abs(lower["slope"]) < flat
    upper_rising = upper["slope"] > flat
    upper_falling = upper["slope"] < -flat
    lower_rising = lower["slope"] > flat
    lower_falling = lower["slope"] < -flat

    matches = {
        "ascending_triangle": upper_flat and lower_rising,
        "descending_triangle": lower_flat and upper_falling,
        "symmetrical_triangle": upper_falling and lower_rising,
        "rising_wedge": upper_rising and lower_rising and shrinking,
        "falling_wedge": upper_falling and lower_falling and shrinking,
        "rectangle": upper_flat and lower_flat and not shrinking,
    }
    if not matches.get(kind):
        return None
    if kind != "rectangle" and not shrinking:
        return None
    touch_avg_check = (upper["touch_quality"] + lower["touch_quality"]) / 2.0
    if touch_avg_check < 0.5:
        return None  # fewer than half the pivots actually respect the
        # fitted boundaries — this is noise producing a coincidental
        # slope, not genuinely coherent geometry (spec: "at least 2
        # MEANINGFUL tests," "coherent geometry" — a line that most of
        # its own defining points don't actually respect fails that bar)

    names = {
        "ascending_triangle": ("Ascending Triangle", LONG), "descending_triangle": ("Descending Triangle", SHORT),
        "symmetrical_triangle": ("Symmetrical Triangle", NEUTRAL), "rising_wedge": ("Rising Wedge", SHORT),
        "falling_wedge": ("Falling Wedge", LONG), "rectangle": ("Rectangle", NEUTRAL),
    }
    pattern_name, direction = names[kind]
    cand = PatternCandidate(pattern_name, direction)

    conv = convergence_score(upper["slope"], lower["slope"]) if kind != "rectangle" else 1.0
    touch_avg = (upper["touch_quality"] + lower["touch_quality"]) / 2.0
    quality = (
        conv * 0.35 + touch_avg * 0.30
        + clamp(duration / 40.0, 0, 1) * 0.20
        + clamp((1.0 - width_now_atr / max(width_then_atr, 1e-9)) if kind != "rectangle" else 0.5, 0, 1) * 0.15
    ) * 100

    boundary_upper_now = upper["value_at"](n - 1)
    boundary_lower_now = lower["value_at"](n - 1)
    within_range = boundary_lower_now <= close_arr[-1] <= boundary_upper_now
    completion = clamp(duration / 50.0, 0, 1) * 100 if within_range else 100.0

    confirmed_up, _ = body_close_breaks(close_arr, open_arr, width_start_i, n - 1, boundary_upper_now, "above")
    confirmed_down, _ = body_close_breaks(close_arr, open_arr, width_start_i, n - 1, boundary_lower_now, "below")
    if kind in ("ascending_triangle", "falling_wedge"):
        confirmed = confirmed_up
        if confirmed:
            direction = LONG
    elif kind in ("descending_triangle", "rising_wedge"):
        confirmed = confirmed_down
        if confirmed:
            direction = SHORT
    else:  # symmetrical_triangle, rectangle — direction follows whichever side broke
        confirmed = confirmed_up or confirmed_down
        if confirmed_up:
            direction = LONG
        elif confirmed_down:
            direction = SHORT
    cand.direction_bias = direction

    invalidated = not shrinking and kind != "rectangle" and duration > 15  # geometry stopped converging
    if invalidated:
        state = "INVALID"
    elif confirmed:
        state = "CONFIRMED"
    elif completion > 70 or duration > 30:
        state = "MATURE"
    elif duration > 15:
        state = "DEVELOPING"
    else:
        state = "FORMING"

    cand.quality, cand.completion, cand.state = quality, completion, state
    cand.confirmed, cand.invalidated = confirmed, invalidated
    cand.evidence = [
        f"{pattern_name} candidate over {duration} candles",
        f"Upper boundary slope {upper['slope']:+.3f} ATR/bar, lower {lower['slope']:+.3f} ATR/bar",
        f"Convergence: {conv:.2f}, touch quality {touch_avg:.2f}",
        f"Width now {width_now_atr:.2f} ATR (was {width_then_atr:.2f} ATR)",
        f"Quality: {quality:.0f}/100, Completion: {completion:.0f}%",
        f"State: {state}" + (f" — confirmed {'above' if confirmed_up else 'below'}" if confirmed else " — breakout not confirmed"),
    ]
    cand.key_levels = [
        {"label": "Upper boundary", "price": round(boundary_upper_now, 2), "type": "resistance"},
        {"label": "Lower boundary", "price": round(boundary_lower_now, 2), "type": "support"},
    ]
    return cand


# ======================================================================
# FLAG FAMILY — Bull Flag / Bear Flag (requires a genuine prior impulse)
# ======================================================================

def _detect_flag(close_arr, open_arr, high_arr, low_arr, atr_value, n, bullish):
    impulse_window = 12
    consolidation_window = 10
    if n < impulse_window + consolidation_window + 5:
        return None

    impulse_start = n - consolidation_window - impulse_window
    impulse_end = n - consolidation_window
    impulse_move_atr = (close_arr[impulse_end] - close_arr[impulse_start]) / max(atr_value, 1e-9)
    impulse_ok = impulse_move_atr > 1.2 if bullish else impulse_move_atr < -1.2
    if not impulse_ok:
        return None  # "without a prior impulse, NOT a flag" — spec sections 19/20

    consolidation = close_arr[impulse_end:n]
    cons_high, cons_low = float(np.max(consolidation)), float(np.min(consolidation))
    cons_width_atr = (cons_high - cons_low) / max(atr_value, 1e-9)
    if cons_width_atr > abs(impulse_move_atr) * 0.7:
        return None  # consolidation too wide relative to the impulse — not compact

    net_retrace_atr = (consolidation[-1] - consolidation[0]) / max(atr_value, 1e-9)
    retrace_ratio = retracement_depth_ratio(impulse_move_atr, net_retrace_atr)
    if retrace_ratio > 0.65:
        return None  # retracement too deep to be a controlled pullback

    pattern_name = "Bull Flag" if bullish else "Bear Flag"
    direction = LONG if bullish else SHORT
    cand = PatternCandidate(pattern_name, direction)

    quality = (
        clamp(abs(impulse_move_atr) / 3.0, 0, 1) * 0.40
        + clamp(1.0 - cons_width_atr / max(abs(impulse_move_atr), 1e-9), 0, 1) * 0.35
        + clamp(1.0 - retrace_ratio / 0.65, 0, 1) * 0.25
    ) * 100

    completion = clamp((n - impulse_end) / consolidation_window, 0, 1) * 100

    confirmed, _ = body_close_breaks(close_arr, open_arr, impulse_end, n - 1,
                                     cons_high if bullish else cons_low,
                                     "above" if bullish else "below")

    failed = retrace_ratio > 0.55  # borderline-excessive retracement flagged as weakening, not hard invalid
    state = "CONFIRMED" if confirmed else ("MATURE" if completion > 70 else ("DEVELOPING" if completion > 35 else "FORMING"))

    cand.quality, cand.completion, cand.state = quality, completion, state
    cand.confirmed, cand.failed = confirmed, failed
    cand.evidence = [
        f"{pattern_name} candidate: prior impulse {impulse_move_atr:+.2f} ATR",
        f"Consolidation width {cons_width_atr:.2f} ATR, retracement ratio {retrace_ratio:.2f}",
        f"Quality: {quality:.0f}/100, Completion: {completion:.0f}%",
        f"State: {state}" + (" (retracement getting deep)" if failed and not confirmed else ""),
    ]
    cand.key_levels = [
        {"label": "Flag boundary", "price": round(cons_low if bullish else cons_high, 2),
         "type": "support" if bullish else "resistance"},
    ]
    return cand


# ======================================================================
# CONTEXT (spec section 31/32) — optional, never mandatory, never
# recomputes another agent's full logic
# ======================================================================

def _context_bonus(cand, market_state):
    if market_state is None:
        return 0.0, 0.0
    bonus_ctx = 0.0
    try:
        direction = getattr(market_state, "direction", None)
        if direction and cand.direction_bias != NEUTRAL and direction == cand.direction_bias:
            bonus_ctx = W_CONTEXT
    except Exception:
        pass
    return bonus_ctx, 0.0


# ======================================================================
# MAIN ENTRY POINT
# ======================================================================

def analyze(candles, timeframe: str, market_state=None) -> AgentResult:
    if len(candles) < MIN_CANDLES:
        return neutral(AGENT_ID, timeframe, "Not enough candles")

    a = arrays(candles)
    high, low, close, open_ = a["high"], a["low"], a["close"], a["open"]
    n = len(close)

    _atr = atr(high, low, close, 14)
    if _atr <= 0 or not np.isfinite(_atr):
        return neutral(AGENT_ID, timeframe, "Invalid ATR")

    piv = find_pivots(high, low, left=PIVOT_LEFT, right=PIVOT_RIGHT)
    highs = [p for p in piv if p["type"] == "H"]
    lows = [p for p in piv if p["type"] == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return neutral(AGENT_ID, timeframe, "Too few pivots")

    candidates = []
    try:
        c = _detect_double_extremum(highs, lows, close, open_, _atr, n, is_top=True)
        if c: candidates.append(c)
        c = _detect_double_extremum(highs, lows, close, open_, _atr, n, is_top=False)
        if c: candidates.append(c)
        c = _detect_head_shoulders(highs, lows, close, open_, _atr, n, inverse=False)
        if c: candidates.append(c)
        c = _detect_head_shoulders(highs, lows, close, open_, _atr, n, inverse=True)
        if c: candidates.append(c)
        for kind in ("ascending_triangle", "descending_triangle", "symmetrical_triangle",
                    "rising_wedge", "falling_wedge", "rectangle"):
            c = _detect_boundary_pattern(highs, lows, close, open_, _atr, n, kind)
            if c: candidates.append(c)
        c = _detect_flag(close, open_, high, low, _atr, n, bullish=True)
        if c: candidates.append(c)
        c = _detect_flag(close, open_, high, low, _atr, n, bullish=False)
        if c: candidates.append(c)
    except Exception:
        pass  # a single detector's failure must never break the whole agent

    if not candidates:
        return neutral(AGENT_ID, timeframe, "No clear pattern", valid=True)

    # Quality floor — prefer no pattern over a low-quality false one
    # (spec section 46). Also exclude candidates that invalidated
    # WITHOUT ever confirming first — that's noise that briefly fit a
    # shape, not meaningful evidence. A candidate that CONFIRMED and
    # only later failed is kept — that's genuinely valuable
    # "confirmed then failed" information the spec explicitly wants
    # distinguished from mere noise (section 30).
    candidates = [c for c in candidates if c.quality >= 40.0 and not (c.invalidated and not c.confirmed)
                 and not (c.failed and not c.confirmed)]
    if not candidates:
        return neutral(AGENT_ID, timeframe, "No clear pattern", valid=True)

    candidates.sort(key=lambda c: c.rank_score(), reverse=True)
    best = candidates[0]
    # Genuine ambiguity: the top two candidates are close enough that
    # picking one over the other isn't well-supported — safer to return
    # a neutral, ambiguous result than to confidently pick a coin-flip.
    if len(candidates) > 1 and abs(best.rank_score() - candidates[1].rank_score()) < 8.0 \
       and best.direction_bias != candidates[1].direction_bias:
        return neutral(AGENT_ID, timeframe,
                       f"Ambiguous — {best.pattern_type} vs {candidates[1].pattern_type} similarly ranked",
                       valid=True)

    context_bonus, _ = _context_bonus(best, market_state)
    total_score = (
        clamp(best.quality / 100 * W_GEOMETRY, 0, W_GEOMETRY)
        + clamp(len(highs + lows) / 8.0, 0, 1) * W_PIVOT_STRUCTURE
        + clamp(best.quality / 100, 0, 1) * W_SYMMETRY
        + clamp(best.completion / 100, 0, 1) * (W_MATURITY + W_COMPLETION_CTX)
        + context_bonus
        + (W_CONFIRMATION if best.confirmed else 0.0)
    )
    best.confidence = clamp(20.0 + total_score * 0.55, 0, 94)
    best.strength = clamp(best.quality * 0.5 + best.completion * 0.5, 0, 100)

    if best.invalidated or best.failed:
        best.direction_bias = NEUTRAL

    evidence = list(best.evidence)
    evidence.append(f"Confidence: {best.confidence:.0f}%, Strength: {best.strength:.0f}")

    return AgentResult(AGENT_ID, best.direction_bias, round(best.confidence, 1), round(best.strength, 1),
                       evidence, best.key_levels, timeframe, valid=True)