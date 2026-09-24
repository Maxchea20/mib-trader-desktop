"""Scenario live bridge: M5 slot counting and multi-tick M1 watching.

Regression tests for two bugs that stopped every scenario FIRE from
reaching MEXC:
  1. Slots were counted from the OPEN of the M15 breakout candle, so any
     live confirmation (which can only happen after that candle closes)
     landed outside every slot and was SKIPPED.
  2. The M1 watcher was only checked on the single tick the engine said
     FIRE; if the confirming M1 candle had not closed yet, the thesis was
     never looked at again.
No exchange, no DB: the engine, watcher storage and candles are faked.
"""
import src.scenario_live_bridge as bridge
import src.brain.entry_timing_c_watcher as watcher_mod

ORIGIN = 1_800_000_000 - (1_800_000_000 % 900)  # M15 candle open (aligned)
M15_CLOSE = ORIGIN + 900
LEVEL = 50_000.0


def test_slot_counted_from_m15_close():
    assert bridge.classify_m5_slot(ORIGIN, M15_CLOSE) == 1
    assert bridge.classify_m5_slot(ORIGIN, M15_CLOSE + 300) == 2
    assert bridge.classify_m5_slot(ORIGIN, M15_CLOSE + 600) == 3
    assert bridge.classify_m5_slot(ORIGIN, M15_CLOSE + 900) is None
    # inside the breakout candle itself: the thesis cannot exist yet
    assert bridge.classify_m5_slot(ORIGIN, ORIGIN) is None
    assert bridge.classify_m5_slot(None, M15_CLOSE) is None


def _engine_out(action, ts, status="ARMED"):
    return {
        "action": action, "direction": "LONG", "ts": ts, "scenario": "FRESH_CLEAN_BREAKOUT",
        "what_happening": "engine idle",
        "debug": {"atr15": 200.0, "thesis": {
            "thesis_id": "TH-000001", "origin_ts": ORIGIN, "origin_level": LEVEL,
            "origin_event": "CHoCH", "status": status}},
    }


class _Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


def _setup(monkeypatch, candles_1m, clock, engine_outputs):
    monkeypatch.setattr(bridge.db, "save_scenario_watch", lambda *a, **k: None)
    monkeypatch.setattr(watcher_mod.db, "save_scenario_watch", lambda *a, **k: None)
    monkeypatch.setattr(watcher_mod.dao, "read_closed_candles", lambda tf, limit=400: list(candles_1m))
    monkeypatch.setattr(watcher_mod.time, "time", clock.time)
    monkeypatch.setattr(bridge, "_WATCHER", watcher_mod.EntryTimingCWatcher())
    monkeypatch.setattr(bridge, "_attempted_thesis_ids", set())
    monkeypatch.setattr(bridge, "_pending_watches", {})
    monkeypatch.setitem(bridge.CONFIG, "enabled_m5_slots", [1, 2])
    outs = iter(engine_outputs)
    monkeypatch.setattr(bridge, "_tick_engine", lambda live_price: next(outs))


def _m1(ts, close):
    return {"ts": ts, "open": close, "high": close, "low": close, "close": close}


def test_watch_keeps_running_until_m1_confirms(monkeypatch):
    candles = []
    clock = _Clock(M15_CLOSE + 10)  # engine confirms 10s into M5#1
    _setup(monkeypatch, candles, clock, [
        _engine_out("FIRE", M15_CLOSE),
        _engine_out("WAIT", M15_CLOSE),   # engine never says FIRE again
        _engine_out("WAIT", M15_CLOSE),
    ])

    first = bridge.evaluate_scenario(LEVEL + 10)
    assert first["action"] == "WAIT" and first["m5_slot"] == 1
    assert first["thesis_id"] in bridge._pending_watches

    # first M1 candle of M5#1 closes below the level -> still waiting
    candles.append(_m1(M15_CLOSE, LEVEL - 5))
    clock.now = M15_CLOSE + 65
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"

    # second M1 candle closes above the level -> FIRE on a WAIT tick
    candles.append(_m1(M15_CLOSE + 60, LEVEL + 20))
    clock.now = M15_CLOSE + 125
    fired = bridge.evaluate_scenario(LEVEL + 20)
    assert fired["action"] == "FIRE"
    assert fired["entry"] == LEVEL + 20
    assert fired["entry_ts"] == M15_CLOSE + 120
    assert fired["atr15"] == 200.0
    assert not bridge._pending_watches


def test_watch_times_out_to_cancel(monkeypatch):
    clock = _Clock(M15_CLOSE + 10)
    candles = []
    _setup(monkeypatch, candles, clock, [_engine_out("FIRE", M15_CLOSE), _engine_out("WAIT", M15_CLOSE)])
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    clock.now = M15_CLOSE + 10 + watcher_mod.MAX_WAIT_SECONDS + 1
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "CANCEL"
    assert not bridge._pending_watches


def test_invalidated_thesis_cancels_watch(monkeypatch):
    clock = _Clock(M15_CLOSE + 10)
    _setup(monkeypatch, [], clock, [
        _engine_out("FIRE", M15_CLOSE),
        _engine_out("CANCEL", M15_CLOSE, status="INVALIDATED"),
    ])
    assert bridge.evaluate_scenario(LEVEL)["action"] == "WAIT"
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "CANCEL" and "invalidated" in out["reason"]
    assert not bridge._pending_watches


def test_thesis_key_survives_engine_id_restart():
    a = {"thesis_id": "TH-000001", "origin_ts": ORIGIN}
    b = {"thesis_id": "TH-000001", "origin_ts": ORIGIN + 900}
    assert bridge._bridge_thesis_id(a) != bridge._bridge_thesis_id(b)
    assert bridge._bridge_thesis_id(a) == bridge._bridge_thesis_id(dict(a))


def test_late_confirmation_is_skipped(monkeypatch):
    clock = _Clock(M15_CLOSE + 1000)
    _setup(monkeypatch, [], clock, [_engine_out("FIRE", M15_CLOSE + 900)])
    out = bridge.evaluate_scenario(LEVEL)
    assert out["action"] == "SKIPPED"
    assert not bridge._pending_watches
