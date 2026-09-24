"""Early-entry S1 / S2 / C.

M15 candle 10:00-10:15 -> its own M5 slots 10:00 / 10:05 / 10:10.

  S1: 15M BOS/CHoCH seen inside the forming candle -> 5M BOS/CHoCH ->
      M1 close beyond BOTH levels -> FIRE; 5M break and M1 close both in
      10:00-10:05. If the M15 then closes back inside -> exit (rule b).
  S2: fresh 5M BOS/CHoCH any time while the thesis is valid; M1 close
      until the end of that M15 candle.
  5M BOS/CHoCH: small swings, live or closed bar, extended BOS excluded.
No exchange, no DB, no numpy: indicator maths and storage are faked.
"""
import types

import src.scenario_live_bridge as bridge
import src.brain.entry_timing_c_watcher as watcher_mod
import src.brain.scenario_engine as eng

ORIGIN = 1_800_000_000 - (1_800_000_000 % 900)  # M15 candle open, "10:00"
LEVEL = 50_000.0      # 15M break level
M5_LEVEL = 50_010.0   # 5M swing broken by the 5M BOS/CHoCH


# ------------------------------------------------------------------ slots

def test_slots_are_the_three_m5_of_the_same_m15():
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN) == 1
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN + 300) == 2
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN + 600) == 3
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN + 900) is None


# ------------------------------------------------------------------ engine

def _c(ts, o, h, low, c):
    return {"ts": ts, "open": o, "high": h, "low": low, "close": c, "volume": 1.0}


class _Obs:
    tags = []
    levels = []

    def __init__(self, history=None, flags=None):
        self.history = history or []
        self.flags = flags or {}

    def measurement(self, name, default=None):
        return 1.0 if name == "break_distance_atr" else None


def _ev(kind, ts, direction="BULLISH", ref=M5_LEVEL):
    return types.SimpleNamespace(event_type=kind, timestamp=ts, direction=direction, reference_price=ref)


def _stub_engine(monkeypatch, m15_events, m5_history=lambda bars: [], extended=False):
    monkeypatch.setattr(eng, "arrays", lambda c: {"high": None, "low": None, "close": None})
    monkeypatch.setattr(eng, "_atr", lambda *a, **k: 100.0)
    monkeypatch.setattr(eng, "_m15_candidate", m15_events)
    monkeypatch.setattr(eng, "_opposing_fast_choch", lambda d, c: False)

    def obs_structure(bars, tf, **kw):
        if tf == "5m":
            assert kw.get("pivot_window_override") == eng.FAST_M5_PIVOT  # small swings
            return _Obs(m5_history(bars), {"extended_bos": extended})
        return _Obs()
    monkeypatch.setattr(eng, "obs_structure", obs_structure)
    for name in ("obs_momentum", "obs_volume", "obs_sr", "obs_fvg"):
        monkeypatch.setattr(eng, name, lambda *a, **k: _Obs())
    monkeypatch.setattr(eng, "_execution_quality", lambda *a, **k: {
        "executable": True, "momentum_state": "NEUTRAL", "distance_atr": 0.2})


def _history():
    c15 = [_c(ORIGIN - 900 * (60 - i), 50_000, 50_010, 49_990, 49_990) for i in range(60)]
    c5 = [_c(ORIGIN - 300 * (60 - i), 50_000, 50_010, 49_990, 49_990) for i in range(60)]
    return c15, c5


def _cand(ts):
    return {"direction": "LONG", "event": "BOS", "level": LEVEL, "ts": ts, "invalidation_level": 49_800.0}


def _break_on_forming(c):
    return _cand(ORIGIN) if int(c[-1]["ts"]) == ORIGIN and c[-1]["close"] > LEVEL else None


def _live(ts, px=50_020.0):
    return _c(ts, px, px, px, px)


def test_s1_fires_inside_the_forming_m15_candle_on_a_5m_bos(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [_ev("BOS", ORIGIN)])
    c15, c5 = _history()
    out = eng.ScenarioEngine().tick(c15, c5, _live(ORIGIN))  # 10:0x, slot 1, live bar
    assert out["action"] == "FIRE" and out["scenario"] == "FRESH_CLEAN_BREAKOUT"
    th = out["debug"]["thesis"]
    assert th["origin_ts"] == ORIGIN and th["provisional"] is True
    m5 = out["debug"]["m5"]
    assert m5["event"] == "BOS" and m5["event_ts"] == ORIGIN and m5["live"] and m5["level"] == M5_LEVEL


