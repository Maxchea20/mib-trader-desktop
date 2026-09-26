"""Read-only score. Never changes Hunt."""
from __future__ import annotations
import math
from . import features as featmod
from .isolation import fail_open
from . import model_store

def _dot(w,x): return sum(a*b for a,b in zip(w,x))
def _sigmoid(z):
    if z < -30: return 0.0
    if z > 30: return 1.0
    return 1.0/(1.0+math.exp(-z))

@fail_open(None)
def score(hunt=None, weather=None, extra=None, ts=0):
    rec = model_store.latest()
    if not rec or not rec.get("artifact"): return None
    art = rec["artifact"]
    x = featmod.vector(featmod.extract(hunt or {}, weather or {}, ts, extra or {}))
    w_cls = art.get("w_cls") or [0]
    w_reg = art.get("w_reg") or [0]
    if len(w_cls) != len(x)+1: return None
    p = _sigmoid(w_cls[0]+_dot(w_cls[1:], x))
    er = w_reg[0]+_dot(w_reg[1:], x)
    quality = "strong" if er >= 0.25 else ("ok" if er >= 0 else "poor")
    return {"available": True, "model_id": rec.get("model_id"), "p_win": round(float(p),4), "expected_r": round(float(er),4), "quality": quality, "veto": False, "size_mult": 1.0, "similar_n": None}
