"""Causal replay through live Hunt C-FI + timing."""
from __future__ import annotations
from .isolation import fail_open
from . import observe as obsmod

@fail_open({"ok": False, "error": "replay_failed", "n": 0})
def run(max_bars=400):
    from ..market_data import data_access as dao
    from ..market_data.closed_candles import filter_closed
    from ..brain.observation_hunt_c_fi import evaluate_hunt_c_fi, HUNT_VERSION_C_FI
    from ..brain.weather import classify
    from ..brain.observation_hunt_c import parent_open
    from ..fair_value_gap.observe import observe as obs_fvg
    from ..momentum.observe import observe as obs_mom
    from ..volume.observe import observe as obs_vol
    from ..support_resistance.observe import observe as obs_sr
    c15=filter_closed(dao.read_candles("15m", limit=max_bars+80), "15m")
    c5=filter_closed(dao.read_candles("5m", limit=(max_bars+80)*3), "5m")
    c1=filter_closed(dao.read_candles("1m", limit=(max_bars+80)*15), "1m")
    c4=filter_closed(dao.read_candles("4h", limit=400), "4h")
    c1h=filter_closed(dao.read_candles("1h", limit=800), "1h")
    if len(c15)<80 or len(c5)<80:
        return {"ok": False, "error": "insufficient_candles", "n15": len(c15), "n5": len(c5)}
    n=0; start=max(60, len(c15)-max_bars)
    for i in range(start, len(c15)):
        window15=c15[max(0,i-120):i+1]; ts15=int(window15[-1]["ts"]); horizon=ts15+900
        w5=[c for c in c5 if int(c["ts"])+300<=horizon and int(c["ts"])>=ts15-86400]
        if len(w5)<30: continue
        fill=w5[-1]
        live=[c for c in w5 if parent_open(c["ts"])==parent_open(fill["ts"])]
        w1=[c for c in c1 if int(c["ts"])+60<=int(fill["ts"])+300]
        w4=[c for c in c4 if int(c["ts"])+14400<=horizon][-200:]
        w1h=[c for c in c1h if int(c["ts"])+3600<=horizon][-200:]
        try:
            aux={"mom": obs_mom(window15,"15m"), "vol": obs_vol(window15,"15m"), "sr": obs_sr(window15,"15m"), "fvg": obs_fvg(window15,"15m")}
        except Exception:
            aux={}
        hunt=evaluate_hunt_c_fi(window15, fill, live_5ms=live, candles_4h=w4, candles_1h=w1h, candles_5m=w5, candles_1m=w1[-400:], aux=aux)
        wx=classify(w4, w1h) if w4 else {}
        extra={"price": float(fill["close"]), "bar_ts_15m": ts15, "bar_ts_5m": int(fill["ts"]), "bar_ts_1m": int(w1[-1]["ts"]) if w1 else None}
        if obsmod.observe_hunt(hunt, weather=wx, extra=extra, source="replay", ts=int(fill["ts"])+300):
            n+=1
        hunt["brain_version"]=hunt.get("brain_version") or HUNT_VERSION_C_FI
    return {"ok": True, "n": n, "hunt_version": HUNT_VERSION_C_FI}
