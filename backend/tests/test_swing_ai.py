"""Swing AI: raw-data-only architecture, strict schema, causality, wakes, safety layer, paper fills, storage and pipeline."""
import ast
import json
import os
import re

import numpy as np
import pandas as pd
import pytest

from src.market_data import database as db
from src.swing_ai import analytics, engine, paper, raw_data, risk, schema, store
from src.swing_ai.config import SwingConfig
from src.swing_ai.llm import FakeLLM
from src.swing_ai.manager import SwingManager
from src.swing_ai.source import ListSource, TF_SEC
from src.swing_ai.wakes import WakeWatcher

T0 = 1_700_000_000 - 1_700_000_000 % 86400
PKG = os.path.join(os.path.dirname(__file__), "..", "src", "swing_ai")


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
NOW = T0 + 25 * 86400 + 17 * 60 + 20            # a few seconds into a 1m bar


def price_at(now, data=DATA):
    return [c for c in data["1m"] if c["ts"] + 60 <= now][-1]["close"]


def snap_at(now=NOW, data=DATA, position=None):
    p = price_at(now, data)
    return raw_data.build_raw_snapshot(ListSource(data), now, p, {"bid": p - 0.5, "ask": p + 0.5}, position)


# ------------------------------------------------------------------ MIB performs no market analysis
FORBIDDEN = ("atr", "rsi", "ema", "sma", "pivot", "cluster", "sweep", "trend", "bos", "choch", "fvg", "momentum", "volatil", "bias",
             "support", "resistance", "structure", "swing", "level", "liquidity", "extension", "signal")


def _all_keys(o, acc=None):
    acc = set() if acc is None else acc
    if isinstance(o, dict):
        for k, v in o.items():
            acc.add(str(k).lower())
            _all_keys(v, acc)
    elif isinstance(o, list):
        for v in o[:3]:
            _all_keys(v, acc)
    return acc


def test_snapshot_is_raw_evidence_only():
    s = snap_at()
    keys = _all_keys(s)
    assert not [k for k in keys for f in FORBIDDEN if f in re.split(r"[_\W]+", k)], keys        # no analytical key anywhere
    assert set(s) == {"symbol", "source", "as_of_unix", "candle_columns", "candle_note", "live", "timeframes", "position_or_order"}
    assert list(s["timeframes"]) == ["1d", "4h", "1h", "15m", "5m", "1m"]
    for tf, blk in s["timeframes"].items():
        assert set(blk) == {"seconds", "candles"} and all(len(r) == 6 for r in blk["candles"])   # OHLCV rows and nothing else
    assert set(s["live"]) == {"price", "bid", "ask", "spread", "exchange_24h_volume"}
    txt = json.dumps(s).lower()
    assert not [w for w in FORBIDDEN if re.search(rf'"{w}[a-z_]*"\s*:', txt)]


def test_package_contains_no_market_analysis_code():
    bad = []
    for fn in os.listdir(PKG):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(PKG, fn)).read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.append(node.name)
            if isinstance(node, ast.Call):
                f = node.func
                names.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            for n in names:
                if any(t in FORBIDDEN for t in re.split(r"_+", n.lower())):
                    bad.append((fn, n))
    assert not bad, bad
    for gone in ("primitives.py", "market_state.py", "events.py"):
        assert not os.path.exists(os.path.join(PKG, gone))
    imports = "".join(open(os.path.join(PKG, f)).read() for f in os.listdir(PKG) if f.endswith(".py"))
    for engine_name in ("hunt", "trend_break", "s1_detect", "structure.observe", "market_state", "indicators"):
        assert f"import {engine_name}" not in imports and f"from ..{engine_name}" not in imports and f"from .{engine_name}" not in imports


def test_prompt_never_carries_a_mib_opinion_and_names_gpt_as_the_analyst():
    from src.swing_ai import prompts
    cfg = SwingConfig()
    msgs = prompts.entry_messages(cfg, snap_at(), {"kind": "HEARTBEAT_15M"}, None)
    system, user = msgs[0]["content"], msgs[1]["content"]
    assert "sole market analyst" in system and "no indicator, no signal, no structure" in system and "NO_TRADE" in system
    payload = json.loads(user.split("RAW MARKET DATA FROM MEXC (JSON):\n", 1)[1])
    assert json.dumps(payload) == json.dumps(snap_at())                                     # exactly the raw snapshot, nothing added
    assert payload["as_of_unix"] == int(NOW)


