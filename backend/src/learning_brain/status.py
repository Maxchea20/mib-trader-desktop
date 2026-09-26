"""UI payload. Fail-open."""
from __future__ import annotations
import json, time
from .isolation import fail_open
from .schema import conn, init
from . import infer, model_store
_OFF = {"available": False, "stage": "OFFLINE", "model": None, "veto": False, "size_adjust": False, "live_impact": "NONE"}

@fail_open(_OFF)
def payload(hunt=None, weather=None, extra=None):
    init()
    with conn() as c:
        n_snap = c.execute("SELECT COUNT(*) AS n FROM lb_snapshots").fetchone()["n"]
        n_lab = c.execute("SELECT COUNT(*) AS n FROM lb_outcomes").fetchone()["n"]
        kinds = c.execute("SELECT row_kind, COUNT(*) AS n FROM lb_snapshots GROUP BY row_kind").fetchall()
        last = c.execute("SELECT * FROM lb_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        avg = c.execute("SELECT AVG(r_multiple) AS a, COUNT(*) AS n FROM lb_outcomes").fetchone()
        c_miss = c.execute("SELECT COUNT(*) AS n FROM lb_snapshots WHERE row_kind='C_MISS'").fetchone()["n"]
        c_ok = c.execute("SELECT COUNT(*) AS n FROM lb_snapshots WHERE timing IN ('S1','S2','SLOT3','C') AND row_kind != 'C_MISS'").fetchone()["n"]
        similar_n = 0
        if last is not None:
            similar_n = c.execute("SELECT COUNT(*) AS n FROM lb_snapshots WHERE IFNULL(direction,'')=IFNULL(?, '') AND IFNULL(timing,'')=IFNULL(?, '') AND IFNULL(weather,'')=IFNULL(?, '') AND IFNULL(event,'')=IFNULL(?, '')", (last["direction"], last["timing"], last["weather"], last["event"])).fetchone()["n"]
    model = model_store.latest()
    signal = infer.score(hunt, weather, extra, int(time.time())) if model and model.get("artifact") else None
    if signal is not None: signal["similar_n"] = int(similar_n)
    stage = "TRAINED" if model and model.get("artifact") else "COLLECTING"
    metrics = None
    if model and model.get("metrics_json"):
        try: metrics = json.loads(model["metrics_json"]) if isinstance(model["metrics_json"], str) else model["metrics_json"]
        except Exception: metrics = None
    slice_ = None if last is None else {"row_kind": last["row_kind"], "action": last["action"], "direction": last["direction"], "event": last["event"], "timing": last["timing"], "timing_state": last["timing_state"], "weather": last["weather"], "gate": last["gate"]}
    return {"available": True, "stage": stage, "model": None if not model else {"model_id": model.get("model_id"), "kind": model.get("kind"), "n_train": model.get("n_train"), "n_test": model.get("n_test"), "metrics": metrics}, "veto": False, "size_adjust": False, "live_impact": "NONE", "counts": {"snapshots": int(n_snap), "labeled": int(n_lab), "similar": int(similar_n), "c_success": int(c_ok), "c_miss": int(c_miss), "by_kind": {r["row_kind"]: r["n"] for r in kinds}}, "last_snapshot": None if last is None else {"obs_id": last["obs_id"], "row_kind": last["row_kind"], "action": last["action"], "timing": last["timing"], "timing_state": last["timing_state"], "ts": last["ts"], "source": last["source"]}, "slice": slice_, "history": {"mean_r": None if avg["a"] is None else round(float(avg["a"]),4), "n": int(avg["n"] or 0)}, "signal": signal, "can_fire": False}
