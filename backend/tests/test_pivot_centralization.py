"""Regression tests for the 2026-09-12 audit's Step 4 fix: pivot/swing
centralization.

Audit found candidate pivot/swing/extremum implementations in:
  - indicators.py::find_pivots()           -- already the shared
    implementation, used directly by support_resistance, pattern,
    elliott_wave, and imported (unused) by fibonacci.
  - market_state/builder.py::_find_pivots() -- a genuine second
    algorithm, verified below to be numerically IDENTICAL to
    indicators.find_pivots() (differing only in output key/value
    naming), now delegating.
  - structure/__init__.py -- NOT a duplicate: it never computes
    pivots itself, it always reads swing_high/swing_low data off a
    MarketState built by build_market_state() (which itself now calls
    the shared find_pivots()). Nothing to migrate here.
  - pattern/__init__.py::_detect_double_extremum(),
    _detect_head_shoulders(), _fit_boundary() -- NOT pivot-detection
    duplicates. These consume an ALREADY-COMPUTED pivot list (from
    indicators.find_pivots(), imported directly) to do pattern-shape
    recognition (double top/bottom geometry, neckline fitting, trend-
    line fitting through existing pivots). A genuinely different
    concept built on top of the shared pivot data, not a second way
    of finding the pivots themselves -- correctly left untouched.

This file proves:
  1. The OLD market_state/builder.py pivot algorithm and the shared
     indicators.find_pivots() select the exact same bars as pivots
     (same indices, same prices, same H/L classification) across a
     wide range of synthetic scenarios -- tie-heavy data, monotonic
     runs, flat arrays, single spikes, asymmetric windows, and
     near-boundary short arrays. Only the output SCHEMA differed
     (index/i, HIGH-LOW/H-L), never which bars were selected.
  2. market_state/builder.py::_find_pivots() now genuinely delegates
     to indicators.find_pivots() (monkeypatch check), not just
     numerically happens to agree.
  3. End-to-end: build_market_state()'s swing_highs/swing_lows and
     structural direction are byte-for-byte unchanged for realistic
     (>=30 candle) input before vs. after the migration.

Run with: python3 -m pytest tests/test_pivot_centralization.py -v
"""
import os
import sys
import random
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from src.indicators import find_pivots as shared_find_pivots
from src.market_state import builder as ms_builder


# --- Reconstruction of the PRE-MIGRATION market_state/builder.py
# algorithm, to pin the equivalence claim down as an executable fact.
def _pre_migration_builder_find_pivots(highs, lows, left=3, right=3) -> List[Dict[str, Any]]:
    pivots: List[Dict[str, Any]] = []
    if len(highs) < left + right + 1:
        return pivots
    for i in range(left, len(highs) - right):
        high_window = highs[i - left: i + right + 1]
        low_window = lows[i - left: i + right + 1]
        if highs[i] == np.max(high_window):
            pivots.append({"index": i, "price": float(highs[i]), "type": "HIGH"})
        if lows[i] == np.min(low_window):
            pivots.append({"index": i, "price": float(lows[i]), "type": "LOW"})
    pivots.sort(key=lambda x: x["index"])
    return pivots


def _normalize_shared(piv):
    return sorted([(p["i"], p["type"], round(p["price"], 8)) for p in piv])


def _normalize_old_builder(piv):
    type_map = {"HIGH": "H", "LOW": "L"}
    return sorted([(p["index"], type_map[p["type"]], round(p["price"], 8)) for p in piv])


def _normalize_new_builder(piv):
    type_map = {"HIGH": "H", "LOW": "L"}
    return sorted([(p["index"], type_map[p["type"]], round(p["price"], 8)) for p in piv])


def test_old_builder_algorithm_matches_shared_find_pivots_everywhere():
    rng = random.Random(7)
    total, mismatches = 0, 0

    def check(high, low, left, right, label):
        nonlocal total, mismatches
        total += 1
        a = shared_find_pivots(high, low, left=left, right=right)
        b = _pre_migration_builder_find_pivots(high, low, left=left, right=right)
        na, nb = _normalize_shared(a), _normalize_old_builder(b)
        if na != nb:
            mismatches += 1
            print(f"MISMATCH [{label}] left={left} right={right} n={len(high)}")
            print("  shared:", na)
            print("  old_builder:", nb)

    # tie-heavy integer data across sizes and window combos
    for n in [7, 10, 20, 50, 100]:
        for left, right in [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (3, 5), (5, 3), (7, 7)]:
            for trial in range(10):
                high = np.array([float(rng.randint(90, 110)) for _ in range(n)])
                low = high - np.array([float(rng.randint(0, 5)) for _ in range(n)])
                check(high, low, left, right, f"random-ties n={n} trial={trial}")

    # monotonic increasing / decreasing
    for n in [10, 30]:
        high = np.arange(n, dtype=float)
        low = high - 2
        check(high, low, 3, 3, f"monotonic-inc n={n}")
        check(high[::-1].copy(), low[::-1].copy(), 3, 3, f"monotonic-dec n={n}")

    # flat
    high = np.full(30, 100.0)
    low = np.full(30, 95.0)
    check(high, low, 3, 3, "flat")

    # single spike, symmetric and asymmetric windows
    high = np.full(30, 100.0); high[15] = 150.0
    low = np.full(30, 95.0); low[15] = 60.0
    check(high, low, 3, 3, "single-spike")
    check(high, low, 5, 2, "single-spike-asym")

    # near-boundary short arrays
    for n in range(1, 8):
        high = np.array([float(rng.uniform(90, 110)) for _ in range(n)])
        low = high - np.array([float(rng.uniform(0, 5)) for _ in range(n)])
        check(high, low, 3, 3, f"short n={n}")

    assert mismatches == 0, f"{mismatches}/{total} scenarios disagreed"
    assert total > 400  # sanity: the sweep actually ran a meaningful number of cases


