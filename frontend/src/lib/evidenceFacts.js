/**
 * Evidence fact parser.
 *
 * Turns the backend's REAL agent.evidence[] strings into structured
 * {label, value} fact rows for display. This never computes or invents
 * a number — it only reformats text the backend already produced,
 * same "no fake UI data" principle as agentMessages.js.
 *
 * Two real, already-established evidence conventions exist across the
 * agents (confirmed directly against backend source):
 *   1. Pipe-delimited multi-fact lines, e.g. (structure/__init__.py):
 *      "Structure sequence: HH_HL | Regime: EXPANSION | Latest event: BOS"
 *   2. Scored single-fact lines, e.g. (breakout/__init__.py):
 *      "Level penetration: 0.42 ATR → 13.9/25"
 * This parses both into a flat, capped list of fact rows so any agent's
 * evidence can be shown as facts without per-agent hardcoding.
 */

const SCORED_LINE = /^(.*?)\s*→\s*([\d.]+)\s*\/\s*([\d.]+)$/;
const KV_SEGMENT = /^([^:]{2,40}):\s*(.+)$/;

function parseSegment(segment) {
  const scored = segment.match(SCORED_LINE);
  if (scored) {
    const [, label, score, max] = scored;
    return { label: label.trim(), value: `${score}/${max} pts` };
  }
  const kv = segment.match(KV_SEGMENT);
  if (kv) {
    const [, label, value] = kv;
    return { label: label.trim(), value: value.trim() };
  }
  return null;
}

export function parseEvidenceFacts(evidence, maxRows = 6) {
  const rows = [];
  for (const line of evidence || []) {
    if (rows.length >= maxRows) break;
    const segments = line.includes(" | ") ? line.split(" | ") : [line];
    for (const seg of segments) {
      if (rows.length >= maxRows) break;
      const parsed = parseSegment(seg.trim());
      if (parsed) rows.push(parsed);
    }
  }
  return rows;
}

/**
 * The first non-technical, non-key-value evidence line — a short
 * narrative sentence rather than a fact row. Reuses the same
 * "looksTechnical" heuristic already established in agentMessages.js
 * would use; duplicated narrowly here to keep this module dependency-
 * free (no functional difference from that file's own filter).
 */
export function firstNarrativeLine(evidence) {
  for (const line of evidence || []) {
    if (line.includes("|")) continue;
    if (/→\s*[\d.]+\s*\/\s*\d/.test(line)) continue;
    const kvMatches = line.match(/[A-Za-z ]+:\s*[+\-]?[\d.]/g) || [];
    if (kvMatches.length >= 2) continue;
    return line;
  }
  return (evidence || [])[0] || "";
}