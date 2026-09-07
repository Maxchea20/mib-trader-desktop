"""Per-engine performance attribution — ported from mib-gold-MT5's
backend/mibgold/backtest/attribution.py, adapted to this project's trade
schema (field names differ, engines are passed in rather than imported
from a fixed weights dict, since mib-trader-desktop doesn't have
per-engine weights the way mib-gold does yet).

The core idea, unchanged from the original: for each of the 10 engines,
look at every closed backtest trade and ask two things —
  1. When this engine agreed with the trade's direction, how often did
     that trade turn out to be a winner vs a loser? (agreement_on_wins
     vs agreement_on_losses)
  2. When this engine was specifically the *strongest* voice behind an
     entry, what was the win rate of those trades?

The gap between agreement-on-wins and agreement-on-losses ("edge") is
what actually tells you whether an engine adds value: an engine that
agrees with winners and losers at the same rate is just noise, no matter
how often it fires.
"""
from typing import Callable, Dict, List, Optional


def attribution(trades: List[dict], weights: Dict[str, float],
                 engine_ids: List[str], display_name: Callable[[str], str]) -> Dict[str, dict]:
    closed = [t for t in trades if t.get("status") == "closed"]
    out = {}
    for key in engine_ids:
        strongest = [t for t in closed if t.get("strongest_engine") == key]
        strongest_wins = sum(1 for t in strongest if t["outcome"] == "win")
        wins = [t for t in closed if t["outcome"] == "win"]
        losses = [t for t in closed if t["outcome"] == "loss"]
        agree_w = [t for t in wins if t["agent_votes"].get(key, {}).get("direction") == t["direction"]]
        agree_l = [t for t in losses if t["agent_votes"].get(key, {}).get("direction") == t["direction"]]
        conf_w = [t["agent_votes"][key]["confidence"] for t in agree_w if key in t["agent_votes"]]
        conf_l = [t["agent_votes"][key]["confidence"] for t in agree_l if key in t["agent_votes"]]
        neutral = sum(1 for t in closed if t["agent_votes"].get(key, {}).get("direction") == "NEUTRAL")

        agr_w = len(agree_w) / len(wins) if wins else 0.0
        agr_l = len(agree_l) / len(losses) if losses else 0.0
        edge = agr_w - agr_l

        if len(closed) < 10:
            rec = "insufficient data"
        elif edge > 0.15:
            rec = "upweight"
        elif edge > -0.05:
            rec = "keep"
        elif edge > -0.2:
            rec = "downweight"
        else:
            rec = "drop"

        out[key] = {
            "engine": display_name(key), "weight": weights.get(key, 1.0),
            "strongest_count": len(strongest),
            "strongest_win_rate": round(strongest_wins / len(strongest), 4) if strongest else None,
            "agreement_on_wins": round(agr_w, 4), "agreement_on_losses": round(agr_l, 4),
            "edge": round(edge, 4),
            "neutral_rate": round(neutral / len(closed), 4) if closed else 0.0,
            "avg_conf_wins": round(sum(conf_w) / len(conf_w), 1) if conf_w else None,
            "avg_conf_losses": round(sum(conf_l) / len(conf_l), 1) if conf_l else None,
            "recommendation": rec,
        }
    return out


def summary_stats(trades: List[dict], start_balance: float) -> dict:
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = [t for t in closed if t["outcome"] == "win"]
    losses = [t for t in closed if t["outcome"] == "loss"]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    net = sum(t["pnl"] for t in closed)
    return {
        "trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "avg_r": round(sum(t["r_multiple"] for t in closed) / len(closed), 3) if closed else 0.0,
        "net_pnl": round(net, 2),
        "return_pct": round(net / start_balance * 100, 2) if start_balance else 0.0,
        "by_exit": {r: sum(1 for t in closed if t["exit_reason"] == r) for r in {t["exit_reason"] for t in closed}},
    }