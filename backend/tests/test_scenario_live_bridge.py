"""S1 / S2 / C live wiring: slot timing, continuous M1 check, intrabar M15.

Design under test:
  - The 3 M5 slots belong to the SAME M15 candle (10:00 -> 10:00/10:05/10:10).
  - S1 (FRESH_CLEAN_BREAKOUT) must confirm in an enabled slot of the
    breakout M15 candle. S2 (FRESH_PULLBACK_CONTINUATION) has no slot
    limit -- valid until the thesis is invalidated.
  - C (M1 close beyond the M15 level) is checked continuously from the M5
    confirmation until that M15 candle ends; a miss cancels the attempt
    only, never the thesis.
  - The engine opens a provisional thesis while the M15 breakout candle
    is still forming, and drops it if the break does not hold at close.
No exchange, no DB: engine, storage and candles are faked.
"""
import src.scenario_live_bridge as bridge
import src.brain.entry_timing_c_watcher as watcher_mod
import src.brain.scenario_engine as eng

ORIGIN = 1_800_000_000 - (1_800_000_000 % 900)  # M15 breakout candle open, e.g. 10:00
LEVEL = 50_000.0


# ---------------------------------------------------------------- slots

def test_slots_are_inside_the_same_m15_candle():
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN) == 1          # 10:00-10:05
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN + 300) == 2    # 10:05-10:10
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN + 600) == 3    # 10:10-10:15
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN + 900) is None  # 10:15 = next M15 candle
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN - 300) is None
    assert bridge.classify_m5_slot(None, ORIGIN) is None


# ---------------------------------------------------------------- bridge

def _engine_out(action, ts, scenario="FRESH_CLEAN_BREAKOUT", status="ARMED", fire_no=1,
                origin_ts=ORIGIN, invalid_reason=None):
    return {
        "action": action, "direction": "LONG", "ts": ts, "scenario": scenario,
        "fire_id": f"TH-000001/FIRE-{fire_no:03d}" if action == "FIRE" else None,
        "what_happening": "engine idle",
        "debug": {"atr15": 200.0, "thesis": {
            "thesis_id": "TH-000001", "origin_ts": origin_ts, "origin_level": LEVEL,
            "origin_event": "CHoCH", "status": status, "provisional": True,
            "m5_event_id": fire_no, "invalid_reason": invalid_reason}},
    }


class _Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


def _setup(monkeypatch, candles_1m, clock, engine_outputs, slots=(1, 2)):
    monkeypatch.setattr(bridge.db, "save_scenario_watch", lambda *a, **k: None)
    monkeypatch.setattr(watcher_mod.db, "save_scenario_watch", lambda *a, **k: None)
    monkeypatch.setattr(watcher_mod.dao, "read_closed_candles", lambda tf, limit=400: list(candles_1m))
    monkeypatch.setattr(watcher_mod.time, "time", clock.time)
    monkeypatch.setattr(bridge, "_WATCHER", watcher_mod.EntryTimingCWatcher())
    monkeypatch.setattr(bridge, "_attempted_thesis_ids", set())
    monkeypatch.setattr(bridge, "_pending_watches", {})
    monkeypatch.setitem(bridge.CONFIG, "enabled_m5_slots", list(slots))
    outs = iter(engine_outputs)
    monkeypatch.setattr(bridge, "_tick_engine", lambda live_price: next(outs))


def _m1(ts, close):
    return {"ts": ts, "open": close, "high": close, "low": close, "close": close}


def test_s1_c_checks_every_m1_until_the_m15_candle_ends(monkeypatch):
    """M5 confirms in slot 1 at 10:02; M1 closes stay below the level
    through slot 1 and slot 2; the 10:11 M1 (slot 3) closes above -> FIRE."""
    candles = []
    clock = _Clock(ORIGIN + 150)
    _setup(monkeypatch, candles, clock,
           [_engine_out("FIRE", ORIGIN)] + [_engine_out("WAIT", ORIGIN)] * 20)

    first = bridge.evaluate_scenario(LEVEL + 10)
    assert first["action"] == "WAIT" and first["m5_slot"] == 1 and first["setup"] == "S1"

    fired = None
    for minute in range(2, 15):  # M1 candles 10:02 .. 10:14
        t = ORIGIN + minute * 60
        candles.append(_m1(t, LEVEL + 30 if minute == 11 else LEVEL - 5))
        clock.now = t + 65
        out = bridge.evaluate_scenario(LEVEL)
        if out["action"] != "WAIT":
            fired = out
            break
    assert fired is not None and fired["action"] == "FIRE"
    assert fired["entry_ts"] == ORIGIN + 12 * 60  # close of the 10:11 M1
    assert fired["entry"] == LEVEL + 30
    assert fired["atr15"] == 200.0
    assert not bridge._pending_watches


