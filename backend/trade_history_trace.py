"""Run this on YOUR machine, from backend/. Shows your real trade
history in chronological order with running balance impact, so you can
see exactly what happened between any specific win and now -- rather
than guessing. Read-only, touches nothing.
"""
import sys
sys.path.insert(0, ".")

from src import paper_trading as pt

all_trades = pt.list_trades()
closed = [t for t in all_trades if t.get("status") == "CLOSED" and t.get("closed_at")]
closed.sort(key=lambda t: t["closed_at"])

if not closed:
    print("No closed trades found.")
    sys.exit(0)

print(f"{'#':>3} {'closed_at':>12} {'side':>6} {'entry':>10} {'exit':>10} {'pnl':>9} {'reason':>16}")
print("-" * 75)
running = 0.0
for i, t in enumerate(closed, 1):
    pnl = t.get("pnl")
    pnl_val = float(pnl) if pnl is not None else 0.0
    running += pnl_val
    print(f"{i:>3} {int(t['closed_at']):>12} {t.get('side',''):>6} "
          f"{t.get('entry_price',0):>10.1f} {t.get('exit_price',0):>10.1f} "
          f"{pnl_val:>+9.2f} {t.get('exit_reason','') or '':>16}")

print("-" * 75)
print(f"Cumulative realized PNL across all {len(closed)} closed trades: {running:+.2f}")
print(f"\nLast 10 trades only, for a quick recent view:")
for t in closed[-10:]:
    pnl = t.get("pnl")
    print(f"  closed_at={int(t['closed_at'])}  side={t.get('side')}  "
          f"pnl={float(pnl) if pnl is not None else 0:+.2f}  reason={t.get('exit_reason')}")