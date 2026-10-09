# ORB engine assumptions

New York cash open only. `America/New_York`, including daylight saving time.
Weekends and full NYSE closures are not traded. Early-close days are traded
because 09:30 still happens and the entry window ends at 11:00, before 13:00.

No London session, Asian session, or other clock is used.

## Data

Primary file: `backend/market_data_clean.db` (Git LFS). The committed object
without LFS is a 133-byte pointer and is refused.

The file has one symbol, `BTC_USDT`. Candles are OHLCV. There is no bid/ask,
trade, or order-book history. Four `decisions.spread` snapshots exist; they
are not a spread path and are not replayed.

`5m` is the backtest timeframe: 2025-09-15 to 2026-09-27, no gaps, and 5/15/30
minutes are exact bar counts. `1m` is a short resolution check only. `15m`
cannot build ORB-5 without using prices from outside those five minutes.

A range with a missing candle is skipped. Partial highs and lows are not used.

## Orders and fills

- Close and retest signals are known at the signal bar's close. The fill is
  the next bar's open, and only if that open is strictly before 11:00 New York.
  Otherwise the order is cancelled. 11:00 does not flatten an open position.
- Intrabar fills are an OHLC proxy (`ohlc_proxy=true`). The database does not
  say when inside the bar the level traded. `info_known_ts` is the bar close,
  which is later than the proxy fill. Do not read those rows as executable.
- A bar that trades both sides of the range is skipped.
- A bar that opens already through the level fills from that open, not from
  the level the market has passed.
- If stop and target are both inside a bar, the stop is taken and the row is
  `path_ambiguous`.
- A stop gap fills at the open (worse than the stop). A target gap fills at
  the target, not at the better open.
- Slippage and half-spread are adverse cost adjustments, then prices are
  snapped to the 0.1 tick. They are not claimed prints. `fill_outside_bar`
  marks an adjusted price the candle did not trade.
- Normal cost: 1 bp plus a 0.10 USDT spread. Conservative cost: 4 bp plus a
  1.40 USDT spread (the worst of the four stored spread snapshots, used only
  as a stress, not as a measured open).
- Fees: taker 0.0002 each side, from the 2026-10-09 MEXC `BTC_USDT` contract
  detail. Maker fee is 0 and is not used. Isolated margin. A gap through the
  approximate liquidation price is capped at initial margin minus maintenance
  and charged the snapshot liquidation fee. This is not MEXC's official
  bankruptcy formula.
- Stops are a fixed percent of the fill. They are not ATR stops. The two risk
  profiles in the grid were written down before the run. The grid is not a
  search, and nothing was selected on the out-of-sample dates.

## Live

`src/orb/live.py` can build the same market-order body the existing private
adapter sends. It does not import that adapter and it raises unless the caller
passes `allow_live_submit=True` and a sender. Intrabar stop-entry is refused
because the adapter has no plan-order method. The backtest does not call it.


## ORB V2

V2 is not ORB-5, ORB-15, or ORB-30. The original engine above is unchanged
and is still the baseline. V2 does not import Hunt, S1, or S2, and it has no
live submit path.

Sequence, New York cash open only:

1. The single 5-minute candle that opens at 09:30 and closes at 09:35 sets
   `OR_HIGH` and `OR_LOW`. Those two prices stay fixed for the day. The range
   is not an entry. If that candle is missing, its OHLC is invalid, the five
   1-minute candles inside it are missing, or those 1-minute candles do not
   rebuild the same OHLC, the session is skipped.
2. From the 09:35 candle onward, the first completed 5-minute candle whose
   close is strictly beyond one side is the day's only breakout. LONG is
   `close > OR_HIGH`. SHORT is `close < OR_LOW`. A wick through the level with
   a close back inside is not a breakout. One candle has one close, so it
   cannot confirm both sides when `OR_HIGH >= OR_LOW`. No intrabar ordering is
   used. A missing 5-minute slot before 11:00 stops the search; a later candle
   is not allowed to fill the hole. The breakout candle must close strictly
   before 11:00.
