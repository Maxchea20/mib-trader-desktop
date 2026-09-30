"""Research-only: scalping study (9/21 EMA + RSI, VWAP) on 1m / 5m candles with percent targets.

Entry at the signal bar's close.  TP and SL are +/-T of the entry price (T = 0.1 / 0.2 / 0.3 %), SL wins a
same-bar tie, and a trade still open after H bars is closed at the market.  Results are in R (SL = -1 R).
Fees: taker 0.020% each side (round trip 0.04%, MEXC BTCUSDT perp) and the maker-both-sides upper bound (0)
- a maker fill is never guaranteed, so the maker case can only ever be an upper bound.
Every event is compared with entering in the same direction on ANY bar (drift control).  No orders."""
import math
from typing import Dict, List

import numpy as np
import pandas as pd

TAKER_RT = 0.0004
TARGETS = (0.001, 0.002, 0.003)
HOLD = {"1m": 30, "5m": 12}           # max bars held, fixed in advance


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def signals(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """+1 / -1 / 0 per bar, decided on that bar's close."""
    c = df["close"]
    e9, e21, r = ema(c, 9), ema(c, 21), rsi(c)
    up = (e9 > e21) & (e9.shift(1) <= e21.shift(1)) & (r >= 50) & (r <= 70)
    dn = (e9 < e21) & (e9.shift(1) >= e21.shift(1)) & (r <= 50) & (r >= 30)
    sig_ema = np.where(up, 1, np.where(dn, -1, 0))
    day = df["ts"] // 86400
    tp = (df["high"] + df["low"] + c) / 3.0
    vwap = (tp * df["volume"]).groupby(day).cumsum() / df["volume"].groupby(day).cumsum()
    pos = df.groupby(day).cumcount()
    vu = (c > vwap) & (c.shift(1) <= vwap.shift(1)) & (pos > 30)
    vd = (c < vwap) & (c.shift(1) >= vwap.shift(1)) & (pos > 30)
    sig_vwap = np.where(vu, 1, np.where(vd, -1, 0))
    return {"EMA9/21+RSI": sig_ema, "VWAP-cross": sig_vwap}


def race_r(hi: np.ndarray, lo: np.ndarray, cl: np.ndarray, T: float, H: int, s: int) -> np.ndarray:
    """Gross result in R of entering at every bar's close in direction s (NaN where H bars do not follow)."""
    n = len(cl)
    out = np.full(n, np.nan)
    done = np.zeros(n, bool)
    tp = cl * (1 + s * T)
    sl = cl * (1 - s * T)
    for k in range(1, H + 1):
        m = n - k
        if s > 0:
            hit_sl, hit_tp = lo[k:] <= sl[:m], hi[k:] >= tp[:m]
        else:
            hit_sl, hit_tp = hi[k:] >= sl[:m], lo[k:] <= tp[:m]
        new = ~done[:m]
        sl_now = new & hit_sl
        tp_now = new & hit_tp & ~hit_sl
        view = out[:m]
        view[sl_now] = -1.0
        view[tp_now] = 1.0
        done[:m] |= sl_now | tp_now
    m = n - H
    open_ = ~done[:m]
    pnl = s * (cl[H:] - cl[:m]) / cl[:m] / T
    out[:m][open_] = np.clip(pnl[open_], -1.0, 1.0)
    out[m:] = np.nan
    return out


def _stats(x: np.ndarray):
    n = len(x)
    if n < 2:
        return 0.0, 0.0
    m = float(x.mean())
    sd = float(x.std(ddof=1))
    return m, (m / (sd / math.sqrt(n)) if sd > 0 else 0.0)


def decluster_idx(idx: np.ndarray, gap: int) -> np.ndarray:
    out, last = [], -10 ** 12
    for i in idx:
        if i - last >= gap:
            out.append(i)
            last = i
    return np.array(out, dtype=int)


def judge(sig: np.ndarray, R: Dict[int, np.ndarray], T: float, H: int, n_tests: int,
          taker_rt: float = TAKER_RT) -> Dict:
    fee_r = taker_rt / T
    ev = decluster_idx(np.where(sig != 0)[0], H)
    base = {s: float(np.nanmean(R[s])) for s in (1, -1)}
    rows = []
    for i in ev:
        s = int(sig[i])
        r = R[s][i]
        if not math.isnan(r):
            rows.append((i, s, r))
    out = {"n": len(rows), "fee_r": fee_r, "base": base}
    if len(rows) < 30:
        out.update({"pass_taker": False, "pass_maker": False})
        return out
    arr = np.array([r for _, _, r in rows])
    sides = np.array([s for _, s, _ in rows])
    exc = np.array([r - base[s] for _, s, r in rows])
    half = len(rows) // 2

    def pack(mask):
        a, e = arr[mask], exc[mask]
        return {"n": int(mask.sum()), "gross": float(a.mean()) if len(a) else 0.0,
                "excess": float(e.mean()) if len(e) else 0.0}
    allm = np.ones(len(rows), bool)
    parts = {"all": pack(allm), "LONG": pack(sides == 1), "SHORT": pack(sides == -1),
             "H1": pack(np.arange(len(rows)) < half), "H2": pack(np.arange(len(rows)) >= half)}
    m_ex, t_ex = _stats(exc)
    p_one = 0.5 * math.erfc(t_ex / math.sqrt(2))
    alpha = 0.05 / n_tests
    consistent = all(parts[k]["excess"] > 0 for k in ("LONG", "SHORT", "H1", "H2"))
    net_taker, net_maker = float(arr.mean()) - fee_r, float(arr.mean())
    out.update({"parts": parts, "win": float((arr > 0).mean()), "gross": float(arr.mean()), "excess": m_ex,
                "t": t_ex, "p": p_one, "net_taker": net_taker, "net_maker": net_maker, "consistent": consistent,
                "pass_taker": bool(p_one < alpha and consistent and net_taker > 0),
                "pass_maker": bool(p_one < alpha and consistent and net_maker > 0)})
    return out
