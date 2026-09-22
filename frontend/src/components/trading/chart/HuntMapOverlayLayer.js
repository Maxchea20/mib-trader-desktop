import React from "react";

/** Full-width Hunt map lines + arrows so the 15m high/low is obvious. */
export const HuntMapOverlayLayer = ({ lines }) => (
  <div className="absolute inset-0 pointer-events-none overflow-hidden" data-testid="hunt-map-overlays">
    {lines.map((l) => (
      <div key={l.key}>
        <div
          className="absolute"
          style={{
            left: 0,
            top: `${l.top}px`,
            width: `${l.width}px`,
            height: 0,
            borderTop: `${l.focus ? 2 : 1}px ${l.focus ? "solid" : "dashed"} ${l.color}`,
            opacity: l.focus ? 1 : 0.7,
            zIndex: 7,
          }}
        />
        <span
          className="absolute font-mono-t font-bold leading-none whitespace-nowrap"
          style={{
            left: 8,
            top: `${l.top - 13}px`,
            color: l.color,
            fontSize: l.focus ? 11 : 9,
            textShadow: "0 0 4px rgba(0,0,0,0.95), 0 0 4px rgba(0,0,0,0.95)",
            zIndex: 8,
          }}
        >
          {l.label}
        </span>
      </div>
    ))}
  </div>
);
