"""One Swing AI review on the latest local data.  Nothing is stored, nothing is traded.

  set OPENAI_API_KEY=...       (Windows: set / PowerShell: $env:OPENAI_API_KEY="...")
  python scripts/swing_ai_dryrun.py [--db path] [--no-call]
Prints the raw snapshot size (a rough token estimate), the AI's structured answer, and the safety layer's verdict."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:                                                    # same .env files run_server.py reads
    from dotenv import load_dotenv
    _here = os.path.join(os.path.dirname(__file__), "..")
    load_dotenv(os.path.join(_here, ".env"), override=False)
    _appdata = os.environ.get("APPDATA")
    if _appdata:
        load_dotenv(os.path.join(_appdata, "mib-trader", ".env"), override=False)
except Exception:
    pass
from src.market_data import database as db  # noqa: E402
from src.swing_ai import engine, raw_data, risk  # noqa: E402
from src.swing_ai.config import SwingConfig  # noqa: E402
from src.swing_ai.llm import OpenAILLM  # noqa: E402
from src.swing_ai.source import DbSource  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--no-call", action="store_true", help="only build and size the snapshot")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
    cfg = SwingConfig()
    now = time.time()
    src = DbSource()
    last = src.closed("1m", 1, now)
    if not last:
        print("no closed 1m candles in the database")
        return
    price = float(last[-1]["close"])
    snap = raw_data.build_raw_snapshot(src, now, price, {"bid": price, "ask": price})
    if snap is None:
        print("not enough closed candle history for 1d/4h/1h/15m/5m/1m")
        return
    text = json.dumps(snap, separators=(",", ":"))
    print(f"raw snapshot as of {int(now)}: {len(text)} chars (~{len(text) // 4} tokens); "
          + ", ".join(f"{tf}:{len(v['candles'])}" for tf, v in snap["timeframes"].items()) + f"; price {price}")
    if a.no_call:
        return
    llm = OpenAILLM(cfg.model, cfg.llm_timeout_seconds)
    dec, raw, err, ms = engine.review_entry(llm, cfg, snap, None, None)
    if llm.last_usage:
        u = llm.last_usage
        print(f"\nBILLED TOKENS for this one call: input {u['input']:,} (cached {u['cached']:,}), output {u['output']:,}")
    _k = os.environ.get("OPENAI_API_KEY") or ""
    print(f"\nOPENAI_API_KEY in use ends with ...{_k[-4:]}" if _k else "\nOPENAI_API_KEY: not set")
    print(f"model {cfg.model}: {ms} ms" + (f"   ERROR: {err}" if err else ""))
    if dec:
        print(json.dumps(dec.to_dict(), indent=1))
        mk = {"price": price, "bid": price, "ask": price, "spread_pct": 0.0, "ticker_age_seconds": 0}
        if dec.decision == "NO_TRADE":
            print("safety layer: nothing to validate (the AI chose NO_TRADE)")
        else:
            rr = risk.validate_entry(dec, mk, cfg, {"now": now})
            print("safety layer:", "ACCEPTED " + json.dumps(rr.plan) if rr.ok else "REJECTED " + "; ".join(rr.reasons))


if __name__ == "__main__":
    main()
