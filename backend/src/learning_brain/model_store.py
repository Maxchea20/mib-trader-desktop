"""Versioned model artifacts."""
from __future__ import annotations
import json, os, time
from pathlib import Path
from ..market_data import database as mdb
from .schema import conn, init

def artifact_dir():
    p = Path(mdb.db_path()).resolve().parent / "learning_models"
    p.mkdir(parents=True, exist_ok=True)
    return p

def save(model_id, payload, metrics, kind, n_train, n_test):
    init()
    path = artifact_dir() / f"{model_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO lb_models (model_id, trained_at, hunt_version, kind, metrics_json, artifact_path, role, n_train, n_test) VALUES (?,?,?,?,?,?,?,?,?)", (model_id, int(time.time()), payload.get("hunt_version"), kind, json.dumps(metrics), str(path), "candidate", n_train, n_test))
        c.commit()
    return str(path)

def latest():
    init()
    with conn() as c:
        row = c.execute("SELECT * FROM lb_models ORDER BY trained_at DESC LIMIT 1").fetchone()
    if not row: return None
    d = dict(row)
    path = d.get("artifact_path")
    d["artifact"] = None
    if path and os.path.isfile(path):
        try: d["artifact"] = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception: pass
    return d

def list_models():
    init()
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lb_models ORDER BY trained_at DESC LIMIT 20").fetchall()]
