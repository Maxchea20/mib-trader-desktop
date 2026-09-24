/**
 * Shared breathing-glow / visible-chip styling.
 *
 * Originally built for the Observation Layer (Layer 3), reused here by
 * ScenarioBrainPanel/ExplainabilityPanel (Layer 2) for visual consistency
 * across the app -- same TOKEN colors and same .breathe-glow keyframe
 * either way, so the whole page pulses with one shared "alive"
 * language instead of two different glow systems.
 *
 * The app's original dirStyle()/STATE_STYLE (lib/style.js) uses very
 * dark fills (e.g. "bg-emerald-950/50") designed to sit under a large
 * glowing hero number or a full-card glow. At small badge scale with
 * no surrounding glow, that fill is nearly invisible against the
 * near-black panel background -- which is why the first Observation
 * Layer render looked flat/outline-only instead of matching the
 * approved mockup's clearly-filled chips.
 *
 * This uses the SAME real color tokens as the rest of the app
 * (--long/--short/--wait/--avoid from index.css), just at a lighter,
 * more visible alpha -- it does not introduce any new colors.
 */
const TOKEN = {
  LONG: "0,245,155",
  SHORT: "255,59,86",
  WAIT: "255,184,0",
  AVOID: "148,163,184",
  NEUTRAL: "148,163,184",
};

export function chipStyle(direction) {
  const rgb = TOKEN[direction] || TOKEN.NEUTRAL;
  return {
    background: `rgba(${rgb},0.12)`,
    borderColor: `rgba(${rgb},0.5)`,
    color: `rgb(${rgb})`,
    textShadow: `0 0 6px rgba(${rgb},0.6)`,
  };
}

/**
 * Card glow, matching the original AgentCard.js treatment but as a
 * CSS custom property (--glow-rgb) consumed by the `.observation-
 * breathe` keyframe animation in index.css, so the glow pulses
 * ("breathes") instead of sitting static. Same TOKEN colors as
 * chipStyle above -- no new colors introduced.
 */
export function cardGlow(direction) {
  const rgb = TOKEN[direction] || TOKEN.NEUTRAL;
  return { "--glow-rgb": rgb };
}