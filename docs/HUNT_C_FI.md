# Hunt C-FI (C-fast + Internal + rearm)

Live book on main. Paper or Isolated MEXC. One position.

## After-FIRE (frozen thesis)

Thesis is frozen at FIRE: `thesis_ts`, `thesis_level`, `thesis_invalid` (parent HL for a long / parent LH for a short).

Structural invalidation is **not** 15m close vs fill.

A **new** closed 15m after `opened_at` may kill only when:

1. 15m CHoCH against the frozen side → `THESIS_FAILURE`, or
2. close beyond frozen parent **and** close beyond frozen level → `STRUCTURAL_INVALIDATION`

One of those two price checks alone is HOLD. The fire-bar 15m is skipped. Hard SL and TP still always exit. SL/TP distances stay 1.5 / 2.5 ATR.

## Re-entry

One position. Hard SL uses a 3-bar cooldown. SI / thesis-fail / TP do **not** lock the gun.

Hunt may fire again on:

- a fresh 15m CHoCH / usable BOS (C-fast or Internal), or
- the **same** 15m thesis if it is still in force (no opposite CHoCH, BOS streak still < 3) and 5m answers again (`gate=rearm_cfast` / `rearm_internal`)

## Live ticket numbers

Open and closed AUTO rows read MEXC.
History columns for live tickets:
- Profit = MEXC close PnL before fee
- Fee = MEXC fee
- PnL = MEXC realised after fee

Hunt SL/TP prices stay Hunt 1.5 / 2.5.
Old ~$1000 paper rows stay paper.
