import React from "react";

/** Hunt map sits on the prior 15m candle only — high stub and low stub. */
export const HuntMapOverlayLayer = ({ lines }) => (
  <div className="absolute inset-0 pointer-events-none overflow-hidden" data-testid="hunt-map-overlays">
    {lines.map((l) => (
      <div key={l.key}>
        <div
          className="absolute"
          style={{
            left: `${l.left}px`,
            top: `${l.top}px`,
            width: `${l.width}px`,
            height: 0,
            borderTop: `${l.focus ? 3 : 2}px solid ${l.color}`,
            opacity: l.focus ? 1 : 0.85,
            zIndex: 7,
          }}
        />
        <span
          className="absolute font-mono-t font-bold leading-none whitespace-nowrap"
          style={{
            left: `${l.left + l.width + 4}px`,
            top: `${l.top - 6}px`,
            color: l.color,
            fontSize: 10,
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