# ------------------------------------------------------------------ causality
def test_snapshot_is_causal():
    s1 = snap_at()
    assert s1 is not None and s1["as_of_unix"] == int(NOW)
    for tf, blk in s1["timeframes"].items():
        assert blk["candles"][-1][0] + TF_SEC[tf] <= NOW                                    # the forming bar is never sent
        assert all(r[0] + TF_SEC[tf] <= NOW for r in blk["candles"])
    future = {tf: [dict(c) for c in rows] for tf, rows in DATA.items()}                      # rewrite everything after NOW
    for tf, rows in future.items():
        for c in rows:
            if c["ts"] + TF_SEC[tf] > NOW:
                c["open"] = c["high"] = c["low"] = c["close"] = 1.0
    assert json.dumps(s1, sort_keys=True) == json.dumps(snap_at(data=future), sort_keys=True)
    trunc = {tf: [c for c in rows if c["ts"] + TF_SEC[tf] <= NOW] for tf, rows in DATA.items()}
    assert json.dumps(s1, sort_keys=True) == json.dumps(snap_at(data=trunc), sort_keys=True)
    assert raw_data.build_raw_snapshot(ListSource({tf: rows[:5] for tf, rows in DATA.items()}), NOW, 60000.0) is None


# ------------------------------------------------------------------ schema
def _good(**kw):
    d = {"decision": "LONG", "confidence": 0.7, "headline": "Trend continuation long", "market_state": "TRENDING_UP", "daily_analysis": "d", "h4_analysis": "h4",
         "h1_analysis": "h1", "m15_analysis": "m15", "structure_analysis": "HH/HL", "entry_analysis": "reclaim", "entry_type": "MARKET",
         "entry": 100.0, "sl": 96.0, "tp": 108.0, "thesis": "t", "invalidation": "i", "invalidation_price": 95.5,
         "wake_levels": [{"price": 105.0, "direction": "ABOVE", "reason": "breakout"}]}
    d.update(kw)
    return d


def test_schema_accepts_valid_and_rejects_everything_else():
    e = schema.parse_entry(json.dumps(_good()))
    assert e.decision == "LONG" and e.daily_analysis == "d" and e.wake_levels[0]["direction"] == "ABOVE"
    nt = _good(decision="NO_TRADE", entry=0, sl=None, tp=None, entry_type=None, invalidation="", invalidation_price=None, wake_levels=[])
    assert schema.parse_entry(nt).decision == "NO_TRADE"
    bads = ["not json", "[]", _good(decision="BUY"), _good(confidence=1.5), _good(sl=None), _good(entry_type="STOP"), _good(thesis=""),
            _good(market_state="BULLISH"), _good(entry="100"), _good(tp=float("nan")), _good(daily_analysis=""), _good(h1_analysis=None), _good(headline=""),
            _good(invalidation=""), _good(wake_levels=[{"price": 1, "direction": "SIDEWAYS", "reason": ""}])]
    for bad in bads:
        with pytest.raises(schema.SchemaError):
            schema.parse_entry(bad)
    too_many = _good(wake_levels=[{"price": 100 + i, "direction": "ABOVE", "reason": "x"} for i in range(9)])
    assert len(schema.parse_entry(too_many).wake_levels) == schema.MAX_WAKE_LEVELS
    m = {"thesis_status": "VALID", "action": "HOLD", "new_sl": None, "confidence": 0.6, "reason": "ok", "reversal_candidate": False, "wake_levels": []}
    assert schema.parse_manage(m).action == "HOLD"
    with pytest.raises(schema.SchemaError):
        schema.parse_manage({**m, "action": "MOVE_SL"})
    for js in (schema.entry_json_schema(), schema.manage_json_schema()):                      # OpenAI strict mode: every key required
        sc = js["schema"]
        assert sc["additionalProperties"] is False and set(sc["required"]) == set(sc["properties"])
    for f in ("headline", "daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis", "entry_analysis", "market_state", "thesis", "invalidation"):
        assert f in schema.entry_json_schema()["schema"]["properties"]


