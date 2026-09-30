"""Look-ahead audit: did GPT ever see a candle that had not closed yet (or one that later changed)?  Read-only.

  .\\.venv\\Scripts\\python.exe scripts\\swing_ai_audit.py            # last 20 stored snapshots
  .\\.venv\\Scripts\\python.exe scripts\\swing_ai_audit.py --limit 100
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.swing_ai import audit  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
    r = audit.audit_recent(a.limit)
    print(f"snapshots checked: {r['snapshots']}")
    for p in r["detail"][:10]:
        m = p["margin_seconds"]
        print(f"  snapshot #{p['id']} as_of {p['as_of']}: newest candle closed this many seconds before the snapshot -> "
              + ", ".join(f"{tf} {s}s" for tf, s in m.items()) + f" | future {len(p['future'])} revised {len(p['revised'])}")
    print(f"\nFUTURE candles (open time + timeframe later than the snapshot time): {r['future_candles']}")
    print(f"REVISED candles (a 'closed' candle that later changed in the database): {r['revised_candles']}")
    print("RESULT: " + ("no look-ahead found" if not r["future_candles"] and not r["revised_candles"] else "LOOK-AHEAD FOUND - send me this output"))


if __name__ == "__main__":
    main()
