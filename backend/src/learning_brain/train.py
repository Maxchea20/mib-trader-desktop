"""Offline training only."""
from __future__ import annotations
import math, time
from . import dataset, features as featmod, model_store
from .isolation import fail_open

def _dot(w,x): return sum(a*b for a,b in zip(w,x))
def _sigmoid(z):
    if z<-30: return 0.0
    if z>30: return 1.0
    return 1.0/(1.0+math.exp(-z))

def _fit_logistic(X,y,steps=200,lr=0.05):
    if not X: return [0.0]
    d=len(X[0]); w=[0.0]*(d+1); n=max(1,len(X))
    for _ in range(steps):
        gw=[0.0]*(d+1)
        for xi,yi in zip(X,y):
            err=_sigmoid(w[0]+_dot(w[1:],xi))-yi
            gw[0]+=err
            for j,v in enumerate(xi): gw[j+1]+=err*v
        for j in range(d+1): w[j]-=lr*gw[j]/n
    return w

def _fit_ridge(X,y):
    if not X: return [0.0]
    d=len(X[0]); w=[0.0]*(d+1); n=max(1,len(X)); w[0]=sum(y)/n
    for _ in range(80):
        gw=[0.0]*(d+1)
        for xi,yi in zip(X,y):
            err=(w[0]+_dot(w[1:],xi))-yi
            gw[0]+=err
            for j,v in enumerate(xi): gw[j+1]+=err*v
        for j in range(d+1): w[j]-=0.01*(gw[j]/n+0.01*w[j])
    return w

@fail_open({"ok": False, "error": "train_failed"})
def run():
    rows=dataset.labeled_rows(); train,test=dataset.split(rows)
    if len(train)<12:
        return {"ok": False, "error": "not_enough_labeled", "n_labeled": len(rows), "n_train": len(train), "need": 12}
    Xtr,yw,yr=dataset.xy(train)
    w_cls=_fit_logistic(Xtr,yw); w_reg=_fit_ridge(Xtr,yr)
    metrics={"n_labeled": len(rows), "n_train": len(train), "n_test": len(test)}
    if test:
        Xt,ytw,ytr=dataset.xy(test); hits=0; se=0.0
        for xi,yi,ri in zip(Xt,ytw,ytr):
            p=_sigmoid(w_cls[0]+_dot(w_cls[1:],xi))
            if (p>=0.5 and yi>=0.5) or (p<0.5 and yi<0.5): hits+=1
            se+=(w_reg[0]+_dot(w_reg[1:],xi)-ri)**2
        metrics["test_acc"]=hits/max(1,len(Xt)); metrics["test_mse_r"]=se/max(1,len(Xt))
    kind="baseline_logistic"
    payload={"kind": kind, "feature_keys": featmod.FEATURE_KEYS, "w_cls": w_cls, "w_reg": w_reg, "hunt_version": "OBSERVATION_HUNT_M5_C_FI"}
    model_id=f"LB-{int(time.time())}-{kind}"
    model_store.save(model_id, payload, metrics, kind, len(train), len(test))
    return {"ok": True, "model_id": model_id, "metrics": metrics, "kind": kind}