def test_no_5m_bos_or_choch_means_no_fire(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [])
    c15, c5 = _history()
    out = eng.ScenarioEngine().tick(c15, c5, _live(ORIGIN))
    assert out["action"] != "FIRE"


def test_extended_5m_bos_does_not_count(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [_ev("BOS", ORIGIN)], extended=True)
    c15, c5 = _history()
    assert eng.ScenarioEngine().tick(c15, c5, _live(ORIGIN))["action"] != "FIRE"


def test_extended_flag_does_not_block_a_5m_choch(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [_ev("CHoCH", ORIGIN)], extended=True)
    c15, c5 = _history()
    assert eng.ScenarioEngine().tick(c15, c5, _live(ORIGIN))["action"] == "FIRE"


def test_5m_break_in_the_wrong_direction_does_not_count(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [_ev("CHoCH", ORIGIN, "BEARISH")])
    c15, c5 = _history()
    assert eng.ScenarioEngine().tick(c15, c5, _live(ORIGIN))["action"] != "FIRE"


def test_5m_break_before_the_m15_candle_does_not_count(monkeypatch):
    # the last CLOSED 5m bar (09:55) broke, nothing on the live 10:00 bar
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [_ev("BOS", ORIGIN - 300)])
    c15, c5 = _history()
    assert eng.ScenarioEngine().tick(c15, c5, _live(ORIGIN))["action"] != "FIRE"


def test_5m_break_on_the_closed_bar_counts(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [_ev("BOS", ORIGIN)])
    c15, c5 = _history()
    c5 = c5 + [_c(ORIGIN, 50_000, 50_030, 49_995, 50_020)]  # 10:00 5m closed
    out = eng.ScenarioEngine().tick(c15, c5, _live(ORIGIN + 300))
    assert out["action"] == "FIRE" and out["debug"]["m5"]["closed"] is True


def test_provisional_thesis_dropped_if_m15_closes_back_inside(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [])
    c15, c5 = _history()
    e = eng.ScenarioEngine()
    e.tick(c15, c5, _live(ORIGIN))
    assert e.thesis is not None and e.thesis.provisional
    closed = c15 + [_c(ORIGIN, 50_000, 50_030, 49_990, 49_995)]  # closed back below LEVEL
    out = e.tick(closed, c5, _live(ORIGIN + 900, 49_995))
    assert out["debug"]["thesis"]["status"] == "INVALIDATED"
    assert out["debug"]["thesis"]["invalid_reason"] == "m15_break_not_held_at_close"


def test_provisional_thesis_kept_if_break_holds_even_when_judged_late(monkeypatch):
    _stub_engine(monkeypatch, _break_on_forming, lambda bars: [])
    c15, c5 = _history()
    e = eng.ScenarioEngine()
    e.tick(c15, c5, _live(ORIGIN))
    later = c15 + [_c(ORIGIN + 900 * i, 50_000, 50_030, 49_990, 50_025) for i in range(4)]
    e.tick(later, c5, _live(ORIGIN + 3600))
    assert e.thesis is not None and e.thesis.status != "INVALIDATED" and not e.thesis.provisional


def test_forming_15m_is_built_from_its_own_5m():
    prev = [_c(ORIGIN - 900, 100, 101, 99, 100.5)]
    closed_5m = [_c(ORIGIN - 300, 100, 100, 100, 100),
                 _c(ORIGIN, 100.5, 103, 100.2, 102), _c(ORIGIN + 300, 102, 104, 101.5, 103)]
    f = eng._forming_15m(prev, closed_5m, _c(ORIGIN + 600, 105, 105, 105, 105))
    assert f == {"ts": ORIGIN, "open": 100.5, "high": 105, "low": 100.2, "close": 105, "volume": 3.0}
    assert eng._forming_15m([_c(ORIGIN - 1800, 1, 1, 1, 1)], closed_5m, _live(ORIGIN)) is None  # sync lag


# ------------------------------------------------------------------ bridge

def _fire(m5_ts, scenario="FRESH_CLEAN_BREAKOUT", fire_no=1, m5_level=M5_LEVEL, status="ARMED",
          invalid_reason=None, action="FIRE"):
    return {
        "action": action, "direction": "LONG", "ts": m5_ts, "scenario": scenario,
        "fire_id": f"TH-000001/FIRE-{fire_no:03d}" if action == "FIRE" else None,
        "what_happening": "idle",
        "debug": {"atr15": 200.0,
                  "m5": {"event": "BOS", "event_ts": m5_ts, "level": m5_level},
                  "thesis": {"thesis_id": "TH-000001", "origin_ts": ORIGIN, "origin_level": LEVEL,
                             "origin_event": "CHoCH", "status": status, "provisional": True,
                             "m5_event_id": fire_no, "invalid_reason": invalid_reason}},
    }


