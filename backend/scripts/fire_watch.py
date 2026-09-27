#!/usr/bin/env python3
"""READ-ONLY live watcher: follow the running engine until S1 FIREs and show
whether the order actually reached MEXC.

Run it next to the running app (from the backend folder):

  python scripts/fire_watch.py              # stop after the first FIRE is resolved
  python scripts/fire_watch.py --keep       # keep watching every FIRE

What it reads (never writes, never places or cancels anything):
  * the engine's own status endpoint  http://127.0.0.1:8811/api/autotrade
  * backend/data/live_sizing_log.jsonl (every live order attempt)
  * MEXC open positions via a GET request (same keys as the app)

For each FIRE it prints the chain:
  S1 FIRE -> live order attempt (SUBMITTED / REJECTED + reason)
          -> MEXC position open (avg price, volume) or not
If no order attempt follows a FIRE, it prints what the engine said instead
(e.g. HOLD, COOLDOWN, WEATHER_BLOCK, paper only because live is not armed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
LOG = BACKEND / "data" / "live_sizing_log.jsonl"


def now():
    return datetime.now().strftime("%H:%M:%S")


def say(msg):
    print(f"[{now()}] {msg}", flush=True)


def load_env():
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / "mib-trader"
    for p in (base / ".env", BACKEND / ".env"):
        if not p.is_file():
            continue
        try:
            from dotenv import load_dotenv
            load_dotenv(p, override=False)
        except ImportError:
            for line in p.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def engine_status(url):
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def mexc_positions(symbol):
    """GET-only; returns list or raises."""
    from src.market_data import mexc_private
    return mexc_private.get_open_positions(symbol) or []


def new_log_lines(pos):
    if not LOG.is_file():
        return [], pos
    size = LOG.stat().st_size
    if size < pos:  # file rotated / truncated
        pos = 0
    with open(LOG, encoding="utf-8") as fh:
        fh.seek(pos)
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        pos = fh.tell()
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out, pos


def describe_attempt(r):
    s = r.get("submitted") or {}
    if r.get("result") == "SUBMITTED":
        oid = (r.get("order_result") or {}).get("data")
        return (f"SUBMITTED to MEXC  order {oid}  price {s.get('price')}  SL {s.get('stop_loss_price')}  "
                f"TP {s.get('take_profit_price')}  vol {s.get('vol')}  notional {r.get('final_notional')}")
    return f"REJECTED before MEXC: {r.get('rejection_reason')}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=f"http://127.0.0.1:{os.environ.get('MIB_PORT', '8811')}/api/autotrade")
    ap.add_argument("--every", type=float, default=5.0, help="poll seconds")
    ap.add_argument("--keep", action="store_true", help="keep watching after the first FIRE")
    ap.add_argument("--symbol", default="BTC_USDT")
    a = ap.parse_args()

    load_env()
    try:
        st = engine_status(a.url)
    except Exception as e:
        sys.exit(f"Cannot reach the engine at {a.url}: {e}\nIs the app / trading engine running?")
    cfg, state = st.get("config") or {}, st.get("state") or {}
    say(f"engine: enabled={cfg.get('enabled')} mode={cfg.get('mode')} engine={cfg.get('entry_engine')} "
        f"live_armed={st.get('live_armed')} keys={st.get('keys_present')} "
        f"SL x{cfg.get('sl_atr_mult')} TP x{cfg.get('tp_atr_mult')}")
    if not cfg.get("enabled"):
        say("WARNING: autotrade is DISABLED -- nothing will fire.")
    if cfg.get("mode") != "LIVE" or not st.get("live_armed"):
        say("WARNING: not LIVE + armed -- a FIRE would only be a PAPER trade, no MEXC order.")
    if st.get("open_auto_trade"):
        t = st["open_auto_trade"]
        say(f"NOTE: a position is already open ({t.get('side')} @ {t.get('entry_price')}) -- engine HOLDs until it closes.")
    try:
        pos = mexc_positions(a.symbol)
        say(f"MEXC open positions now: {len(pos)}" + (f"  ({pos[0].get('positionType')} vol {pos[0].get('holdVol')})" if pos else ""))
    except Exception as e:
        say(f"MEXC position check unavailable ({e}); will rely on the order log.")

    log_pos = LOG.stat().st_size if LOG.is_file() else 0
    last_key = None
    pending = None  # FIRE waiting for an order attempt / position
    say("watching... (Ctrl+C to stop)")
    while True:
        try:
            st = engine_status(a.url)
        except Exception as e:
            say(f"engine unreachable: {e}")
            time.sleep(a.every)
            continue
        state = st.get("state") or {}
        s1 = state.get("last_s1") or {}
        key = (s1.get("action"), s1.get("timing"), s1.get("timing_state"), s1.get("direction"),
               state.get("last_action"), state.get("last_reason"))
        if key != last_key:
            say(f"S1 {s1.get('action')} {s1.get('direction') or ''} timing={s1.get('timing')}/{s1.get('timing_state')} "
                f"slot={s1.get('slot')} | engine: {state.get('last_action')} | {str(s1.get('why') or state.get('last_reason') or '')[:90]}")
            last_key = key
        if s1.get("action") == "FIRE" and pending is None:
            pending = {"t": time.time(), "dir": s1.get("direction"), "entry": s1.get("entry"),
                       "sl": s1.get("stop"), "tp": s1.get("target"), "attempt": None}
            say(f">>> FIRE {pending['dir']}  entry {pending['entry']}  SL {pending['sl']}  TP {pending['tp']}")

        lines, log_pos = new_log_lines(log_pos)
        for r in lines:
            say(f">>> order attempt: {describe_attempt(r)}")
            if pending is not None:
                pending["attempt"] = r

        if pending is not None:
            done = False
            att = pending["attempt"]
            if att and att.get("result") == "SUBMITTED":
                try:
                    pos = mexc_positions(a.symbol)
                except Exception as e:
                    pos = None
                    say(f"MEXC position check failed: {e}")
                if pos:
                    p = pos[0]
                    say(f"=== RESULT: FIRE reached MEXC. Position open: type {p.get('positionType')} "
                        f"avg {p.get('holdAvgPrice') or p.get('openAvgPrice')} vol {p.get('holdVol')}")
                    done = True
                elif time.time() - pending["t"] > 60:
                    say("=== RESULT: order SUBMITTED but no MEXC position visible after 60s "
                        "(filled and already closed, or not filled) -- check MEXC order history.")
                    done = True
            elif att and att.get("result") == "REJECTED":
                say(f"=== RESULT: FIRE did NOT reach MEXC -- rejected: {att.get('rejection_reason')}")
                done = True
            elif time.time() - pending["t"] > 30:
                say(f"=== RESULT: FIRE but no live order attempt within 30s. Engine said: "
                    f"{state.get('last_action')} / {state.get('last_reason')}")
                done = True
            if done:
                if not a.keep:
                    return
                pending = None
        time.sleep(a.every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