def test_m1_before_the_m5_confirmation_does_not_count(monkeypatch):
    candles = [_m1(ORIGIN, LEVEL + 50)]  # 10:00 M1 closed above, BEFORE M5 confirmation
    clock = _Clock(ORIGIN + 400)          # M5 confirms at 10:06:40 (slot 2)
    _setup(monkeypatch, candles, clock, [_engine_out("FIRE", ORIGIN + 300), _engine_out("WAIT", ORIGIN + 300)])
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    candles.append(_m1(ORIGIN + 360, LEVEL + 40))  # 10:06 M1, contains the confirmation
    clock.now = ORIGIN + 425
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "FIRE" and out["entry_ts"] == ORIGIN + 420


def test_c_miss_cancels_the_attempt_at_m15_close(monkeypatch):
    candles = [_m1(ORIGIN + m * 60, LEVEL - 5) for m in range(15)]
    clock = _Clock(ORIGIN + 30)
    _setup(monkeypatch, candles, clock, [_engine_out("FIRE", ORIGIN), _engine_out("WAIT", ORIGIN)])
    bridge.evaluate_scenario(LEVEL)
    clock.now = ORIGIN + 905
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "CANCEL" and "M15 candle ended" in out["reason"]
    assert not bridge._pending_watches


def test_missing_m1_waits_for_sync_before_calling_it_a_gap(monkeypatch):
    candles = []
    clock = _Clock(ORIGIN + 10)
    _setup(monkeypatch, candles, clock, [_engine_out("FIRE", ORIGIN)] + [_engine_out("WAIT", ORIGIN)] * 3)
    bridge.evaluate_scenario(LEVEL)
    clock.now = ORIGIN + 90   # 10:00 M1 closed 30s ago, not synced yet
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    clock.now = ORIGIN + 60 + watcher_mod.SYNC_GRACE_SECONDS + 5
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "CANCEL" and "sync gap" in out["reason"]


def test_s1_after_the_breakout_candle_is_skipped(monkeypatch):
    _setup(monkeypatch, [], _Clock(ORIGIN + 950), [_engine_out("FIRE", ORIGIN + 900)])
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "SKIPPED" and "outside the 3 M5 slots" in out["reason"]


def test_s1_disabled_slot_is_skipped(monkeypatch):
    _setup(monkeypatch, [], _Clock(ORIGIN + 650), [_engine_out("FIRE", ORIGIN + 600)])
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "SKIPPED" and out["m5_slot"] == 3


def test_s2_has_no_slot_limit_and_uses_its_own_m15_candle(monkeypatch):
    later = ORIGIN + 3 * 3600 + 300  # 3 hours later, 2nd M5 of that M15 candle
    candles = []
    clock = _Clock(later + 20)
    _setup(monkeypatch, candles, clock,
           [_engine_out("FIRE", later, scenario="FRESH_PULLBACK_CONTINUATION"),
            _engine_out("WAIT", later)])
    first = bridge.evaluate_scenario(LEVEL)
    assert first["action"] == "WAIT" and first["setup"] == "S2"
    assert first["window_open_ts"] == ORIGIN + 3 * 3600
    assert first["m5_slot"] == 2
    candles.append(_m1(later, LEVEL + 15))
    clock.now = later + 65
    assert bridge.evaluate_scenario(LEVEL)["action"] == "FIRE"


