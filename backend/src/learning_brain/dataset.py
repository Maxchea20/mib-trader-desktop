"""Build train/test matrices with time split + embargo. Labeled rows only."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import features as featmod
from .schema import conn, init


def labeled_rows() -> List[Dict]:
    init()
    with conn() as c:
        rows = c.execute(
            """SELECT s.obs_id, s.ts, s.features_json, s.row_kind, s.hunt_version,
                      o.r_multiple, o.filled
               FROM lb_snapshots s
               JOIN lb_outcomes o ON o.obs_id = s.obs_id
               WHERE s.label_status='LABELED' AND o.r_multiple IS NOT NULL
               ORDER BY s.ts ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def split(rows: List[Dict], embargo_sec: int = 86400) -> Tuple[List[Dict], List[Dict]]:
    if len(rows) < 8:
        return rows, []
    cut = rows[int(len(rows) * 0.7)]["ts"]
    train = [r for r in rows if r["ts"] < cut]
    test = [r for r in rows if r["ts"] >= cut + embargo_sec]
    return train, test


def xy(rows: List[Dict]) -> Tuple[List[List[float]], List[float], List[float]]:
    X, y_win, y_r = [], [], []
    for r in rows:
        feat = featmod.loads(r.get("features_json"))
        X.append(featmod.vector(feat))
        rm = float(r.get("r_multiple") or 0)
        y_r.append(rm)
        y_win.append(1.0 if rm > 0 else 0.0)
    return X, y_win, y_r
