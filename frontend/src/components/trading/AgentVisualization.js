import React from "react";

/**
 * AgentVisualization
 *
 * Lightweight, direction-aware SVG micro-visualization per agent, per
 * the suggested visual language in the design spec. Pure CSS/SVG, no
 * images, no canvas loop — animation is CSS keyframes only, so this
 * costs nothing per-frame in JS and is GPU-friendly.
 *
 * These are decorative — they reflect the agent's CURRENT direction
 * (color) but do not encode any additional real data beyond what the
 * card already shows via confidence/strength/message. Nothing here is
 * a duplicate calculation of any trading logic.
 */
const COLORS = {
  LONG: "#00f59b",
  SHORT: "#ff3b56",
  NEUTRAL: "#5b9bd5",
};

const colorFor = (direction) => COLORS[direction] || COLORS.NEUTRAL;

function MarketStructureViz({ direction }) {
  const c = colorFor(direction);
  const up = direction === "LONG";
  const points = up ? "2,38 14,22 26,28 38,10 50,16 62,4" : "2,4 14,20 26,14 38,32 50,26 62,38";
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      <polyline points={points} fill="none" stroke={c} strokeWidth="1.4" opacity="0.85" />
      {points.split(" ").map((p, i) => {
        const [x, y] = p.split(",");
        return <circle key={i} cx={x} cy={y} r="2" fill={c} className="agent-viz-pulse" style={{ animationDelay: `${i * 0.15}s` }} />;
      })}
    </svg>
  );
}

function BreakoutViz({ direction }) {
  const c = colorFor(direction);
  const active = direction !== "NEUTRAL";
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      <line x1="2" y1="21" x2="62" y2="21" stroke={c} strokeWidth="1" strokeDasharray="3 2" opacity="0.6" />
      {active && (
        <rect x="30" y={direction === "LONG" ? 4 : 21} width="6" height="17" fill={c} opacity="0.35" className="agent-viz-beam" />
      )}
      <circle cx={active ? 34 : 20} cy={active ? (direction === "LONG" ? 6 : 36) : 21} r="2.2" fill={c} />
    </svg>
  );
}

function FibonacciViz({ direction }) {
  const c = colorFor(direction);
  const levels = [6, 14, 21, 28, 36];
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      {levels.map((y, i) => (
        <line key={i} x1="4" y1={y} x2="60" y2={y} stroke={c} strokeWidth="0.8" opacity={0.3 + i * 0.12} />
      ))}
      <circle cx="32" cy={levels[2]} r="2" fill={c} className="agent-viz-pulse" />
    </svg>
  );
}

function ElliottWaveViz({ direction }) {
  const c = colorFor(direction);
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      <path
        d="M2,30 L12,12 L22,24 L32,6 L42,26 L52,16 L62,30"
        fill="none" stroke={c} strokeWidth="1.4" opacity="0.85"
        className="agent-viz-wave"
      />
    </svg>
  );
}

function VolumeViz({ direction }) {
  const c = colorFor(direction);
  const bars = [10, 22, 14, 30, 18, 26, 12];
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      {bars.map((h, i) => (
        <rect key={i} x={4 + i * 8.5} y={38 - h} width="5" height={h} fill={c}
          opacity={0.4 + (i / bars.length) * 0.5} className="agent-viz-bar" style={{ animationDelay: `${i * 0.08}s` }} />
      ))}
    </svg>
  );
}

function MomentumViz({ direction }) {
  const c = colorFor(direction);
  const angle = direction === "LONG" ? -50 : direction === "SHORT" ? 50 : 0;
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      <circle cx="32" cy="24" r="14" fill="none" stroke={c} strokeWidth="1" opacity="0.3" />
      <line x1="32" y1="24" x2={32 + 12 * Math.sin((angle * Math.PI) / 180)} y2={24 - 12 * Math.cos((angle * Math.PI) / 180)}
        stroke={c} strokeWidth="1.6" className="agent-viz-needle" />
      <circle cx="32" cy="24" r="1.6" fill={c} />
    </svg>
  );
}

function SupportResistanceViz({ direction }) {
  const c = colorFor(direction);
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      {[8, 18, 28, 38].map((y, i) => (
        <rect key={i} x="4" y={y} width="56" height="4" fill={c} opacity={i === 1 || i === 2 ? 0.55 : 0.2} />
      ))}
    </svg>
  );
}

function TrendViz({ direction }) {
  const c = colorFor(direction);
  const up = direction === "LONG";
  const rows = [0, 1, 2];
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      {rows.map((r) => {
        const base = up ? 34 - r * 6 : 8 + r * 6;
        const end = up ? base - 14 : base + 14;
        return (
          <path key={r} d={`M2,${base} Q32,${(base + end) / 2} 62,${end}`}
            fill="none" stroke={c} strokeWidth="1.1" opacity={0.85 - r * 0.22} />
        );
      })}
    </svg>
  );
}

function PatternViz({ direction }) {
  const c = colorFor(direction);
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      <polygon points="8,34 32,8 56,34" fill="none" stroke={c} strokeWidth="1.3" opacity="0.8" />
      <circle cx="32" cy="8" r="2" fill={c} className="agent-viz-pulse" />
    </svg>
  );
}

function FairValueGapViz({ direction }) {
  const c = colorFor(direction);
  return (
    <svg viewBox="0 0 64 42" className="agent-viz-svg">
      <rect x="8" y="10" width="48" height="8" fill={c} opacity="0.25" />
      <rect x="8" y="24" width="48" height="8" fill={c} opacity="0.4" />
      <line x1="8" y1="10" x2="56" y2="10" stroke={c} strokeWidth="0.8" opacity="0.6" />
      <line x1="8" y1="32" x2="56" y2="32" stroke={c} strokeWidth="0.8" opacity="0.6" />
    </svg>
  );
}

const VIZ_BY_AGENT = {
  market_structure: MarketStructureViz,
  breakout: BreakoutViz,
  fibonacci: FibonacciViz,
  elliott_wave: ElliottWaveViz,
  volume: VolumeViz,
  momentum: MomentumViz,
  support_resistance: SupportResistanceViz,
  trend: TrendViz,
  pattern: PatternViz,
  fair_value_gap: FairValueGapViz,
};

export const AgentVisualization = ({ agentId, direction }) => {
  const Viz = VIZ_BY_AGENT[agentId];
  if (!Viz) return null;
  return (
    <div className="agent-viz-wrap" data-testid={`agent-viz-${agentId}`}>
      <Viz direction={direction} />
    </div>
  );
};