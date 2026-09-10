"""PHASE E — Conflict / severe contradiction detection.

Conflict answers: how materially does credible opposing ROLE-level
evidence contradict the current directional thesis?

Raw headcount of strong agents is diagnostic only. Two correlated
agents in the same role do not independently create a contradiction.
"""
from typing import List, Dict
from .. import settings
from ..contract import LONG, SHORT, NEUTRAL
from . import evidence as ev
from .scoring import EXCLUDE_FROM_EXECUTION


def detect_conflict(agents: List, bias: str, weights_override: Dict = None,
                     conflict_override: Dict = None) -> Dict:
    CONFLICT = conflict_override if conflict_override is not None else settings.conflict()
    AGENT_WEIGHTS = weights_override if weights_override is not None else settings.weights()
    net = ev.role_net_evidence(agents, AGENT_WEIGHTS, exclude_agents=EXCLUDE_FROM_EXECUTION)

    long_mass = net["long_role_evidence"]
    short_mass = net["short_role_evidence"]
    total = long_mass + short_mass

    opposing_roles = []
    thesis_roles = []
    if bias == LONG:
        opposing_mass, thesis_mass = short_mass, long_mass
        opposing_roles = [c for c in net["contributions"] if c["direction"] == SHORT]
        thesis_roles = [c for c in net["contributions"] if c["direction"] == LONG]
    elif bias == SHORT:
        opposing_mass, thesis_mass = long_mass, short_mass
        opposing_roles = [c for c in net["contributions"] if c["direction"] == LONG]
        thesis_roles = [c for c in net["contributions"] if c["direction"] == SHORT]
    else:
        opposing_mass = min(long_mass, short_mass)
        thesis_mass = max(long_mass, short_mass)
        if long_mass >= short_mass:
            opposing_roles = [c for c in net["contributions"] if c["direction"] == SHORT]
            thesis_roles = [c for c in net["contributions"] if c["direction"] == LONG]
        else:
            opposing_roles = [c for c in net["contributions"] if c["direction"] == LONG]
            thesis_roles = [c for c in net["contributions"] if c["direction"] == SHORT]

    if total <= 0:
        return {"conflict": False, "severity": "none", "opposing_ratio": 0.0,
                "opposing_agents": [], "penalty": 0.0, "veto": False,
                "opposing_roles": [], "n_opposing_roles": 0}

    ratio = opposing_mass / (thesis_mass + opposing_mass) if (thesis_mass + opposing_mass) else 0.0
    n_opp_roles = len(opposing_roles)

    # Agent list is diagnostic. It must NOT independently create severity.
    strong_cut = CONFLICT["strong_confidence"]
    opposing_agents = []
    for rec in opposing_roles:
        for name in rec["agents"]:
            for res in agents:
                if res.agent == name and res.valid and res.confidence >= strong_cut:
                    opposing_agents.append(name)

    veto = ratio >= CONFLICT["veto_ratio"]
    severe = (not veto) and ratio >= CONFLICT["severe_ratio"]
    # HIGH: majority of directional role-mass is opposing, but below severe.
    high = (not veto) and (not severe) and ratio >= 0.55
    # MODERATE: material role-level contradiction — either a sizable
    # opposing share of role-mass, or two+ independent opposing roles.
    # contradiction_min_agents is intentionally unused as a trigger.
    moderate = (not veto) and (not severe) and (not high) and (
        ratio >= 0.35 or n_opp_roles >= 2
    )
    low = (not veto) and (not severe) and (not high) and (not moderate) and (
        n_opp_roles >= 1 or ratio >= 0.15
    )

    if veto:
        severity = "veto"
        penalty = CONFLICT["confidence_penalty"] * 2
    elif severe:
        severity = "severe"
        penalty = CONFLICT["confidence_penalty"] * 1.5
    elif high:
        severity = "high"
        penalty = CONFLICT["confidence_penalty"]
    elif moderate:
        severity = "moderate"
        penalty = CONFLICT["confidence_penalty"]
    elif low:
        severity = "low"
        penalty = CONFLICT["confidence_penalty"] * 0.5
    else:
        severity = "none"
        penalty = 0.0

    return {
        "conflict": severity not in ("none", "low"),
        "severity": severity,
        "opposing_ratio": round(ratio, 3),
        "opposing_agents": opposing_agents,
        "penalty": round(penalty, 1),
        "veto": veto,
        "opposing_roles": [r["role"] for r in opposing_roles],
        "thesis_roles": [r["role"] for r in thesis_roles],
        "n_opposing_roles": n_opp_roles,
    }
