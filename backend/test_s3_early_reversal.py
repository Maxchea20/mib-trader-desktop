"""Unit tests for s3_early_reversal.py. No exchange connection, no
live wiring touched -- this module isn't called from the live loop yet.
"""
import os
import sys

sys.path.insert(0, ".")
os.environ["MARKET_DB_PATH"] = "/home/claude/work/market_data_clean.db"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS  {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}  {detail}")


from src.brain import s3_early_reversal as s3  # noqa: E402
from src.market_data import database as db  # noqa: E402

# --- ReversalOwnership: pure logic, synthetic data ---
own = s3.ReversalOwnership()
check("fresh registry: nothing owned yet", not own.is_owned("LONG", 50000.0, 1000, 200.0))

own.own("LONG", 50000.0, 1000)
check("after own(): same direction+level now owned", own.is_owned("LONG", 50000.0, 1005, 200.0))
check("after own(): tiny level difference within tolerance still owned",
      own.is_owned("LONG", 50000.0 + 50.0, 1005, 200.0))  # 50 < 0.3*200=60
check("after own(): level difference beyond tolerance NOT owned",
      not own.is_owned("LONG", 50000.0 + 100.0, 1005, 200.0))  # 100 > 60
check("after own(): opposite direction NOT owned", not own.is_owned("SHORT", 50000.0, 1005, 200.0))

# expiry
own2 = s3.ReversalOwnership()
own2.own("SHORT", 49000.0, 1000)
check("within window: still owned", own2.is_owned("SHORT", 49000.0, 1000 + s3.OWNERSHIP_WINDOW_SECONDS - 1, 200.0))
check("past window: expired, no longer owned",
      not own2.is_owned("SHORT", 49000.0, 1000 + s3.OWNERSHIP_WINDOW_SECONDS + 1, 200.0))

# --- RSI range: sanity on synthetic monotonic data ---
up_candles = [{"ts": i, "open": 100 + i, "high": 100 + i + 1, "low": 100 + i - 1, "close": 100 + i, "volume": 10}
              for i in range(40)]
rng_up = s3._rsi_range(up_candles)
check("monotonic uptrend: RSI range max is high (overbought territory)", rng_up["max"] >= 60, detail=str(rng_up))

flat_candles = [{"ts": i, "open": 100, "high": 100.5, "low": 99.5,
                  "close": 100 + (0.3 if i % 2 == 0 else -0.3), "volume": 10} for i in range(40)]
rng_flat = s3._rsi_range(flat_candles)
check("choppy/no-trend data: RSI stays near neutral (~50)", 35 <= rng_flat["max"] <= 65, detail=str(rng_flat))

check("too little data: returns neutral default without crashing",
      s3._rsi_range(up_candles[:5]) == {"max": 50.0, "min": 50.0})

# --- detect(): must never fire when M15 already had a fresh event ---
real_15m = db.get_candles("BTC_USDT", "15m", limit=2000)[:-1]
real_5m = db.get_candles("BTC_USDT", "5m", limit=3000)[:-1]
real_1m = db.get_candles("BTC_USDT", "1m", limit=15000)[:-1]

own3 = s3.ReversalOwnership()
result_blocked = s3.detect(real_15m, real_5m, real_1m, m15_had_fresh_event=True, ownership=own3)
check("detect() returns None when M15 already had a fresh event (S3 never competes with S1/S2)",
      result_blocked is None)

# --- detect(): never fires without enough data (never fires blind) ---
result_short = s3.detect(real_15m, real_5m[:10], real_1m, m15_had_fresh_event=False, ownership=own3)
check("detect() returns None with insufficient 5m history", result_short is None)

result_no1m = s3.detect(real_15m, real_5m, [], m15_had_fresh_event=False, ownership=own3)
check("detect() returns None with no M1 data at all (never fires blind)", result_no1m is None)

# --- detect(): real-data smoke test -- must not crash across a real
#     historical run, and any candidate it does produce must carry
#     complete, sane fields ---
crashed = False
candidates_found = 0
try:
    for i in range(60, len(real_5m)):
        window_5m = real_5m[: i + 1]
        window_ts = window_5m[-1]["ts"]
        window_15m = [c for c in real_15m if c["ts"] <= window_ts]
        window_1m = [c for c in real_1m if c["ts"] <= window_ts]
        if len(window_15m) < 60:
            continue
        cand = s3.detect(window_15m, window_5m, window_1m, m15_had_fresh_event=False, ownership=own3)
        if cand is not None:
            candidates_found += 1
            check(f"candidate #{candidates_found}: direction is LONG or SHORT",
                  cand.direction in ("LONG", "SHORT"), detail=str(cand))
            check(f"candidate #{candidates_found}: exhaustion_side matches direction",
                  (cand.direction == "SHORT" and cand.exhaustion_side == "OVERBOUGHT")
                  or (cand.direction == "LONG" and cand.exhaustion_side == "OVERSOLD"),
                  detail=str(cand))
            check(f"candidate #{candidates_found}: m1_confirmed is True (detect() never returns unconfirmed)",
                  cand.m1_confirmed, detail=str(cand))
            check(f"candidate #{candidates_found}: atr15 is positive", cand.atr15 > 0, detail=str(cand))
            result_dict = s3.candidate_to_result(cand, price=window_5m[-1]["close"], thesis_id="TH-TEST")
            check(f"candidate #{candidates_found}: candidate_to_result() shape has action=FIRE",
                  result_dict["action"] == "FIRE")
        if candidates_found >= 5:
            break  # enough for a smoke test; full backtest is a separate step
except Exception as e:
    crashed = True
    print("CRASH during real-data smoke test:", repr(e))

check("real-data smoke test: no crash across historical run", not crashed)
print(f"\n(informational, not pass/fail) candidates found in smoke test: {candidates_found}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)