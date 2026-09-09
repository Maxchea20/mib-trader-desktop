/**
 * Agent message adapter.
 *
 * Converts the backend's REAL agent.evidence array into one concise,
 * readable sentence for the card's live ticker. This never computes or
 * invents anything — it only selects and lightly joins text the backend
 * already produced, per the "no fake UI data" requirement.
 *
 * Heuristic: several agents (Market Structure, Breakout, Trend) also
 * produce dense, pipe-separated technical summary lines alongside their
 * more natural-language evidence — e.g.
 *   "Structure sequence: HH_LL | Regime: EXPANSION | ..."
 * versus
 *   "Higher High + Lower Low → structural expansion"
 * This prefers the natural-language style lines for the ticker (the
 * technical summary remains fully visible in the existing detail modal
 * on click — nothing is hidden, just not duplicated on every card).
 */
function looksTechnical(line) {
  if (line.includes("|")) return true;
  // "Label: 1.05 ATR → 13.9/20" style per-factor score lines.
  if (/→\s*[\d.]+\s*\/\s*\d/.test(line)) return true;
  // Lines that are mostly "Label: number" key/value dumps.
  const kvMatches = line.match(/[A-Za-z ]+:\s*[+\-]?[\d.]/g) || [];
  return kvMatches.length >= 2;
}

export function formatAgentMessage(agent) {
  const evidence = agent?.evidence || [];
  if (evidence.length === 0) {
    return agent?.direction === "NEUTRAL"
      ? "No current reading from this agent."
      : "Awaiting evidence.";
  }

  const natural = evidence.filter((line) => !looksTechnical(line));
  const candidates = natural.length > 0 ? natural : evidence;

  const picked = candidates.slice(0, 2);
  let message = picked.join(". ").trim();
  if (message && !/[.!?]$/.test(message)) message += ".";

  if (message.length > 170) message = message.slice(0, 167) + "...";
  return message || evidence[0];
}