"""Swing AI: schema strictness, causality, event detection, risk layer, paper fills, and the full paper pipeline."""
import json
import os
import random

import numpy as np
import pandas as pd
import pytest

from src.market_data import database as db
from src.swing_ai import engine, events, market_state as MS, paper, risk, schema, store
from src.swing_ai.config import SwingConfig
from src.swing_ai.llm import FakeLLM
from src.swing_ai.manager import SwingManager
from src.swing_ai.source import ListSource, TF_SEC

T0 = 1_700_000_000 - 1_700_000_000 % 86400


def make_data(days=30, seed=1):
    rng = np.random.default_rng(seed)
    n = days * 1440
    close = 60000 * np.exp(np.cumsum(rng.normal(0, 0.0006, n)))
    op = np.concatenate([[60000.0], close[:-1]])
    df = pd.DataFrame({"ts": T0 + np.arange(n) * 60, "open": op,
                       "high": np.maximum(op, close) * (1 + np.abs(rng.normal(0, 0.0002, n))),
                       "low": np.minimum(op, close) * (1 - np.abs(rng.normal(0, 0.0002, n))),
                       "close": close, "volume": rng.uniform(50, 150, n)})
    out = {}
    for tf, sec in TF_SEC.items():
        g = df.groupby(df["ts"] // sec * sec).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                                   close=("close", "last"), volume=("volume", "sum")).reset_index()
        out[tf] = [{k: (int(r[k]) if k == "ts" else float(r[k])) for k in ("ts", "open", "high", "low", "close", "volume")}
                   for _, r in g.iterrows()]
    return out


@pytest.fixture()
def tmpdb(tmp_path):
    old_path, old_conn = db._DB_PATH, db._conn
    db._DB_PATH, db._conn = str(tmp_path / "t.db"), None
    db.init_db()
    store._ready = False
    store.init()
    yield
    if db._conn is not None:
        db._conn.close()
    db._DB_PATH, db._conn = old_path, old_conn
    store._ready = False


DATA = make_data()
NOW = T0 + 25 * 86400 + 17 * 60 + 20            # a few seconds into a 1m bar, mid-day 25


def snap_at(now=NOW, data=DATA):
    src = ListSource(data)
    price = [c for c in data["1m"] if c["ts"] + 60 <= now][-1]["close"]
    return MS.build_snapshot(src, now, price, {"bid": price - 0.5, "ask": price + 0.5, "age_seconds": 1})


# ------------------------------------------------------------------ schema
GOOD = {"decision": "LONG", "confidence": 0.7, "entry": 100.0, "entry_type": "MARKET", "sl": 90.0, "tp": 130.0,
        "thesis": "x", "invalidation": "y", "invalidation_price": 89.0, "context_timeframe": "4H"}


def test_schema_accepts_valid_and_rejects_everything_else():
    assert schema.parse_entry(json.dumps(GOOD)).decision == "LONG"
    nt = {**GOOD, "decision": "NO_TRADE", "entry": None, "sl": None, "tp": None, "entry_type": None}
    assert schema.parse_entry(nt).decision == "NO_TRADE"
    for bad in ["not json", "[]", {**GOOD, "decision": "BUY"}, {**GOOD, "confidence": 1.5}, {**GOOD, "sl": None},
                {**GOOD, "entry_type": "STOP"}, {**GOOD, "thesis": ""}, {**GOOD, "context_timeframe": "1D"},
                {**GOOD, "entry": "100"}, {**GOOD, "tp": float("nan")}]:
        with pytest.raises(schema.SchemaError):
            schema.parse_entry(bad)
    m = {"thesis_status": "VALID", "action": "HOLD", "new_sl": None, "confidence": 0.6, "reason": "ok", "reversal_candidate": False}
    assert schema.parse_manage(m).action == "HOLD"
    with pytest.raises(schema.SchemaError):
        schema.parse_manage({**m, "action": "MOVE_SL"})           # MOVE_SL needs new_sl
    for js in (schema.entry_json_schema(), schema.manage_json_schema()):    # OpenAI strict mode: every key required
        sc = js["schema"]
        assert sc["additionalProperties"] is False and set(sc["required"]) == set(sc["properties"])


# ------------------------------------------------------------------ causality
def test_snapshot_uses_only_closed_candles_and_ignores_the_future():
    s1 = snap_at()
    assert s1 is not None and s1["as_of"] == int(NOW)
    for tf in ("4h", "1h", "15m", "5m", "1m"):
        for row in s1["timeframes"][tf]["candles"]:
            assert row[0] + TF_SEC[tf] <= NOW                              # nothing still forming
    for tf in ("4h", "1h", "15m"):
        blk = s1["timeframes"][tf]
        for sw in blk["swing_highs"] + blk["swing_lows"]:
            assert sw["confirmed_ts"] + TF_SEC[tf] <= NOW                  # a swing is known only once confirmed and closed
    future = {tf: [dict(c) for c in rows] for tf, rows in DATA.items()}    # rewrite everything after NOW
    for tf, rows in future.items():
        for c in rows:
            if c["ts"] + TF_SEC[tf] > NOW:
                c["open"] = c["high"] = c["low"] = c["close"] = 1.0
    price = [c for c in DATA["1m"] if c["ts"] + 60 <= NOW][-1]["close"]
    s2 = MS.build_snapshot(ListSource(future), NOW, price, {"bid": price - 0.5, "ask": price + 0.5, "age_seconds": 1})
    assert json.dumps(s1, sort_keys=True) == json.dumps(s2, sort_keys=True)  # the future cannot change the snapshot
    truncated = {tf: [c for c in rows if c["ts"] + TF_SEC[tf] <= NOW] for tf, rows in DATA.items()}
    s3 = MS.build_snapshot(ListSource(truncated), NOW, price, {"bid": price - 0.5, "ask": price + 0.5, "age_seconds": 1})
    assert json.dumps(s1, sort_keys=True) == json.dumps(s3, sort_keys=True)
    assert MS.build_snapshot(ListSource({tf: rows[:10] for tf, rows in DATA.items()}), NOW, 60000.0) is None


def test_snapshot_contains_what_the_prompt_promises():
    s = snap_at()
    for tf in ("4h", "1h", "15m"):
        b = s["timeframes"][tf]
        for k in ("candles", "atr", "trend", "structure_events", "swing_highs", "levels", "momentum", "volume", "volatility", "extension"):
            assert k in b
    assert "liquidity_sweeps" in s["timeframes"]["15m"] and "entry_refinement" in s["timeframes"]["5m"]
    assert set(s["derived"]) >= {"trend_alignment", "volatility_regime_1h", "room_in_1h_atr", "context_stats", "price_extended"}
    assert s["market"]["spread_pct"] is not None and len(json.dumps(s)) < 60_000


# ------------------------------------------------------------------ events
def _summary(**kw):
    base = {"as_of": 1000, "price": 100.0, "trend": {"4h": "UP", "1h": "UP", "15m": "UP"},
            "last_structure": {"4h": None, "1h": None, "15m": None}, "last_sweep_ts": None, "atr15": 1.0,
            "atr_ratio_15m": 1.0, "last15_ts": 900, "last15_close": 100.0, "prev15_close": 100.0, "last15_range_atr": 1.0,
            "rsi_1h": 55.0, "rsi_15m": 55.0, "levels": []}
    base.update(kw)
    return base


def test_events_are_edge_triggered_and_cooled_down():
    a = _summary()
    assert events.detect(None, a) == []
    b = _summary(as_of=1900, last_structure={"4h": None, "1h": (1800, "CHoCH", "DOWN"), "15m": None})
    assert [e["kind"] for e in events.detect(a, b)] == ["STRUCTURE_1H"]
    assert events.detect(b, b) == []                                        # no change, no event
    c = _summary(last15_ts=1800, last15_close=101.5, prev15_close=99.5, levels=[(100.5, 3)], price=101.5)
    assert "LEVEL_BREAK_OR_RECLAIM" in [e["kind"] for e in events.detect(a, c)]
    v = _summary(last15_ts=1800, atr_ratio_15m=1.8)
    assert "VOLATILITY_EXPANSION" in [e["kind"] for e in events.detect(a, v)]
    r = _summary(rsi_1h=48.0)
    assert "MOMENTUM_SHIFT" in [e["kind"] for e in events.detect(a, r)]
    pos = {"id": 1, "status": "OPEN", "side": "LONG", "sl": 95.0, "invalidation_price": 97.0}
    inv = events.invalidation_events(pos, 96.9, 1.0)
    assert inv and inv[0]["kind"] == "TRADE_INVALIDATION" and inv[0]["urgent"]
    cd = events.Cooldown(900)
    e1, e2 = {"kind": "X", "key": "a"}, {"kind": "X", "key": "b"}
    assert cd.allow(e1, 0) and not cd.allow(e1, 5000) and not cd.allow(e2, 100) and cd.allow(e2, 1000)
    assert cd.allow({"kind": "X", "key": "c", "urgent": True}, 1001)        # urgent ignores the quiet period


# ------------------------------------------------------------------ risk layer
def _snap_stub(price=100.0, a1h=2.0, a15=0.5, spread=0.005, age=1):
    return {"market": {"price": price, "bid": price - 0.005, "ask": price + 0.005, "spread_pct": spread, "ticker_age_seconds": age},
            "timeframes": {"15m": {"atr": a15}, "1h": {"atr": a1h}}}


def _dec(**kw):
    d = schema.EntryDecision(decision="LONG", confidence=0.7, entry=100.0, entry_type="MARKET", sl=96.0, tp=108.0,
                             thesis="t", invalidation="i", invalidation_price=95.5, context_timeframe="1H")
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_risk_layer_accepts_good_sizes_correctly_and_rejects_bad():
    cfg = SwingConfig()
    ok = risk.validate_entry(_dec(), _snap_stub(), cfg, {"now": 0})
    assert ok.ok and ok.plan["entry_type"] == "MARKET" and ok.plan["rr"] == pytest.approx(8.0 / 4.005, rel=1e-2)
    assert ok.plan["risk_usd"] <= cfg.equity_usd * cfg.risk_pct / 100 + 1e-6            # 1% of equity at most
    assert ok.plan["leverage"] <= cfg.max_leverage + 1e-9
    cases = {
        "sl above entry": _dec(sl=104.0), "tp below entry": _dec(tp=98.0), "rr too low": _dec(tp=104.0),
        "stop too tight": _dec(sl=99.5, tp=110.0), "stop too wide": _dec(sl=85.0, tp=140.0),
        "low confidence": _dec(confidence=0.3), "market but far": _dec(entry=103.0, tp=115.0),
        "limit wrong side": _dec(entry=101.0, entry_type="LIMIT", sl=97.0, tp=112.0),
    }
    for name, d in cases.items():
        assert not risk.validate_entry(d, _snap_stub(), cfg, {"now": 0}).ok, name
    assert not risk.validate_entry(_dec(), _snap_stub(spread=0.2), cfg, {"now": 0}).ok
    assert not risk.validate_entry(_dec(), _snap_stub(age=99), cfg, {"now": 0}).ok
    assert not risk.validate_entry(_dec(), _snap_stub(), cfg, {"now": 0, "has_active": True}).ok
    assert not risk.validate_entry(_dec(), _snap_stub(), cfg, {"now": 0, "trades_today": 3}).ok
    assert not risk.validate_entry(_dec(), _snap_stub(), cfg, {"now": 0, "daily_r": -3.5}).ok
    assert not risk.validate_entry(_dec(), _snap_stub(), cfg, {"now": 1000, "last_loss_ts": 900}).ok
    assert not risk.validate_entry(_dec(decision="NO_TRADE"), _snap_stub(), cfg, {"now": 0}).ok
    lim = risk.validate_entry(_dec(entry=98.0, entry_type="LIMIT", sl=94.0, tp=110.0), _snap_stub(), cfg, {"now": 0})
    assert lim.ok and lim.plan["entry_type"] == "LIMIT" and lim.plan["expires_ts"] and lim.plan["fee_entry"] == cfg.maker_fee
    small = SwingConfig(equity_usd=10_000, max_leverage=0.5)                          # leverage cap shrinks the size, never the stop
    tight = risk.validate_entry(_dec(entry=60000.0, sl=59400.0, tp=61800.0), _snap_stub(price=60000.0, a1h=600.0, a15=150.0), small, {"now": 0})
    assert tight.ok and tight.plan["leverage"] <= 0.5 + 1e-9 and tight.plan["sized_down"]
    md = schema.ManageDecision(action="MOVE_SL", new_sl=98.0, thesis_status="VALID", confidence=0.6)
    pos = {"side": "LONG", "sl": 96.0}
    assert risk.validate_manage(md, pos, 101.0, 0.5, cfg).ok
    assert not risk.validate_manage(schema.ManageDecision(action="MOVE_SL", new_sl=95.0), pos, 101.0, 0.5, cfg).ok   # loosening
    assert not risk.validate_manage(schema.ManageDecision(action="MOVE_SL", new_sl=101.0), pos, 101.0, 0.5, cfg).ok  # through price


# ------------------------------------------------------------------ paper broker
def _plan(side="LONG", typ="MARKET", entry=100.0, sl=96.0, tp=108.0, qty=1.0):
    return {"side": side, "entry_type": typ, "entry": entry, "sl": sl, "tp": tp, "qty": qty, "risk_usd": abs(entry - sl) * qty,
            "risk_dist": abs(entry - sl), "leverage": 1.0, "rr": abs(tp - entry) / abs(entry - sl), "fee_entry": 0.0002 if typ == "MARKET" else 0.0,
            "fee_tp": 0.0, "fee_sl": 0.0002, "expires_ts": 10_000 if typ == "LIMIT" else None, "invalidation_price": None, "sized_down": False}


def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def test_paper_fills_are_causal_and_r_math_is_right(tmpdb):
    cfg = SwingConfig()
    did = store.add_decision(ts=0, kind="ENTRY", decision="LONG")
    pid = paper.open_from_plan(_plan(), {"thesis": "t", "invalidation": "i"}, did, 1000, 99.9, 100.0)
    p = store.get_position(pid)
    assert p["status"] == "OPEN" and p["fill_price"] == 100.0                      # market long fills at the ask
    p = paper.process_bar(p, _bar(1020, 100, 105, 99, 104), cfg)
    assert p["status"] == "OPEN" and p["mfe_r"] == pytest.approx(1.25) and p["mae_r"] == pytest.approx(0.25)
    p = paper.process_bar(p, _bar(1080, 104, 109, 103, 108), cfg)                    # TP touched
    assert p["status"] == "CLOSED" and p["exit_reason"] == "TP" and p["r_gross"] == pytest.approx(2.0)
    fees = 1.0 * (100.0 * 0.0002)                                                     # taker in, maker out
    assert p["r_net"] == pytest.approx(2.0 - fees / 4.0)
    # same bar touches both -> the stop wins
    did = store.add_decision(ts=0, kind="ENTRY", decision="LONG")
    q = store.get_position(paper.open_from_plan(_plan(), {"thesis": "t", "invalidation": "i"}, did, 2000, 99.9, 100.0))
    q = paper.process_bar(q, _bar(2040, 100, 110, 95, 100), cfg)
    assert q["exit_reason"] == "SL" and q["r_gross"] == pytest.approx(-1.0) and q["r_net"] < -1.0
    # limit: not filled until price trades through it; expires; fill bar checks only the stop
    did = store.add_decision(ts=0, kind="ENTRY", decision="LONG")
    lp = store.get_position(paper.open_from_plan(_plan(typ="LIMIT", entry=98.0, sl=94.0, tp=106.0), {"thesis": "t", "invalidation": "i"}, did, 3000, None, None))
    assert lp["status"] == "PENDING"
    lp = paper.process_bar(lp, _bar(3060, 100, 101, 99, 100), cfg)
    assert lp["status"] == "PENDING"
    lp = paper.process_bar(lp, _bar(3120, 100, 107, 97, 105), cfg)                    # fills at 98; the TP is not counted on the fill bar
    assert lp["status"] == "OPEN" and lp["fill_price"] == 98.0
    lp = paper.process_bar(lp, _bar(3180, 105, 107, 104, 106), cfg)
    assert lp["exit_reason"] == "TP" and lp["r_gross"] == pytest.approx(2.0)
    did = store.add_decision(ts=0, kind="ENTRY", decision="LONG")
    ex = store.get_position(paper.open_from_plan(_plan(typ="LIMIT", entry=90.0, sl=86.0, tp=98.0), {"thesis": "t", "invalidation": "i"}, did, 9000, None, None))
    ex = paper.process_bar(ex, _bar(10_020, 100, 101, 99, 100), cfg)
    assert ex["status"] == "EXPIRED"
    perf = paper.performance(store.positions())
    assert perf["trades"] == 3 and perf["net_r"] == pytest.approx(sum(r["r_net"] for r in store.positions() if r["status"] == "CLOSED"))


# ------------------------------------------------------------------ engine (AI call layer)
def test_engine_retries_once_then_fails_closed():
    snap = snap_at()
    llm = FakeLLM(["nonsense", json.dumps({**GOOD, "decision": "NO_TRADE", "entry": None, "sl": None, "tp": None, "entry_type": None})])
    dec, raw, err, ms = engine.review_entry(llm, snap, None, None)
    assert dec and dec.decision == "NO_TRADE" and err is None and len(llm.calls) == 2
    llm2 = FakeLLM(["bad", "still bad"])
    dec, raw, err, ms = engine.review_entry(llm2, snap, None, None)
    assert dec is None and "schema" in err
    llm3 = FakeLLM([])
    dec, raw, err, ms = engine.review_entry(llm3, snap, None, None)
    assert dec is None and "llm" in err
    msgs = llm.calls[0][0]
    assert "NO_TRADE" in msgs[0]["content"] and "swing" in msgs[0]["content"].lower()
    payload = msgs[1]["content"].split("all closed):\n", 1)[1]
    assert json.loads(payload)["as_of"] == snap["as_of"]                               # the AI sees exactly the causal snapshot


# ------------------------------------------------------------------ full paper pipeline
def _future_ramp(data, now, price, per_min):
    d = {tf: [dict(c) for c in rows] for tf, rows in data.items()}
    k = 0
    for c in d["1m"]:
        if c["ts"] >= (int(now) // 60 + 1) * 60 - 60 and c["ts"] + 60 > now:
            k += 1
            p = price * (1 + per_min * k)
            c["open"], c["high"], c["low"], c["close"] = p * (1 - per_min), p * 1.0001, p * (1 - per_min) * 0.9999, p
    return d


def _entry_json(snap, side="LONG", **over):
    price = snap["market"]["price"]
    a = snap["timeframes"]["1h"]["atr"]
    sl = price - 1.5 * a if side == "LONG" else price + 1.5 * a
    tp = price + 4.0 * a if side == "LONG" else price - 4.0 * a
    d = {"decision": side, "confidence": 0.72, "entry": price, "entry_type": "MARKET", "sl": sl, "tp": tp, "thesis": "4H trend continuation",
         "invalidation": "loses the 1H swing low", "invalidation_price": price - 1.2 * a if side == "LONG" else price + 1.2 * a,
         "context_timeframe": "4H"}
    d.update(over)
    return json.dumps(d)


def _ticker(price):
    return {"bid": price - 0.5, "ask": price + 0.5, "age_seconds": 1}


def test_full_paper_pipeline_long_to_take_profit(tmpdb):
    cfg = SwingConfig()
    src = ListSource(DATA)
    snap = snap_at()
    price = snap["market"]["price"]
    llm = FakeLLM(lambda msgs, sc: _entry_json(snap) if sc["name"] == "swing_entry_decision" else "{}")
    m = SwingManager(cfg, src, llm)
    o = m.step(NOW, price, _ticker(price))
    assert o["reviewed"] == "ENTRY" and o["decision"]["decision"] == "LONG" and o["decision"]["risk_ok"]
    pos = store.active_position()
    assert pos and pos["status"] == "OPEN" and pos["qty"] > 0
    assert len(store.decisions()) == 1 and store.decisions()[0]["snapshot_id"] and store.get_snapshot(store.decisions()[0]["snapshot_id"])["as_of"] == int(NOW)
    calls = len(llm.calls)
    o2 = m.step(NOW + 15, price, _ticker(price))                                       # same minute: no new AI call
    assert o2["reviewed"] is None and len(llm.calls) == calls
    ramp = _future_ramp(DATA, NOW, price, 0.0006)                                      # price climbs 0.06% a minute
    m.source = ListSource(ramp)
    t = NOW
    for _ in range(90):
        t += 60
        px = [c for c in ramp["1m"] if c["ts"] + 60 <= t][-1]["close"]
        m.step(t, px, _ticker(px))
        if store.active_position() is None:
            break
    closed = store.positions("CLOSED")
    assert len(closed) == 1 and closed[0]["exit_reason"] in ("TP", "AI_EXIT")
    assert closed[0]["mfe_r"] > 0 and closed[0]["r_net"] is not None
    perf = paper.performance(store.positions())
    assert perf["trades"] == 1


def test_no_trade_rejected_and_failed_ai_never_open_positions(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["market"]["price"]
    for script, expect in (
        ([json.dumps({**GOOD, "decision": "NO_TRADE", "entry": None, "sl": None, "tp": None, "entry_type": None, "invalidation_price": None})], "NO_TRADE"),
        ([_entry_json(snap, sl=price + 50, tp=price + 90)], "LONG"),                    # bad geometry -> risk reject
        (["garbage", "garbage"], "NO_TRADE"),                                            # unparseable -> fail closed
    ):
        with db._lock:
            db._connect().executescript("DELETE FROM swing_ai_decisions; DELETE FROM swing_ai_positions; DELETE FROM swing_ai_snapshots;")
        m = SwingManager(cfg, ListSource(DATA), FakeLLM(list(script)))
        o = m.step(NOW, price, _ticker(price))
        assert o["reviewed"] == "ENTRY" and o["decision"]["decision"] == expect
        assert store.active_position() is None
        d = store.decisions()[0]
        assert d["decision"] == expect and d["ts"] == int(NOW) and d["price"] == price
        if script[0].startswith("garbage"):
            assert d["valid"] == 0 and "ai error" in d["risk_reasons"]
        if expect == "LONG":
            assert d["risk_ok"] == 0 and d["risk_reasons"]


def test_management_exit_and_urgent_invalidation(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["market"]["price"]
    a15 = snap["timeframes"]["15m"]["atr"]
    a1h = snap["timeframes"]["1h"]["atr"]

    def script(msgs, sc):
        if sc["name"] == "swing_entry_decision":
            return _entry_json(snap)
        return json.dumps({"thesis_status": "INVALID", "action": "EXIT", "new_sl": None, "confidence": 0.8,
                           "reason": "structure broke", "reversal_candidate": True})
    llm = FakeLLM(script)
    m = SwingManager(cfg, ListSource(DATA), llm)
    m.step(NOW, price, _ticker(price))
    assert store.active_position()["status"] == "OPEN"
    n = len(llm.calls)
    # price drops through the invalidation level (but not the stop): an urgent event wakes management immediately
    px = price - 1.3 * a1h
    o = m.step(NOW + 5, px, _ticker(px))
    assert "TRADE_INVALIDATION" in o["events"] and o["reviewed"] == "MANAGE" and len(llm.calls) == n + 1
    assert o["decision"]["decision"] == "EXIT" and o["decision"].get("reversal_ignored")   # never auto-reverses
    assert store.active_position() is None
    c = store.positions("CLOSED")[0]
    assert c["exit_reason"] == "AI_EXIT" and c["r_net"] < 0
    kinds = [d["kind"] for d in store.decisions()]
    assert kinds.count("MANAGE") == 1 and kinds.count("ENTRY") == 1


def test_heartbeat_and_event_debounce(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["market"]["price"]
    nt = json.dumps({**GOOD, "decision": "NO_TRADE", "entry": None, "sl": None, "tp": None, "entry_type": None, "invalidation_price": None})
    llm = FakeLLM(lambda msgs, sc: nt)
    m = SwingManager(cfg, ListSource(DATA), llm)
    m.step(NOW, price, _ticker(price))
    assert len(llm.calls) == 1
    t = NOW
    for _ in range(10):                                                                # 10 minutes of quiet market
        t += 60
        px = [c for c in DATA["1m"] if c["ts"] + 60 <= t][-1]["close"]
        m.step(t, px, _ticker(px))
    assert len(llm.calls) <= 2                                                         # far fewer calls than minutes (events are rare)
    px = [c for c in DATA["1m"] if c["ts"] + 60 <= NOW + 960][-1]["close"]
    m.step(NOW + 960, px, _ticker(px))                                                 # heartbeat (900s) is due
    assert len(llm.calls) >= 2
    assert m.step(NOW + 1000, None, None)["reviewed"] is None                          # no live price -> paused, no AI call
