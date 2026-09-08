import React from "react";

/**
 * FvgOverlayLayer
 *
 * Pure rendering of already-computed FVG "bands" (screen-pixel rectangles
 * with color/label), as absolutely-positioned divs over the chart.
 *
 * Deliberately knows nothing about candles, timestamps, or lightweight-
 * charts internals — that's useFvgOverlay's job. This component only
 * knows "given a list of {left, top, width, height, color, borderColor,
 * label}, draw labeled boxes." Extracted verbatim from CandleChart.js's
 * inline JSX, same markup and classes, just relocated.
 */
export const FvgOverlayLayer = ({ bands }) => (
  <div className="absolute inset-0 pointer-events-none overflow-hidden" data-testid="chart-overlays">
    {bands.map((b) => (
      <div
        key={b.key}
        className="absolute"
        style={{
          left: `${b.left}px`,
          top: `${b.top}px`,
          width: `${b.width}px`,
          height: `${b.height}px`,
          background: b.color,
          borderTop: `1px solid ${b.borderColor}`,
          borderBottom: `1px solid ${b.borderColor}`,
          zIndex: 5,
        }}
      >
        <span
          className="absolute left-1 top-0 font-mono-t text-[8px] leading-none px-1 py-0.5"
          style={{ color: b.borderColor }}
        >
          {b.label}
        </span>
      </div>
    ))}
  </div>
);