def _wait():
    return {"action": "WAIT", "scenario": "NO_SETUP", "what_happening": "idle",
            "debug": {"thesis": None}}


class _Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


def _setup(monkeypatch, candles_1m, clock, outs):
    monkeypatch.setattr(bridge.db, "save_scenario_watch", lambda *a, **k: None)
    monkeypatch.setattr(watcher_mod.db, "save_scenario_watch", lambda *a, **k: None)
    monkeypatch.setattr(watcher_mod.dao, "read_closed_candles", lambda tf, limit=400: list(candles_1m))
    monkeypatch.setattr(watcher_mod.time, "time", clock.time)
    monkeypatch.setattr(bridge, "_WATCHER", watcher_mod.EntryTimingCWatcher())
    monkeypatch.setattr(bridge, "_attempted_thesis_ids", set())
    monkeypatch.setattr(bridge, "_pending_watches", {})
    it = iter(outs)
    monkeypatch.setattr(bridge, "_tick_engine", lambda live_price: next(it))


def _m1(ts, close):
    return {"ts": ts, "open": close, "high": close, "low": close, "close": close}


def test_s1_m1_must_close_beyond_both_levels_within_first_5_minutes(monkeypatch):
    candles = []
    clock = _Clock(ORIGIN + 70)  # 5M BOS seen live at 10:01:10
    _setup(monkeypatch, candles, clock, [_fire(ORIGIN)] + [_wait()] * 5)
    first = bridge.evaluate_scenario(LEVEL)
    assert first["action"] == "WAIT" and first["setup"] == "S1" and first["m5_slot"] == 1
    assert first["trigger_level"] == M5_LEVEL and first["window_end_ts"] == ORIGIN + 300

    candles.append(_m1(ORIGIN + 60, LEVEL + 5))     # 10:01 past the 15M level only -> no
    clock.now = ORIGIN + 125
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    candles.append(_m1(ORIGIN + 120, M5_LEVEL + 5))  # 10:02 past both -> FIRE
    clock.now = ORIGIN + 185
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "FIRE" and out["entry_ts"] == ORIGIN + 180 and out["atr15"] == 200.0


def test_s1_cancels_when_first_5_minutes_end_without_m1(monkeypatch):
    candles = [_m1(ORIGIN + m * 60, LEVEL + 5) for m in range(5)]  # never past the 5M level
    clock = _Clock(ORIGIN + 70)
    _setup(monkeypatch, candles, clock, [_fire(ORIGIN), _wait()])
    bridge.evaluate_scenario(LEVEL)
    clock.now = ORIGIN + 305
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "CANCEL" and "window ended" in out["reason"]
    assert not bridge._pending_watches


def test_s1_5m_break_in_slot_2_is_skipped(monkeypatch):
    _setup(monkeypatch, [], _Clock(ORIGIN + 320), [_fire(ORIGIN + 300)])
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "SKIPPED" and out["m5_slot"] == 2


def test_s2_any_time_and_m1_until_end_of_that_m15(monkeypatch):
    later = ORIGIN + 3 * 3600 + 600          # 13:10, slot 3 of the 13:00 candle
    candles = []
    clock = _Clock(later + 20)
    _setup(monkeypatch, candles, clock,
           [_fire(later, scenario="FRESH_PULLBACK_CONTINUATION")] + [_wait()] * 5)
    first = bridge.evaluate_scenario(LEVEL)
    assert first["action"] == "WAIT" and first["setup"] == "S2"
    assert first["window_end_ts"] == ORIGIN + 3 * 3600 + 900
    candles.extend(_m1(later + m * 60, M5_LEVEL - 1) for m in range(4))  # 13:10-13:13 not past
    candles.append(_m1(later + 240, M5_LEVEL + 1))  # 13:14 M1, closes 13:15 -> last chance
    clock.now = later + 305
    assert bridge.evaluate_scenario(LEVEL)["action"] == "FIRE"


