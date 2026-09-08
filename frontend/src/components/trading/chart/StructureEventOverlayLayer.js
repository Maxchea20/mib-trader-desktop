import React from "react";

/**
 * StructureEventOverlayLayer
 *
 * Pure rendering of already-computed BOS/CHoCH line segments (from
 * useStructureEventOverlay) as a horizontal line at the broken price
 * level, with the event label sitting at the right end — right where
 * the break actually happened. Knows nothing about candles, timestamps,
 * or swing_index lookups; that's useStructureEventOverlay's job.
 */
export const StructureEventOverlayLayer = ({ lines }) => (
  <div className="absolute inset-0 pointer-events-none overflow-hidden" data-testid="structure-event-overlays">
    {lines.map((l) => (
      <div key={l.key}>
        <div
          className="absolute"
          style={{
            left: `${l.left}px`,
            top: `${l.top}px`,
            width: `${l.width}px`,
            height: "0px",
            borderTop: `1px dashed ${l.color}`,
            zIndex: 6,
          }}
        />
        <span
          className="absolute font-mono-t text-[9px] font-semibold leading-none whitespace-nowrap"
          style={{
            left: `${l.left + l.width - 2}px`,
            top: `${l.top - 12}px`,
            transform: "translateX(-100%)",
            color: l.color,
            textShadow: "0 0 3px rgba(0,0,0,0.9), 0 0 3px rgba(0,0,0,0.9)",
            zIndex: 6,
          }}
        >
          {l.label}
        </span>
      </div>
    ))}
  </div>
);