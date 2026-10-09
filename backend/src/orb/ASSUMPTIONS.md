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
