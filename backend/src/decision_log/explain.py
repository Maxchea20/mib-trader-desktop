"""Human-readable explanation generator.

Builds a plain-English sentence from ALREADY-RECORDED evidence — never
invents a reason that wasn't actually part of the decision. If the
recorded data doesn't support a specific claim, it's left out rather
than guessed at.
"""
from typing import Dict, List


def explain_decision(final_decision: str, bias: str, agent_results: List[Dict],
                     htf_blocked: bool, htf_regime: str, rejection_stage: str = None,
                     rejection_reason: str = None) -> str:
    passing = [a for a in agent_results if a.get("direction") == bias and bias in ("LONG", "SHORT")]
    n_pass = len(passing)
    n_total = len([a for a in agent_results if a.get("direction") in ("LONG", "SHORT", "NEUTRAL")])

    if final_decision in ("LONG", "SHORT"):
        direction_word = "BUY" if final_decision == "LONG" else "SELL"
        parts = [f"{direction_word} opportunity detected because {n_pass} of {n_total} agents are aligned"]
        if not htf_blocked:
            parts.append(f"HTF regime ({htf_regime}) supports the setup")
        return ". ".join(parts) + "."

    if final_decision == "AVOID" and rejection_reason:
        return f"Setup rejected — {rejection_reason}"

    if final_decision == "WAIT":
        if htf_blocked:
            return (f"Setup rejected because HTF regime ({htf_regime}) opposes the "
                    f"{n_pass}-of-{n_total}-agent {bias} lean.")
        return f"No trade — only {n_pass} of {n_total} agents currently agree, not enough for entry yet."

    return "No significant signal at this time."