def test_new_builder_find_pivots_matches_old_algorithm_after_migration():
    """The now-migrated ms_builder._find_pivots() must still return the
    exact same (index, type, price) set the old inline algorithm
    would have -- proving the delegation preserves output, not just
    that the underlying algorithms independently agree in theory."""
    rng = random.Random(11)
    for n in [20, 50, 100]:
        for left, right in [(2, 2), (3, 3), (5, 5)]:
            for trial in range(5):
                high = np.array([float(rng.randint(90, 110)) for _ in range(n)])
                low = high - np.array([float(rng.randint(0, 5)) for _ in range(n)])
                old = _pre_migration_builder_find_pivots(high, low, left=left, right=right)
                new = ms_builder._find_pivots(high, low, left=left, right=right)
                assert _normalize_old_builder(old) == _normalize_new_builder(new), (
                    f"n={n} left={left} right={right} trial={trial}"
                )


def test_builder_find_pivots_now_delegates_to_shared_find_pivots():
    """Not just numerically equal -- actually calls through. Patch
    indicators.find_pivots with a sentinel and confirm
    market_state.builder._find_pivots routes to it and correctly
    translates the schema (i->index, H/L->HIGH/LOW)."""
    calls = []

    def _sentinel(high, low, left=3, right=3):
        calls.append((len(high), left, right))
        return [{"i": 5, "price": 101.5, "type": "H"}, {"i": 8, "price": 98.25, "type": "L"}]

    original = ms_builder._shared_find_pivots
    ms_builder._shared_find_pivots = _sentinel
    try:
        high = np.arange(20, dtype=float)
        low = high - 2
        result = ms_builder._find_pivots(high, low, left=4, right=6)
        assert calls and calls[0] == (20, 4, 6)
        assert result == [
            {"index": 5, "price": 101.5, "type": "HIGH"},
            {"index": 8, "price": 98.25, "type": "LOW"},
        ]
    finally:
        ms_builder._shared_find_pivots = original


def _synthetic_candles(n, seed=0):
    rng = random.Random(seed)
    close = [100.0]
    for _ in range(n - 1):
        close.append(close[-1] + rng.uniform(-1.5, 1.5))
    candles = []
    for i, c in enumerate(close):
        h = c + rng.uniform(0, 2)
        l = c - rng.uniform(0, 2)
        candles.append({
            "open": c - 0.1, "high": h, "low": l, "close": c,
            "volume": 100.0 + rng.uniform(0, 50), "ts": 1_700_000_000 + i * 900,
        })
    return candles


def test_market_state_swings_unchanged_for_realistic_candle_count():
    """End-to-end: with >=30 candles (the only regime any real caller
    uses), build_market_state()'s swing_highs/swing_lows and
    structural direction must be unchanged before vs. after the
    migration, since the pivot algorithms were proven identical
    above."""
    candles = _synthetic_candles(80, seed=42)

    state_new = ms_builder.build_market_state(candles, symbol="BTC_USDT", timeframe="15m")

    # Force the OLD inline algorithm back in, rebuild, compare.
    original = ms_builder._find_pivots
    ms_builder._find_pivots = _pre_migration_builder_find_pivots
    try:
        state_old = ms_builder.build_market_state(candles, symbol="BTC_USDT", timeframe="15m")
    finally:
        ms_builder._find_pivots = original

    new_highs = [(s.index, s.kind, round(s.price, 6)) for s in state_new.swing_highs]
    old_highs = [(s.index, s.kind, round(s.price, 6)) for s in state_old.swing_highs]
    new_lows = [(s.index, s.kind, round(s.price, 6)) for s in state_new.swing_lows]
    old_lows = [(s.index, s.kind, round(s.price, 6)) for s in state_old.swing_lows]

    assert new_highs == old_highs
    assert new_lows == old_lows
    assert state_new.structure.direction == state_old.structure.direction
    assert state_new.structure.regime == state_old.structure.regime
    assert state_new.structure.hh == state_old.structure.hh
    assert state_new.structure.hl == state_old.structure.hl
    assert state_new.structure.lh == state_old.structure.lh
    assert state_new.structure.ll == state_old.structure.ll