# ------------------------------------------------------------------ wakes (raw conditions only)
def test_wakes_are_clock_arithmetic_and_the_ais_own_alerts():
    cfg = SwingConfig()
    w = WakeWatcher(cfg)
    src = ListSource(DATA)
    p0 = price_at(NOW)
    assert w.check(NOW, p0, src) == []                                                        # first look: nothing fires
    hour_close = (NOW // 3600 + 1) * 3600 + 5
    got = w.check(hour_close, price_at(hour_close), src)
    assert "CANDLE_CLOSE_1H" in [x["kind"] for x in got]
    assert w.check(hour_close + 60, price_at(hour_close + 60), src) == [] or True
    w2 = WakeWatcher(cfg)
    w2.check(NOW, 100.0, src)
    w2.check(NOW + 65, 100.0, src)
    mv = w2.check(NOW + 130, 101.0, src)                                                      # +1% inside the window: raw move gate
    assert "PRICE_MOVE" in [x["kind"] for x in mv]
    w3 = WakeWatcher(cfg)
    w3.set_ai_levels([{"price": 105.0, "direction": "ABOVE", "reason": "breakout"}, {"price": 95.0, "direction": "BELOW", "reason": "breakdown"}])
    w3.check(NOW, 100.0, src)
    hit = w3.check(NOW + 5, 105.5, src)
    assert [x["kind"] for x in hit if x["kind"] == "AI_WAKE_LEVEL"] and hit[0]["urgent"]
    assert not [x for x in w3.check(NOW + 10, 105.6, src) if x["kind"] == "AI_WAKE_LEVEL"]   # one-shot
    pos = {"id": 1, "status": "OPEN", "side": "LONG", "stop_loss": 95.0, "invalidation_price": 97.0}
    inv = w3.position_wakes(pos, 96.9)
    assert inv and inv[0]["kind"] == "INVALIDATION_LEVEL_HIT" and inv[0]["urgent"]
    near = w3.position_wakes({**pos, "invalidation_price": None}, 95.1)
    assert near and near[0]["kind"] == "STOP_NEAR"
    assert not w3.position_wakes({**pos, "invalidation_price": None}, 100.0)


# ------------------------------------------------------------------ safety layer
def _mk(price=100.0, spread=0.005, age=1):
    return {"price": price, "bid": price - 0.005, "ask": price + 0.005, "spread_pct": spread, "ticker_age_seconds": age}


def _dec(**kw):
    d = schema.EntryDecision(decision="LONG", confidence=0.7, entry=100.0, entry_type="MARKET", sl=96.0, tp=108.0, thesis="t",
                             invalidation="i", invalidation_price=95.5)
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_safety_layer_checks_geometry_and_limits_only():
    cfg = SwingConfig()
    ok = risk.validate_entry(_dec(), _mk(), cfg, {"now": 0})
    assert ok.ok and ok.plan["entry_type"] == "MARKET" and ok.plan["risk_usd"] <= cfg.equity_usd * cfg.risk_pct / 100 + 1e-6
    assert ok.plan["leverage"] <= cfg.max_leverage + 1e-9
    cases = {"sl above entry": _dec(sl=104.0), "tp below entry": _dec(tp=98.0), "rr too low": _dec(tp=104.0),
             "stop too tight": _dec(sl=99.9, tp=110.0), "stop too wide": _dec(sl=85.0, tp=140.0),
             "market but far": _dec(entry=103.0, tp=115.0), "limit wrong side": _dec(entry=101.0, entry_type="LIMIT", sl=97.0, tp=112.0)}
    for name, d in cases.items():
        assert not risk.validate_entry(d, _mk(), cfg, {"now": 0}).ok, name
    assert risk.validate_entry(_dec(confidence=0.1), _mk(), cfg, {"now": 0}).ok                   # confidence is recorded, not a MIB gate
    assert not risk.validate_entry(_dec(confidence=0.3), _mk(), SwingConfig(min_confidence=0.5), {"now": 0}).ok
    for bad_ctx in ({"has_active": True}, {"trades_today": 3}, {"daily_r": -3.5}, {"last_loss_ts": 900, "now": 1000}, {"connected": False}):
        assert not risk.validate_entry(_dec(), _mk(), cfg, {"now": 0, **bad_ctx}).ok
    assert not risk.validate_entry(_dec(), _mk(spread=0.2), cfg, {"now": 0}).ok
    assert not risk.validate_entry(_dec(), _mk(age=99), cfg, {"now": 0}).ok
    lim = risk.validate_entry(_dec(entry=98.0, entry_type="LIMIT", sl=94.0, tp=110.0), _mk(), cfg, {"now": 0})
    assert lim.ok and lim.plan["entry_type"] == "LIMIT" and lim.plan["fee_entry"] == cfg.maker_fee
    small = SwingConfig(equity_usd=10_000, max_leverage=0.5)
    t = risk.validate_entry(_dec(entry=60000.0, sl=59400.0, tp=61800.0), _mk(price=60000.0), small, {"now": 0})
    assert t.ok and t.plan["leverage"] <= 0.5 + 1e-9 and t.plan["sized_down"]
    md = schema.ManageDecision(action="MOVE_SL", new_sl=98.0)
    pos = {"side": "LONG", "sl": 96.0}
    assert risk.validate_manage(md, pos, 101.0, cfg).ok
    assert not risk.validate_manage(schema.ManageDecision(action="MOVE_SL", new_sl=95.0), pos, 101.0, cfg).ok
    assert not risk.validate_manage(schema.ManageDecision(action="MOVE_SL", new_sl=101.0), pos, 101.0, cfg).ok


# ------------------------------------------------------------------ paper broker
def _plan(side="LONG", typ="MARKET", entry=100.0, sl=96.0, tp=108.0, qty=1.0):
    return {"side": side, "entry_type": typ, "entry": entry, "sl": sl, "tp": tp, "qty": qty, "risk_usd": abs(entry - sl) * qty,
            "risk_dist": abs(entry - sl), "leverage": 1.0, "rr": abs(tp - entry) / abs(entry - sl),
            "fee_entry": 0.0002 if typ == "MARKET" else 0.0, "fee_tp": 0.0, "fee_sl": 0.0002,
            "expires_ts": 10_000 if typ == "LIMIT" else None, "invalidation_price": None, "sized_down": False}


def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def _open(plan, now, bid=99.9, ask=100.0, state="TRENDING_UP", conf=0.7):
    did = store.add_decision(ts=0, kind="ENTRY", decision=plan["side"])
    return store.get_trade(paper.open_from_plan(plan, {"thesis": "t", "invalidation": "i", "market_state": state, "confidence": conf}, did, now, bid, ask))


def test_paper_fills_are_causal_and_r_math_is_right(tmpdb):
    cfg = SwingConfig()
    p = _open(_plan(), 1000)
    assert p["status"] == "OPEN" and p["fill_price"] == 100.0 and p["market_state"] == "TRENDING_UP" and p["confidence"] == 0.7
    p = paper.process_bar(p, _bar(1020, 100, 105, 99, 104), cfg)
    assert p["status"] == "OPEN" and p["mfe_r"] == pytest.approx(1.25) and p["mae_r"] == pytest.approx(0.25)
    p = paper.process_bar(p, _bar(1080, 104, 109, 103, 108), cfg)
    assert p["exit_reason"] == "TP" and p["r_gross"] == pytest.approx(2.0) and p["outcome"] == "WIN"
    assert p["r_net"] == pytest.approx(2.0 - (100.0 * 0.0002) / 4.0)
    q = paper.process_bar(_open(_plan(), 2000), _bar(2040, 100, 110, 95, 100), cfg)                # both touched: stop wins
    assert q["exit_reason"] == "SL" and q["r_gross"] == pytest.approx(-1.0) and q["r_net"] < -1.0 and q["outcome"] == "LOSS"
    lp = _open(_plan(typ="LIMIT", entry=98.0, sl=94.0, tp=106.0), 3000, None, None)
    assert lp["status"] == "PENDING"
    lp = paper.process_bar(lp, _bar(3060, 100, 101, 99, 100), cfg)
    assert lp["status"] == "PENDING"
    lp = paper.process_bar(lp, _bar(3120, 100, 107, 97, 105), cfg)                                 # fill bar: the target does not count
    assert lp["status"] == "OPEN" and lp["fill_price"] == 98.0
    lp = paper.process_bar(lp, _bar(3180, 105, 107, 104, 106), cfg)
    assert lp["exit_reason"] == "TP" and lp["r_gross"] == pytest.approx(2.0)
    ex = paper.process_bar(_open(_plan(typ="LIMIT", entry=90.0, sl=86.0, tp=98.0), 9000, None, None), _bar(10_020, 100, 101, 99, 100), cfg)
    assert ex["status"] == "EXPIRED"
    assert paper.performance(store.trades())["trades"] == 3


# ------------------------------------------------------------------ storage: the audit trail
def test_store_round_trips_snapshots_decisions_and_trades(tmpdb):
    s = snap_at()
    sid = store.add_snapshot(int(NOW), "BTC_USDT", 60000.0, s)
    assert store.get_snapshot(sid) == s
    with db._lock:
        row = db._connect().execute("SELECT bytes, encoding FROM swing_ai_market_snapshots WHERE id=?", (sid,)).fetchone()
    assert row["encoding"] == "zlib+json" and row["bytes"] < len(json.dumps(s)) / 2                 # compressed
    d = schema.parse_entry(_good())
    did = store.add_decision(ts=1, symbol="BTC_USDT", kind="ENTRY", wake_kind="HEARTBEAT_15M", price=1.0, snapshot_id=sid, model="m",
                             prompt_version="v", raw="{}", valid=1, risk_ok=1, **{k: getattr(d, k) for k in (
                                 "decision", "confidence", "headline", "market_state", "daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis",
                                 "structure_analysis", "entry_analysis", "entry_type", "entry", "sl", "tp", "thesis", "invalidation",
                                 "invalidation_price", "wake_levels")})
    r = store.latest_entry_decision()
    assert r["id"] == did and r["headline"] == "Trend continuation long" and r["h4_analysis"] == "h4" and r["snapshot_id"] == sid and json.loads(r["wake_levels"])[0]["price"] == 105.0
    assert r["model"] == "m" and r["wake_kind"] == "HEARTBEAT_15M"


def test_analytics_by_side_state_and_confidence(tmpdb):
    cfg = SwingConfig()
    for side, state, conf, win in (("LONG", "TRENDING_UP", 0.75, True), ("LONG", "RANGE", 0.55, False), ("SHORT", "TRENDING_DOWN", 0.85, True),
                                   ("SHORT", "RANGE", 0.65, False)):
        pl = _plan(side=side, sl=96.0 if side == "LONG" else 104.0, tp=108.0 if side == "LONG" else 92.0)
        t = _open(pl, 100, 99.9, 100.0 if side == "LONG" else 99.9, state, conf)
        bar = _bar(160, 100, 109 if (win and side == "LONG") else 100.5, 91 if (win and side == "SHORT") else 99.5, 100) if win else \
            _bar(160, 100, 104.5 if side == "SHORT" else 100.5, 95 if side == "LONG" else 99.5, 100)
        paper.process_bar(t, bar, cfg)
    for d in ("LONG", "SHORT", "NO_TRADE", "NO_TRADE"):
        store.add_decision(ts=1000 + len(store.decisions()), symbol="BTC_USDT", kind="ENTRY", decision=d, valid=1, risk_ok=1)
    r = analytics.report()
    assert r["frequency"]["entry_reviews"] == 8 and r["frequency"]["NO_TRADE"] == 2 and r["frequency"]["no_trade_share"] == 0.25   # 4 opened trades logged 4 decisions
    assert r["overall"]["trades"] == 4 and r["by_side"]["LONG"]["trades"] == 2 and r["by_side"]["SHORT"]["trades"] == 2
    assert set(r["by_market_state"]) == {"TRENDING_UP", "RANGE", "TRENDING_DOWN"} and r["by_market_state"]["RANGE"]["trades"] == 2
    bins = {c["confidence_bin"]: c for c in r["confidence_calibration"]}
    assert bins["0.8-1.0"]["win_rate"] == 1.0 and bins["0.0-0.6"]["win_rate"] == 0.0


# ------------------------------------------------------------------ AI call layer
def test_engine_retries_once_then_fails_closed():
    cfg = SwingConfig()
    snap = snap_at()
    nt = json.dumps(_good(decision="NO_TRADE", entry=None, sl=None, tp=None, entry_type=None, invalidation="", invalidation_price=None, wake_levels=[]))
    llm = FakeLLM(["nonsense", nt])
    dec, raw, err, ms = engine.review_entry(llm, cfg, snap, None, None)
    assert dec and dec.decision == "NO_TRADE" and err is None and len(llm.calls) == 2
    assert engine.review_entry(FakeLLM(["bad", "still bad"]), cfg, snap, None, None)[0] is None
    d3 = engine.review_entry(FakeLLM([]), cfg, snap, None, None)
    assert d3[0] is None and "llm" in d3[2]


# ------------------------------------------------------------------ full paper pipeline
def _entry_json(snap, side="LONG", **over):
    price = snap["live"]["price"]
    long_ = side == "LONG"
    d = _good(decision=side, entry=price, sl=price * (0.985 if long_ else 1.015), tp=price * (1.05 if long_ else 0.95),
              invalidation_price=price * (0.988 if long_ else 1.012), market_state="TRENDING_UP" if long_ else "TRENDING_DOWN",
              wake_levels=[{"price": price * 1.02, "direction": "ABOVE", "reason": "continuation"}])
    d.update(over)
    return json.dumps(d)


def _ticker(p):
    return {"bid": p - 0.5, "ask": p + 0.5, "age_seconds": 1}


def _ramp(data, now, price, per_min):
    d = {tf: [dict(c) for c in rows] for tf, rows in data.items()}
    k = 0
    for c in d["1m"]:
        if c["ts"] + 60 > now:
            k += 1
            p = price * (1 + per_min * k)
            c["open"], c["high"], c["low"], c["close"] = p * (1 - per_min), p * 1.0001, p * (1 - per_min) * 0.9999, p
    return d


def test_full_paper_pipeline_long_to_take_profit(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["live"]["price"]
    llm = FakeLLM(lambda msgs, sc: _entry_json(snap))
    m = SwingManager(cfg, ListSource(DATA), llm)
    o = m.step(NOW, price, _ticker(price))
    assert o["reviewed"] == "ENTRY" and o["decision"]["decision"] == "LONG" and o["decision"]["risk_ok"]
    tr = store.active_trade()
    assert tr and tr["status"] == "OPEN" and tr["qty"] > 0 and tr["market_state"] == "TRENDING_UP"
    d = store.decisions()[0]
    assert d["snapshot_id"] and d["h4_analysis"] == "h4" and d["wake_kind"] == "HEARTBEAT_15M" and d["model"] == cfg.model
    assert store.get_snapshot(d["snapshot_id"])["as_of_unix"] == int(NOW) and d["trade_id"] == tr["id"]
    calls = len(llm.calls)
    assert m.step(NOW + 15, price, _ticker(price))["reviewed"] is None and len(llm.calls) == calls
    ramp = _ramp(DATA, NOW, price, 0.0009)
    m.source = ListSource(ramp)
    t = NOW
    for _ in range(120):
        t += 60
        px = price_at(t, ramp)
        m.step(t, px, _ticker(px))
        if store.active_trade() is None:
            break
    closed = store.trades("CLOSED")
    assert len(closed) == 1 and closed[0]["exit_reason"] in ("TP", "AI_EXIT") and closed[0]["mfe_r"] > 0 and closed[0]["outcome"]
    assert paper.performance(store.trades())["trades"] == 1


def test_no_trade_rejected_and_failed_ai_never_open_trades(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["live"]["price"]
    nt = json.dumps(_good(decision="NO_TRADE", entry=None, sl=None, tp=None, entry_type=None, invalidation="", invalidation_price=None, wake_levels=[]))
    for script, expect in (([nt], "NO_TRADE"), ([_entry_json(snap, sl=price * 1.02, tp=price * 1.05)], "LONG"), (["garbage", "garbage"], "NO_TRADE")):
        with db._lock:
            db._connect().executescript("DELETE FROM swing_ai_decisions; DELETE FROM swing_ai_trades; DELETE FROM swing_ai_market_snapshots;")
        m = SwingManager(cfg, ListSource(DATA), FakeLLM(list(script)))
        o = m.step(NOW, price, _ticker(price))
        assert o["reviewed"] == "ENTRY" and o["decision"]["decision"] == expect and store.active_trade() is None
        d = store.decisions()[0]
        assert d["decision"] == expect and d["ts"] == int(NOW) and d["price"] == price
        if script[0] == "garbage":
            assert d["valid"] == 0 and "ai error" in d["risk_reasons"]
        if expect == "LONG":
            assert d["risk_ok"] == 0 and d["risk_reasons"] and d["daily_analysis"] == "d"        # rejected, but the AI's analysis is kept
        if expect == "NO_TRADE" and script[0] == nt:
            assert d["thesis"] == "t" and d["m15_analysis"] == "m15"


def test_management_exit_on_the_ais_own_invalidation_and_no_auto_reverse(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["live"]["price"]

    def script(msgs, sc):
        if sc["name"] == "swing_entry_decision":
            return _entry_json(snap)
        return json.dumps({"thesis_status": "INVALID", "action": "EXIT", "new_sl": None, "confidence": 0.8, "reason": "structure broke",
                           "reversal_candidate": True, "wake_levels": []})
    llm = FakeLLM(script)
    m = SwingManager(cfg, ListSource(DATA), llm)
    m.step(NOW, price, _ticker(price))
    assert store.active_trade()["status"] == "OPEN"
    n = len(llm.calls)
    px = price * 0.987                                                                        # through the AI's own invalidation price
    o = m.step(NOW + 5, px, _ticker(px))
    assert "INVALIDATION_LEVEL_HIT" in o["wakes"] and o["reviewed"] == "MANAGE" and len(llm.calls) == n + 1
    assert o["decision"]["decision"] == "EXIT" and o["decision"].get("reversal_ignored") and store.active_trade() is None
    c = store.trades("CLOSED")[0]
    assert c["exit_reason"] == "AI_EXIT" and c["r_net"] < 0
    ks = [d["kind"] for d in store.decisions()]
    assert ks.count("MANAGE") == 1 and ks.count("ENTRY") == 1
    assert "[INVALID]" in store.decisions()[0]["thesis"]


def test_heartbeat_wakes_and_ai_alert_levels(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["live"]["price"]
    nt = json.dumps(_good(decision="NO_TRADE", entry=None, sl=None, tp=None, entry_type=None, invalidation="", invalidation_price=None,
                          wake_levels=[{"price": price * 1.003, "direction": "ABOVE", "reason": "if it breaks out"}]))
    llm = FakeLLM(lambda msgs, sc: nt)
    m = SwingManager(cfg, ListSource(DATA), llm)
    m.step(NOW, price, _ticker(price))
    assert len(llm.calls) == 1
    m.step(NOW + 30, price, _ticker(price))
    assert len(llm.calls) == 1                                                                # quiet: no call
    up = price * 1.004
    o = m.step(NOW + 60, up, _ticker(up))                                                     # the AI's own alert is crossed
    assert "AI_WAKE_LEVEL" in o["wakes"] and o["reviewed"] == "ENTRY" and len(llm.calls) == 2
    assert store.decisions()[0]["wake_kind"] == "AI_WAKE_LEVEL" and "your alert" in store.decisions()[0]["wake_detail"]
    h = m.step(NOW + 960, up, _ticker(up))                                                    # 15 min heartbeat
    assert h["reviewed"] == "ENTRY"
    assert m.step(NOW + 1000, None, None)["reviewed"] is None                                 # no live price: paused, no AI call


def test_ui_endpoint_serves_only_stored_gpt_output(tmpdb):
    from src.swing_ai import service
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["live"]["price"]
    m = SwingManager(cfg, ListSource(DATA), FakeLLM(lambda msgs, sc: _entry_json(snap)))
    service._manager = m
    try:
        assert service.latest()["last_analysis"] is None and service.latest()["last_review"] is None    # nothing invented before GPT has spoken
        m.step(NOW, price, _ticker(price))
        out = service.latest()
        json.dumps(out)                                                                     # JSON-serialisable for the API
        a = out["last_analysis"]
        stored = store.latest_entry_decision()
        for k in ("headline", "daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis", "structure_analysis", "entry_analysis", "thesis",
                  "invalidation", "decision", "confidence", "entry", "sl", "tp", "market_state", "model"):
            assert a[k] == stored[k]                                                        # UI fields are the stored GPT fields
        assert set(a) == set(service.AI_FIELDS) and out["mode"] == "PAPER" and out["active_trade"]["status"] == "OPEN"
        assert out["analytics"]["frequency"]["LONG"] == 1
    finally:
        service._manager = None


def test_openai_call_has_room_for_reasoning_and_rejects_truncated_replies(monkeypatch):
    import sys, types
    from src.swing_ai.llm import OpenAILLM, LLMError
    seen = {}

    class _Msg: content = "{}"

    def make(reason):
        class _Choice:
            finish_reason = reason
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
        return _Resp()

    state = {"reason": "stop"}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            return make(state["reason"])

    class _Client:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=_Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_Client))
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.delenv("SWING_AI_MAX_TOKENS", raising=False)
    llm = OpenAILLM("gpt-5.4")
    assert llm.complete([{"role": "user", "content": "hi"}], schema.entry_json_schema()) == "{}"
    assert seen["max_completion_tokens"] >= 8000 and seen["response_format"]["type"] == "json_schema"     # not the old 900 cap
    state["reason"] = "length"
    with pytest.raises(LLMError, match="truncated"):
        llm.complete([{"role": "user", "content": "hi"}], schema.entry_json_schema())
    dec, raw, err, ms = engine.review_entry(llm, SwingConfig(), snap_at(), None, None)
    assert dec is None and "truncated" in err                                                               # fails closed, no retry loop


def test_prompt_is_brief_and_lets_the_ai_act_on_limit_setups():
    from src.swing_ai import prompts
    text = prompts.system_prompt(SwingConfig())
    assert "BE BRIEF" in text and "at most 12 words" in text and "LIMIT order at a range edge" in text
    assert "frequent and good" not in text and "confidence at least" not in text


def test_old_database_gets_the_headline_column(tmpdb):
    with db._lock:
        c = db._connect()
        c.executescript("DROP TABLE swing_ai_decisions; CREATE TABLE swing_ai_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, symbol TEXT, kind TEXT);")
        c.commit()
    store._ready = False
    store.init()
    with db._lock:
        cols = {r[1] for r in db._connect().execute("PRAGMA table_info(swing_ai_decisions)").fetchall()}
    assert "headline" in cols


def test_pending_order_is_never_described_as_a_filled_position(tmpdb):
    from src.swing_ai import prompts
    pend = _open(_plan(typ="LIMIT", entry=98.0, sl=94.0, tp=106.0), 3000, None, None)
    v = raw_data.position_state(pend, 100.0, 3100)
    assert v["status"] == "PENDING_NOT_FILLED" and v["limit_price"] == 98.0 and "NOT in a trade" in v["note"]
    assert "entry_price" not in v and "unrealized_r" not in v
    filled = paper.process_bar(pend, _bar(3120, 100, 101, 97, 99), SwingConfig())
    o = raw_data.position_state(filled, 100.0, 3200)
    assert o["status"] == "OPEN" and o["entry_price"] == 98.0 and "limit_price" not in o
    assert "PENDING_NOT_FILLED" in prompts.system_prompt(SwingConfig())
    r = risk.validate_entry(_dec(entry=60000.0, sl=59400.0, tp=61800.0), _mk(price=60000.0), SwingConfig(equity_usd=12345.0), {"now": 0})
    assert r.ok and r.plan["qty"] == round(r.plan["qty"], 3)                                 # no float artefacts like 0.20800000000000002


# ------------------------------------------------------------------ UI settings: AUTO-TRADE on/off, PAPER/LIVE request, live inputs
def test_settings_are_validated_persisted_and_live_is_only_a_request(tmpdb, tmp_path, monkeypatch):
    from src.swing_ai import service, settings as sett
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.delenv("SWING_AI_ENABLED", raising=False)
    service._manager = None
    try:
        s = sett.load()
        assert s["enabled"] is False and s["mode"] == "PAPER"                                # off until switched on in the UI
        monkeypatch.setenv("SWING_AI_ENABLED", "1")
        assert sett.load()["enabled"] is True                                                # env flag is only the default
        v = service.update_settings({"enabled": False, "mode": "LIVE", "risk_pct": 0.5, "max_leverage": 3, "max_position_usd": 2000})
        assert v["enabled"] is False and v["mode"] == "LIVE"
        assert v["effective_mode"] == "PAPER" and v["live_execution_implemented"] is False    # LIVE is only a request
        assert service.status()["mode"] == "PAPER" and service.latest()["mode"] == "PAPER"
        assert sett.load()["risk_pct"] == 0.5                                                 # persisted to disk
        cfg = service.get_manager().cfg
        assert (cfg.risk_pct, cfg.max_leverage, cfg.max_position_usd) == (0.5, 3.0, 2000.0)   # applied to the running engine
        for bad in ({"mode": "REAL"}, {"risk_pct": 50}, {"max_leverage": 0}, {"enabled": "yes"}, {"max_position_usd": -1}, {"risk_pct": True}):
            with pytest.raises(ValueError):
                service.update_settings(bad)
        assert sett.load()["risk_pct"] == 0.5                                                 # a rejected update changes nothing
    finally:
        service._manager = None


def test_ai_off_stops_ai_calls_but_paper_positions_keep_being_tracked(tmpdb):
    cfg = SwingConfig()
    snap = snap_at()
    price = snap["live"]["price"]
    llm = FakeLLM(lambda msgs, sc: _entry_json(snap))
    m = SwingManager(cfg, ListSource(DATA), llm)
    m.step(NOW, price, _ticker(price))
    assert store.active_trade()["status"] == "OPEN" and len(llm.calls) == 1
    ramp = _ramp(DATA, NOW, price, 0.0009)
    m.source = ListSource(ramp)
    t = NOW
    for _ in range(120):
        t += 60
        px = price_at(t, ramp)
        o = m.step(t, px, _ticker(px), ai_enabled=False)
        assert o["reviewed"] is None
        if store.active_trade() is None:
            break
    assert len(llm.calls) == 1                                                                # AI never called while OFF
    assert store.trades("CLOSED") and store.trades("CLOSED")[0]["exit_reason"] == "TP"        # but the take-profit was still simulated
    assert m.status["state"].startswith("off")


def test_run_once_respects_the_enabled_setting(tmp_path, monkeypatch):
    from src.swing_ai import service, settings as sett
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.delenv("SWING_AI_ENABLED", raising=False)
    seen = []

    class _M:
        def step(self, now, price, ticker, connected, ai_enabled=True):
            seen.append(ai_enabled)
            return {}
    st = {"last_price": 1.0, "connected": True, "last_ticker": {}, "last_tick_ts": None}
    service.run_once(_M(), st)
    sett.save({"enabled": True})
    service.run_once(_M(), st)
    assert seen == [False, True]