def test_invalidated_thesis_cancels_the_watch(monkeypatch):
    _setup(monkeypatch, [], _Clock(ORIGIN + 10), [
        _engine_out("FIRE", ORIGIN),
        _engine_out("CANCEL", ORIGIN + 900, status="INVALIDATED",
                    invalid_reason="m15_break_not_held_at_close"),
    ])
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "CANCEL" and "m15_break_not_held_at_close" in out["reason"]
    assert not bridge._pending_watches


def test_c_miss_does_not_block_the_next_event_of_the_same_thesis(monkeypatch):
    candles = [_m1(ORIGIN + m * 60, LEVEL - 5) for m in range(15)]
    later = ORIGIN + 3600
    clock = _Clock(ORIGIN + 30)
    _setup(monkeypatch, candles, clock, [
        _engine_out("FIRE", ORIGIN, fire_no=1),
        _engine_out("WAIT", ORIGIN),
        _engine_out("FIRE", later, scenario="FRESH_PULLBACK_CONTINUATION", fire_no=2),
    ])
    bridge.evaluate_scenario(LEVEL)
    clock.now = ORIGIN + 905
    assert bridge.evaluate_scenario(LEVEL)["action"] == "CANCEL"
    clock.now = later + 10
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "WAIT" and out["setup"] == "S2"


def test_same_execution_event_is_never_attempted_twice(monkeypatch):
    _setup(monkeypatch, [], _Clock(ORIGIN + 950), [_engine_out("FIRE", ORIGIN + 900)] * 2)
    assert bridge.evaluate_scenario(LEVEL)["action"] == "SKIPPED"
    assert bridge.evaluate_scenario(LEVEL)["action"] == "ALREADY_ATTEMPTED"


def test_thesis_key_survives_engine_id_restart():
    a = {"thesis_id": "TH-000001", "origin_ts": ORIGIN}
    b = {"thesis_id": "TH-000001", "origin_ts": ORIGIN + 900}
    assert bridge._bridge_thesis_id(a) != bridge._bridge_thesis_id(b)


# ---------------------------------------------------------------- engine

def _c(ts, o, h, low, c):
    return {"ts": ts, "open": o, "high": h, "low": low, "close": c, "volume": 1.0}


def test_forming_15m_is_built_from_its_own_5m_candles():
    prev = [_c(ORIGIN - 900, 100, 101, 99, 100.5)]
    closed_5m = [_c(ORIGIN - 300, 100, 100, 100, 100),
                 _c(ORIGIN, 100.5, 103, 100.2, 102), _c(ORIGIN + 300, 102, 104, 101.5, 103)]
    live = _c(ORIGIN + 600, 105, 105, 105, 105)
    f = eng._forming_15m(prev, closed_5m, live)
    assert f == {"ts": ORIGIN, "open": 100.5, "high": 105, "low": 100.2, "close": 105, "volume": 3.0}
    # slot 1: no closed 5m yet -> open is the previous M15 close
    f1 = eng._forming_15m(prev, closed_5m[:1], _c(ORIGIN, 101, 101, 101, 101))
    assert f1["open"] == 100.5 and f1["close"] == 101
    # previous M15 missing (sync lag) -> never bridges the gap
    assert eng._forming_15m(prev[:0] + [_c(ORIGIN - 1800, 1, 1, 1, 1)], closed_5m, live) is None
    assert eng._forming_15m(prev, closed_5m, None) is None


class _Obs:
    tags = []
    levels = []

    def measurement(self, name, default=None):
        return None if name != "break_distance_atr" else 1.0


def _stub_engine(monkeypatch, m15_events):
    """m15_events(candles) -> candidate dict or None. Everything numeric
    that needs numpy is stubbed; the tick() flow itself is real."""
    monkeypatch.setattr(eng, "arrays", lambda c: {"high": None, "low": None, "close": None})
    monkeypatch.setattr(eng, "_atr", lambda *a, **k: 100.0)
    monkeypatch.setattr(eng, "_m15_candidate", m15_events)
    monkeypatch.setattr(eng, "_fast_pivots_5m", lambda c: {"high": 50_010.0, "low": 49_900.0})
    monkeypatch.setattr(eng, "_opposing_fast_choch", lambda d, c: False)
    for name in ("obs_momentum", "obs_volume", "obs_sr", "obs_fvg", "obs_structure"):
        monkeypatch.setattr(eng, name, lambda *a, **k: _Obs())
    monkeypatch.setattr(eng, "_execution_quality", lambda *a, **k: {
        "executable": True, "momentum_state": "NEUTRAL", "distance_atr": 0.2})


