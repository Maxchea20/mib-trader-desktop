"""One Swing AI review on the latest local data.  Nothing is stored, nothing is traded.

  set OPENAI_API_KEY=...       (Windows: set / PowerShell: $env:OPENAI_API_KEY="...")
  python scripts/swing_ai_dryrun.py [--db path] [--no-call]
Prints the snapshot size (a rough token estimate), the AI's structured answer, and the risk layer's verdict."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.swing_ai import engine, market_state, risk  # noqa: E402
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
    snap = market_state.build_snapshot(src, now, price, {"bid": price, "ask": price, "age_seconds": 0})
    if snap is None:
        print("not enough closed candle history for 4h/1h/15m/5m/1m")
        return
    text = json.dumps(snap, separators=(",", ":"))
    print(f"snapshot as of {int(now)}: {len(text)} chars (~{len(text) // 4} tokens), price {price}")
    print(json.dumps(snap["derived"], indent=1)[:1500])
    if a.no_call:
        return
    dec, raw, err, ms = engine.review_entry(OpenAILLM(cfg.model, cfg.llm_timeout_seconds), snap, None, None)
    print(f"\nmodel {cfg.model}: {ms} ms" + (f"   ERROR: {err}" if err else ""))
    if dec:
        print(json.dumps(dec.to_dict(), indent=1))
        rr = risk.validate_entry(dec, snap, cfg, {"now": now})
        print("risk layer:", "ACCEPTED " + json.dumps(rr.plan) if rr.ok else "REJECTED " + "; ".join(rr.reasons))


if __name__ == "__main__":
    main()