3. After that close, the first completed 1-minute continuation is the entry
   signal. LONG needs a bullish minute (`close > open`) that also closes
   strictly above `OR_HIGH`. SHORT needs a bearish minute (`close < open`)
   that closes strictly below `OR_LOW`. A 1-minute candle that closed at or
   before the 5-minute confirmation is ignored, including the minutes inside
   the breakout candle. The fill is the next 1-minute bar's open, not the
   signal close. There is no retest.
4. One signal per session. The direction does not flip. A second minute is
   not another entry, including after the first trade has already exited.
5. The next open is filled only when its timestamp is strictly before 11:00.
   An 11:00 bar does not open a new trade and does not flatten an open one.
   Stops and targets keep managing after 11:00.

The distance filter is off on the primary run. A separate run sets
`max_entry_distance_range_multiple=1`: the next open may not be farther beyond
the broken level than one opening-range width. That run is not a search and
is not used to pick a risk setting.

Risk, fees, slippage, spread, leverage, and the stop-first same-bar rule are
the original primary preset. Exits are still an OHLC proxy, resolved on
1-minute bars because that is the entry timeframe. `1m` history in
`market_data_clean.db` does not cover the full 5-minute sample, so V2 cannot
be run on the 2025-09-16 to 2026-07-13 baseline window. Sessions outside the
1-minute file are skipped, not synthesized.

## ORB V3

V3 corrects V2's opening range. V2's code and results are left in place and
are not the V3 result. The original ORB engine is also unchanged. V3 does not
import Hunt, S1, or S2, and it does not submit orders.

Sequence, New York cash open only:

1. The 15-minute candle that opens at 09:30 and closes at 09:45 sets
   `OR_HIGH` and `OR_LOW`. Those prices are frozen at 09:45. The first
   five-minute candle is not the range. When the three 5-minute candles
   inside 09:30-09:45 all exist, they must rebuild the 15-minute OHLC or
   the session is skipped. One-minute candles are not required to build
   the range and are never fabricated from five-minute OHLC.
2. The first 5-minute body breakout is sought from the 09:45 candle onward,
   not from 09:35, and not only on the 09:45 candle. LONG is `close > OR_HIGH`.
   SHORT is `close < OR_LOW`. A wick with the close back inside is not a
   breakout. The first valid side is locked for the session. A missing
   5-minute slot before 11:00 stops the search. The breakout candle must
   close strictly before 11:00.
3. The entry is the next completed 1-minute continuation after that close.
   LONG needs `close > open` and `close > OR_HIGH`. SHORT needs `close < open`
   and `close < OR_LOW`. No retest. Minutes that completed at or before the
   5-minute confirmation are ignored. The fill is the next 1-minute open,
   not the signal close. If that next open is not the immediately following
   minute, the order is not filled. If it is at or after 11:00, it is
   cancelled. No new entry is taken when a breakout has no qualifying
   1-minute continuation before 11:00.
4. One entry per session. Direction does not reverse. A later opposite
   5-minute close does not flip the day.
5. Stops and targets keep managing after 11:00 on 1-minute bars. If a
   1-minute hole opens while a position is still on, the trade is left
   unresolved (`data_gap`) rather than marked to a later bar across the
   hole. Same-bar stop and target takes the stop and sets `path_ambiguous`.

The reported backtest uses only sessions where the 15-minute range, the
5-minute breakout path, and the real 1-minute entry window are all present.
Earlier 5-minute history is not turned into fake 1-minute entries. Risk,
fees, slippage, spread, and leverage are the original primary preset
(0.25% stop, 0.50% target, 1% equity risk, 5x, 1 bp, 0.10 spread, 1,000
USDT restarted on every chronological split). Nothing was searched.

Funding is a flat snapshot of the MEXC `BTC_USDT` rate taken at
2026-10-09 13:21:54 UTC: `+0.000015` (+0.0015%) every 8 hours, settled at
00:00, 08:00 and 16:00 UTC. A positive rate means longs pay and shorts
receive. The mark is that minute's open, because the file has no fair-price
series. It is not the funding path from August and September 2026. A
position opened on the settlement minute is not charged for that print.
A position closed before the settlement is not charged. Taker commission
is unchanged at 0.02% of notional on entry and on exit.

