"""Research-only: let a model search ALL the data together for a setup.

Every 15-minute bar is a decision point.  Its features describe what all timeframes (5m .. 1d) looked
like at that bar's close (nothing later).  Its labels say whether a LONG / SHORT entered at that close
would have hit +3 ATR before -1.5 ATR.  A gradient-boosted model is trained walk-forward (only on the
past, with an embargo) and scored on the future it never saw; a sealed last period is only opened once,
and only if the walk-forward result passes a pre-set gate.  No orders, no engine."""
import math
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

TFS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
BASE, BASE_SEC = "15m", 900
SL_ATR, TP_ATR, MAX_BARS = 1.5, 3.0, 200
FEE_RT = 0.0009
THRESH_P = 0.40          # trade when the model's win probability is at least this (fixed in advance)
EMBARGO = MAX_BARS * BASE_SEC


def load_tf(dbs: Sequence[str], symbol: str, tf: str) -> pd.DataFrame:
    frames = []
    for path in dbs:
        con = sqlite3.connect(path)
        try:
            frames.append(pd.read_sql_query(
                "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
                con, params=(symbol, tf)))
        finally:
            con.close()
    df = pd.concat(frames).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def tf_features(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    """Causal features for every bar of one timeframe (bar j uses bars <= j)."""
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    atr = _atr(df)
    f = {f"{tag}_atr_pct": atr / c, f"{tag}_atr_ratio": atr / atr.rolling(100).median()}
    for k in (1, 3, 6, 12, 24):
        f[f"{tag}_ret{k}"] = (c - c.shift(k)) / atr
    for w in (24, 96):
        hi, lo = h.rolling(w).max(), l.rolling(w).min()
        f[f"{tag}_pos{w}"] = (c - lo) / (hi - lo)
        f[f"{tag}_dhi{w}"] = (hi - c) / atr
        f[f"{tag}_dlo{w}"] = (c - lo) / atr
    for w in (20, 50, 200):
        f[f"{tag}_sma{w}"] = (c - c.rolling(w).mean()) / atr
    f[f"{tag}_vol"] = v / v.rolling(20).mean()
    f[f"{tag}_body"] = (c - o) / atr
    f[f"{tag}_uw"] = (h - np.maximum(o, c)) / atr
    f[f"{tag}_lw"] = (np.minimum(o, c) - l) / atr
    f[f"{tag}_rng"] = (h - l) / atr
    return pd.DataFrame(f)


def _race(hi, lo, cl, i, s, a):
    n = len(cl)
    for k in range(i + 1, min(n, i + 1 + MAX_BARS)):
        adv = (cl[i] - lo[k]) if s > 0 else (hi[k] - cl[i])
        fav = (hi[k] - cl[i]) if s > 0 else (cl[i] - lo[k])
        if adv >= SL_ATR * a:
            return 0, k - i
        if fav >= TP_ATR * a:
            return 1, k - i
    return -1, MAX_BARS


RAW_WINDOWS = {"5m": 24, "15m": 32, "1h": 24, "4h": 20, "1d": 14}    # bars of raw candles the model may look at


def raw_windows(frames: Dict[str, pd.DataFrame], close_ts: np.ndarray) -> pd.DataFrame:
    """No engineered indicators: the last W raw candles of each timeframe, scaled only by that timeframe's
    ATR and anchored on its latest close (prices) / its 20-bar mean volume (volume)."""
    cols, names = [], []
    for tf, W in RAW_WINDOWS.items():
        d = frames[tf].reset_index(drop=True)
        atr = _atr(d).to_numpy()
        o, h, l, c, v = (d[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
        vm = pd.Series(v).rolling(20).mean().to_numpy()
        j = np.searchsorted(d["ts"].to_numpy() + TFS[tf], close_ts, side="right") - 1
        for k in range(W):
            jj = j - k
            bad = jj < 0
            jc = np.where(bad, 0, jj)
            ref = np.where(j < 0, 0, j)
            for nm, arr in (("o", o), ("h", h), ("l", l), ("c", c)):
                if nm == "c" and k == 0:
                    continue
                val = (arr[jc] - c[ref]) / atr[ref]
                val[bad | (j < 0)] = np.nan
                cols.append(val.astype(np.float32))
                names.append(f"{tf}_{nm}{k}")
            val = v[jc] / vm[ref]
            val[bad | (j < 0)] = np.nan
            cols.append(val.astype(np.float32))
            names.append(f"{tf}_v{k}")
    return pd.DataFrame(np.column_stack(cols), columns=names)


def build_dataset(frames: Dict[str, pd.DataFrame], inputs: str = "features", stride: int = 1) -> Dict:
    base = frames[BASE].reset_index(drop=True)
    close_ts = base["ts"].to_numpy() + BASE_SEC
    hour = ((close_ts % 86400) / 3600.0)
    if inputs == "raw":
        X = raw_windows(frames, close_ts)
    else:
        cols = []
        for tf, sec in TFS.items():
            d = frames[tf].reset_index(drop=True)
            ft = tf_features(d, tf)
            idx = np.searchsorted(d["ts"].to_numpy() + sec, close_ts, side="right") - 1
            ok = idx >= 0
            m = ft.iloc[np.where(ok, idx, 0)].reset_index(drop=True)
            m.loc[~ok, :] = np.nan
            cols.append(m)
        X = pd.concat(cols, axis=1)
    X["hour_sin"], X["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    X["dow"] = ((close_ts // 86400 + 3) % 7).astype(float)
    hi, lo, cl = base["high"].to_numpy(), base["low"].to_numpy(), base["close"].to_numpy()
    atr = _atr(base).to_numpy()
    n = len(base)
    y = {1: np.full(n, np.nan), -1: np.full(n, np.nan)}
    dur = {1: np.zeros(n, int), -1: np.zeros(n, int)}
    for i in range(n - 1):
        a = atr[i]
        if not a or math.isnan(a):
            continue
        for s in (1, -1):
            r, d = _race(hi, lo, cl, i, s, a)
            if r >= 0:
                y[s][i], dur[s][i] = r, d
    fee_r = FEE_RT * cl / (SL_ATR * np.where(atr > 0, atr, np.nan))
    out = {"X": X, "y": y, "dur": dur, "fee_r": fee_r, "ts": close_ts, "atr": atr}
    if stride > 1:                                            # thin the decision bars (saves memory in raw mode)
        keep = np.arange(0, n, stride)
        out = {"X": X.iloc[keep].reset_index(drop=True), "y": {k: v[keep] for k, v in y.items()},
               "dur": {k: v[keep] for k, v in dur.items()}, "fee_r": fee_r[keep], "ts": close_ts[keep], "atr": atr[keep]}
    return out


def _model(kind: str = "gbm"):
    if kind == "tree":
        from sklearn.tree import DecisionTreeClassifier
        return DecisionTreeClassifier(max_depth=4, min_samples_leaf=800, random_state=7)
    import lightgbm as lgb
    return lgb.LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=15, min_child_samples=300,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.5, reg_lambda=5.0,
                              max_bin=63, verbose=-1, n_jobs=-1, random_state=7)


def tree_rules(model, names: Sequence[str], min_p: float = THRESH_P) -> List[Dict]:
    """Readable IF-THEN rules: every leaf of a fitted decision tree whose training win rate is >= min_p."""
    t = model.tree_
    out: List[Dict] = []

    def walk(node, conds):
        if t.children_left[node] == -1:
            v = t.value[node][0]
            tot = float(v.sum())
            p = float(v[1] / tot) if tot and len(v) > 1 else 0.0
            if p >= min_p:
                out.append({"rule": " AND ".join(conds) or "always", "p_train": p, "n_train": int(t.n_node_samples[node])})
            return
        nm, th = names[t.feature[node]], t.threshold[node]
        walk(t.children_left[node], conds + [f"{nm} <= {th:.3f}"])
        walk(t.children_right[node], conds + [f"{nm} > {th:.3f}"])

    walk(0, [])
    return sorted(out, key=lambda r: -r["p_train"])


def walk_forward(data: Dict, first_test: int, last_ts: int, fold_days: int = 91,
                 log=print, kind: str = "gbm") -> Dict[int, np.ndarray]:
    """Expanding-window walk-forward.  Returns out-of-sample probabilities (NaN where not scored)."""
    ts, X, y = data["ts"], data["X"], data["y"]
    oos = {1: np.full(len(ts), np.nan), -1: np.full(len(ts), np.nan)}
    start = first_test
    imp = None
    rules: Dict[int, List[Dict]] = {1: [], -1: []}
    while start < last_ts:
        end = min(start + fold_days * 86400, last_ts)
        tr = np.where((ts < start - EMBARGO))[0]
        te = np.where((ts >= start) & (ts < end))[0]
        if len(te) and len(tr) > 5000:
            for s in (1, -1):
                lab = ~np.isnan(y[s][tr])
                m = _model(kind).fit(X.iloc[tr[lab]], y[s][tr][lab].astype(int))
                oos[s][te] = m.predict_proba(X.iloc[te])[:, 1]
                if kind == "tree":
                    rules[s] = tree_rules(m, list(X.columns))
                elif s == 1:
                    imp = pd.Series(m.booster_.feature_importance("gain"), index=X.columns)
            log(f"  fold {pd.to_datetime(start, unit='s').date()} -> {pd.to_datetime(end, unit='s').date()}: "
                f"train {len(tr)}, test {len(te)}")
        start = end
    oos["imp"] = imp
    oos["rules"] = rules                       # the rules of the LAST fold (trained on the most data)
    return oos


def simulate(data: Dict, pred: Dict[int, np.ndarray], lo_ts: int, hi_ts: int, thresh: float = THRESH_P) -> List[Dict]:
    """One position at a time, taking the most confident side when its probability >= thresh."""
    ts = data["ts"]
    n = len(ts)
    trades, busy = [], -1
    for i in range(n):
        if ts[i] < lo_ts or ts[i] >= hi_ts or ts[i] <= busy:
            continue
        pl, ps = pred[1][i], pred[-1][i]
        cand = [(p, s) for p, s in ((pl, 1), (ps, -1)) if not math.isnan(p) and p >= thresh]
        if not cand:
            continue
        p, s = max(cand)
        r = data["y"][s][i]
        if math.isnan(r):
            continue
        trades.append({"i": i, "ts": int(ts[i]), "s": s, "p": p, "win": int(r), "fee_r": float(data["fee_r"][i])})
        busy = int(ts[i]) + int(data["dur"][s][i]) * BASE_SEC
    return trades


def summarize(trades: List[Dict]) -> Dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    w = sum(t["win"] for t in trades)
    gross = [(TP_ATR / SL_ATR) if t["win"] else -1.0 for t in trades]
    net = [g - t["fee_r"] for g, t in zip(gross, trades)]
    fee = sum(t["fee_r"] for t in trades) / n
    be = (1.0 + fee) / (1.0 + TP_ATR / SL_ATR)                       # win rate needed to break even after fees
    z = (w / n - be) / math.sqrt(be * (1 - be) / n)
    p_one = 0.5 * math.erfc(z / math.sqrt(2))                        # one-sided: beats break-even?
    wins_r = sum(x for x in net if x > 0)
    loss_r = -sum(x for x in net if x <= 0)
    return {"n": n, "wins": w, "rate": w / n, "breakeven": be, "z": z, "p": p_one, "net_r": sum(net),
            "net_per_trade": sum(net) / n, "pf": (wins_r / loss_r) if loss_r > 0 else float("inf"), "fee_r": fee}


def auc(y: np.ndarray, p: np.ndarray) -> float:
    m = ~np.isnan(y) & ~np.isnan(p)
    if m.sum() < 100:
        return float("nan")
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y[m].astype(int), p[m]))