def _history():
    c15 = [_c(ORIGIN - 900 * (60 - i), 50_000, 50_010, 49_990, 50_000) for i in range(60)]
    c5 = [_c(ORIGIN - 300 * (60 - i), 50_000, 50_010, 49_990, 50_000) for i in range(60)]
    return c15, c5


def _cand(ts):
    return {"direction": "LONG", "event": "BOS", "level": LEVEL, "ts": ts, "invalidation_level": 49_800.0}


def test_engine_fires_s1_inside_the_forming_breakout_candle(monkeypatch):
    # break visible only when the forming candle (ts == ORIGIN) is included
    _stub_engine(monkeypatch, lambda c: _cand(ORIGIN) if int(c[-1]["ts"]) == ORIGIN else None)
    c15, c5 = _history()
    e = eng.ScenarioEngine()
    out = e.tick(c15, c5, _c(ORIGIN + 300, 50_020, 50_020, 50_020, 50_020))  # 10:05, slot 2
    assert out["action"] == "FIRE" and out["scenario"] == "FRESH_CLEAN_BREAKOUT"
    assert out["debug"]["thesis"]["origin_ts"] == ORIGIN
    assert out["debug"]["thesis"]["provisional"] is True
    assert bridge.classify_m5_slot(ORIGIN, out["ts"]) == 2


def test_engine_drops_provisional_thesis_if_break_fails_at_close(monkeypatch):
    _stub_engine(monkeypatch, lambda c: _cand(ORIGIN) if int(c[-1]["ts"]) == ORIGIN
                 and c[-1]["close"] > LEVEL else None)
    c15, c5 = _history()
    e = eng.ScenarioEngine()
    e.tick(c15, c5, _c(ORIGIN + 300, 50_020, 50_020, 50_020, 50_020))
    assert e.thesis is not None and e.thesis.provisional
    # M15 closes back below the level -> no BOS on the closed candle
    closed = c15 + [_c(ORIGIN, 50_000, 50_030, 49_990, 49_995)]
    out = e.tick(closed, c5, _c(ORIGIN + 900, 49_995, 49_995, 49_995, 49_995))
    assert out["debug"]["thesis"]["status"] == "INVALIDATED"
    assert out["debug"]["thesis"]["invalid_reason"] == "m15_break_not_held_at_close"
    assert out["scenario"] == "SETUP_INVALIDATED"


def test_engine_keeps_thesis_when_break_holds_at_close(monkeypatch):
    _stub_engine(monkeypatch, lambda c: _cand(ORIGIN) if int(c[-1]["ts"]) == ORIGIN else None)
    c15, c5 = _history()
    e = eng.ScenarioEngine()
    e.tick(c15, c5, _c(ORIGIN + 300, 50_020, 50_020, 50_020, 50_020))
    closed = c15 + [_c(ORIGIN, 50_000, 50_030, 49_990, 50_025)]
    e.tick(closed, c5, _c(ORIGIN + 900, 50_025, 50_025, 50_025, 50_025))
    assert e.thesis is not None and e.thesis.status != "INVALIDATED"
    assert e.thesis.provisional is False


def test_engine_judges_the_breakout_candle_even_after_later_closes(monkeypatch):
    _stub_engine(monkeypatch, lambda c: _cand(ORIGIN) if int(c[-1]["ts"]) == ORIGIN else None)
    c15, c5 = _history()
    e = eng.ScenarioEngine()
    e.tick(c15, c5, _c(ORIGIN + 300, 50_020, 50_020, 50_020, 50_020))
    # engine not ticked for an hour (position open); 4 more M15 closed since
    closed = c15 + [_c(ORIGIN + 900 * i, 50_000, 50_030, 49_990, 50_025) for i in range(5)]
    e.tick(closed, c5, _c(ORIGIN + 4500, 50_025, 50_025, 50_025, 50_025))
    assert e.thesis is not None and e.thesis.status != "INVALIDATED"
    assert e.thesis.provisional is False
