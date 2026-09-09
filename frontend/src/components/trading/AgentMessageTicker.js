import React from "react";
import { ChevronsRight } from "lucide-react";

/**
 * AgentMessageTicker
 *
 * Right-to-left scrolling display of the agent's real, backend-derived
 * message (see lib/agentMessages.js — this component receives an
 * already-computed string, it does not touch evidence itself).
 *
 * Pure CSS animation (transform: translateX via @keyframes, defined in
 * index.css) — no per-frame JS, no requestAnimationFrame loop, no DOM
 * measurement. The standard seamless-marquee trick: two identical
 * copies of the text back-to-back, animated by exactly -50% of the
 * track width, looping invisibly. Pauses on hover via CSS
 * `animation-play-state`, not a JS timer.
 *
 * The message itself only updates when the parent re-renders with a
 * genuinely new `message` prop (i.e. when the real agent result
 * changes) — the animation loops continuously, but the CONTENT does
 * not get recomputed by the animation itself.
 */
export const AgentMessageTicker = ({ message, accentColor = "#5b9bd5" }) => {
  return (
    <div className="agent-ticker-mask" style={{ borderColor: `${accentColor}2a` }} data-testid="agent-ticker">
      <div className="agent-ticker-track">
        <span className="agent-ticker-text" style={{ color: accentColor }}>{message}</span>
        <span className="agent-ticker-text" style={{ color: accentColor }} aria-hidden="true">{message}</span>
      </div>
      <ChevronsRight className="agent-ticker-icon" style={{ color: accentColor }} />
    </div>
  );
};