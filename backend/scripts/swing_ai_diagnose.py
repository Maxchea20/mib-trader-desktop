"""Find mistakes and inconsistencies in what the Swing AI did.  Read-only.

  python scripts/swing_ai_diagnose.py
  python scripts/swing_ai_diagnose.py --db path/to/market_data_clean.db
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.swing_ai import diagnose  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
    r = diagnose.diagnose()
    lat = "—" if r["median_latency_s"] is None else f"{r['median_latency_s']:.0f}s"
    print(f"{r['decisions']} decisions ({r['entry_reviews']} entry, {r['manage_reviews']} management), {r['proposals']} LONG/SHORT proposals, "
          f"{r['trades']} trades ({r['closed']} closed), median GPT latency {lat}\n")
    if not r["findings"]:
        print("no issues found (or not enough data yet)")
    for f in r["findings"]:
        print(f"[{f['severity']}] {f['code']}: {f['count']} - {f['message']}")
        for e in f["examples"]:
            print(f"      {e}")


if __name__ == "__main__":
    main()