def test_m1_before_the_5m_confirmation_does_not_count(monkeypatch):
    candles = [_m1(ORIGIN, M5_LEVEL + 50)]  # 10:00 M1 already past, before the 5M BOS at 10:02
    clock = _Clock(ORIGIN + 130)
    _setup(monkeypatch, candles, clock, [_fire(ORIGIN)] + [_wait()] * 2)
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    candles.append(_m1(ORIGIN + 60, M5_LEVEL + 50))  # 10:01, also before
    clock.now = ORIGIN + 150
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"


def test_thesis_invalidated_cancels_the_watch(monkeypatch):
    _setup(monkeypatch, [], _Clock(ORIGIN + 70), [
        _fire(ORIGIN),
        _fire(ORIGIN, action="CANCEL", status="INVALIDATED", invalid_reason="opposing_fast_choch"),
    ])
    bridge.evaluate_scenario(LEVEL)
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "CANCEL" and "opposing_fast_choch" in out["reason"]


def test_unsynced_m1_is_waited_for_then_treated_as_gap(monkeypatch):
    clock = _Clock(ORIGIN + 70)
    _setup(monkeypatch, [], clock, [_fire(ORIGIN)] + [_wait()] * 3)
    bridge.evaluate_scenario(LEVEL)
    clock.now = ORIGIN + 150  # 10:01 M1 closed 30s ago, not stored yet
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    clock.now = ORIGIN + 120 + watcher_mod.SYNC_GRACE_SECONDS + 5
    assert bridge.evaluate_scenario(LEVEL)["action"] == "CANCEL"


def test_same_event_never_attempted_twice(monkeypatch):
    _setup(monkeypatch, [], _Clock(ORIGIN + 320), [_fire(ORIGIN + 300)] * 2)
    assert bridge.evaluate_scenario(LEVEL)["action"] == "SKIPPED"
    assert bridge.evaluate_scenario(LEVEL)["action"] == "ALREADY_ATTEMPTED"


def test_thesis_key_is_unique_across_engine_restarts():
    a = bridge._bridge_thesis_id({"thesis_id": "TH-000001", "origin_ts": ORIGIN})
    b = bridge._bridge_thesis_id({"thesis_id": "TH-000001", "origin_ts": ORIGIN + 900})
    assert a != b


# ------------------------------------------------------------------ rule (b)

def _rule_b_setup(monkeypatch, failed, thesis):
    import src.autotrader_loop as loop
    import src.paper_trading as pt
    import src.autotrader_exec as ex
    calls = {"flatten": 0, "closed": None}
    monkeypatch.setattr(pt, "_thesis", lambda t: thesis)
    monkeypatch.setattr(pt, "close_trade", lambda tid, px, reason: calls.__setitem__("closed", (tid, reason)))
    monkeypatch.setattr(ex, "_fresh_price", lambda: 49_990.0)
    monkeypatch.setattr(loop, "flatten_mexc", lambda *a: calls.__setitem__("flatten", calls["flatten"] + 1))
    monkeypatch.setattr(bridge, "break_failed", lambda origin_ts, side: failed)
    monkeypatch.setitem(loop.STATE, "m15_break_checked_trade", None)
    return loop, calls


S1_EARLY = {"engine": "scenario", "setup": "S1", "provisional": True, "origin_ts": ORIGIN}
TRADE = {"id": "t1", "side": "LONG", "entry_price": 50_020.0}


def test_rule_b_exits_when_m15_closes_back_inside(monkeypatch):
    loop, calls = _rule_b_setup(monkeypatch, True, S1_EARLY)
    assert loop._exit_if_m15_break_failed(TRADE, 49_990.0) is True
    assert calls["flatten"] == 1 and calls["closed"] == ("t1", "M15_BREAK_FAILED")


def test_rule_b_keeps_trade_when_break_held(monkeypatch):
    loop, calls = _rule_b_setup(monkeypatch, False, S1_EARLY)
    assert loop._exit_if_m15_break_failed(TRADE, 50_030.0) is False
    assert calls["flatten"] == 0 and calls["closed"] is None


def test_rule_b_waits_until_the_m15_candle_closed(monkeypatch):
    loop, calls = _rule_b_setup(monkeypatch, None, S1_EARLY)
    assert loop._exit_if_m15_break_failed(TRADE, 50_030.0) is False and calls["flatten"] == 0


def test_rule_b_does_not_touch_s2_trades(monkeypatch):
    loop, calls = _rule_b_setup(monkeypatch, True, {**S1_EARLY, "setup": "S2"})
    assert loop._exit_if_m15_break_failed(TRADE, 49_990.0) is False and calls["flatten"] == 0
