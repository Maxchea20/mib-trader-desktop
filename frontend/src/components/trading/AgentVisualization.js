import React from "react";

/**
 * AgentVisualization
 *
 * Decorative, direction-aware visualizations only.
 * No trading calculations are performed here.
 *
 * Market Structure LONG now uses a dense holographic bull-head mesh.
 * All other existing visualizations remain unchanged.
 */

const COLORS = {
  LONG: "#00f59b",
  SHORT: "#ff3b56",
  NEUTRAL: "#5b9bd5",
};

const colorFor = (direction) => COLORS[direction] || COLORS.NEUTRAL;

/* ================================================================
   MARKET STRUCTURE — LONG: HOLOGRAPHIC BULL HEAD
   ================================================================ */

function BullHeadViz({ direction }) {
  const c = colorFor(direction);

  return (
    <svg
      viewBox="0 0 260 190"
      className="agent-viz-svg agent-viz-bull-head"
      preserveAspectRatio="xMidYMid meet"
      aria-label="Bull hologram"
      role="img"
    >
      <defs>
        <filter
          id="bullGlow"
          x="-30%"
          y="-30%"
          width="160%"
          height="160%"
        >
          <feGaussianBlur
            stdDeviation="2.2"
            result="blur"
          />

          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        <radialGradient
          id="bullAura"
          cx="50%"
          cy="45%"
          r="58%"
        >
          <stop
            offset="0%"
            stopColor={c}
            stopOpacity="0.18"
          />

          <stop
            offset="58%"
            stopColor={c}
            stopOpacity="0.06"
          />

          <stop
            offset="100%"
            stopColor={c}
            stopOpacity="0"
          />
        </radialGradient>
      </defs>

      {/* =========================================================
          SOFT HOLOGRAPHIC ATMOSPHERE
          ========================================================= */}

      <ellipse
        cx="130"
        cy="92"
        rx="112"
        ry="82"
        fill="url(#bullAura)"
        className="agent-viz-hologram-pulse"
      />

      {/* =========================================================
          OUTER SCAN CONTOUR
          ========================================================= */}

      <path
        d="
          M49 57
          C61 32 88 18 112 17
          C124 11 136 11 148 17
          C173 18 199 32 212 57
          C222 76 225 98 219 118
          C211 143 190 160 166 170
          C151 176 139 178 130 179
          C121 178 109 176 94 170
          C70 160 49 143 41 118
          C35 98 38 76 49 57Z
        "
        fill="none"
        stroke={c}
        strokeWidth="1"
        opacity="0.22"
      />

      {/* =========================================================
          HORNS — MULTIPLE CURVED SCAN LINES
          ========================================================= */}

      <g
        fill="none"
        stroke={c}
        strokeLinecap="round"
        filter="url(#bullGlow)"
      >
        {/* Left horn */}
        <path
          d="M91 53 C69 48 48 32 42 10 C60 19 79 25 100 29"
          strokeWidth="1.8"
          opacity="0.9"
        />

        <path
          d="M88 57 C65 52 43 39 36 19 C55 28 75 32 97 35"
          strokeWidth="0.8"
          opacity="0.52"
        />

        <path
          d="M86 61 C61 58 40 46 31 28 C50 36 70 40 94 42"
          strokeWidth="0.55"
          opacity="0.32"
        />

        {/* Right horn */}
        <path
          d="M169 53 C191 48 212 32 218 10 C200 19 181 25 160 29"
          strokeWidth="1.8"
          opacity="0.9"
        />

        <path
          d="M172 57 C195 52 217 39 224 19 C205 28 185 32 163 35"
          strokeWidth="0.8"
          opacity="0.52"
        />

        <path
          d="M174 61 C199 58 220 46 229 28 C210 36 190 40 166 42"
          strokeWidth="0.55"
          opacity="0.32"
        />
      </g>

      {/* =========================================================
          MAIN CONTINUOUS HEAD SURFACE
          ========================================================= */}

      <path
        d="
          M77 51
          C91 35 109 27 130 27
          C151 27 169 35 183 51
          C197 68 202 87 198 108
          C194 130 180 150 158 162
          C147 168 138 172 130 174
          C122 172 113 168 102 162
          C80 150 66 130 62 108
          C58 87 63 68 77 51Z
        "
        fill="none"
        stroke={c}
        strokeWidth="1.25"
        opacity="0.72"
      />

      {/* =========================================================
          DENSE HORIZONTAL SURFACE SCAN
          ========================================================= */}

      <g
        fill="none"
        stroke={c}
        strokeLinecap="round"
      >
        <path
          d="M79 51 C99 43 161 43 181 51"
          strokeWidth="0.7"
          opacity="0.36"
        />

        <path
          d="M70 62 C93 53 167 53 190 62"
          strokeWidth="0.72"
          opacity="0.42"
        />

        <path
          d="M65 73 C89 64 171 64 195 73"
          strokeWidth="0.78"
          opacity="0.48"
        />

        <path
          d="M62 84 C86 76 174 76 198 84"
          strokeWidth="0.85"
          opacity="0.56"
        />

        <path
          d="M61 96 C84 89 176 89 199 96"
          strokeWidth="0.9"
          opacity="0.62"
        />

        <path
          d="M61 108 C85 103 175 103 199 108"
          strokeWidth="0.92"
          opacity="0.65"
        />

        <path
          d="M66 120 C89 117 171 117 194 120"
          strokeWidth="0.88"
          opacity="0.6"
        />

        <path
          d="M72 132 C94 131 166 131 188 132"
          strokeWidth="0.8"
          opacity="0.54"
        />

        <path
          d="M82 143 C102 145 158 145 178 143"
          strokeWidth="0.72"
          opacity="0.46"
        />

        <path
          d="M96 153 C109 157 151 157 164 153"
          strokeWidth="0.65"
          opacity="0.36"
        />
      </g>

      {/* =========================================================
          DENSE VERTICAL / LONGITUDE MESH
          ========================================================= */}

      <g
        fill="none"
        stroke={c}
        strokeLinecap="round"
      >
        <path
          d="M103 31 C91 54 88 83 92 111 C95 137 105 157 116 169"
          strokeWidth="0.68"
          opacity="0.44"
        />

        <path
          d="M113 29 C103 54 101 84 104 113 C106 139 113 158 122 171"
          strokeWidth="0.76"
          opacity="0.54"
        />

        <path
          d="M122 27 C117 54 116 84 117 113 C118 141 122 160 126 172"
          strokeWidth="0.88"
          opacity="0.64"
        />

        <path
          d="M130 27 C130 55 130 84 130 113 C130 141 130 160 130 174"
          strokeWidth="1.05"
          opacity="0.78"
        />

        <path
          d="M138 27 C143 54 144 84 143 113 C142 141 138 160 134 172"
          strokeWidth="0.88"
          opacity="0.64"
        />

        <path
          d="M147 29 C157 54 159 84 156 113 C154 139 147 158 138 171"
          strokeWidth="0.76"
          opacity="0.54"
        />

        <path
          d="M157 31 C169 54 172 83 168 111 C165 137 155 157 144 169"
          strokeWidth="0.68"
          opacity="0.44"
        />
      </g>

      {/* =========================================================
          FOREHEAD SURFACE
          ========================================================= */}

      <g fill="none" stroke={c}>
        <path
          d="M91 49 C108 39 116 36 130 36 C144 36 152 39 169 49"
          strokeWidth="0.72"
          opacity="0.52"
        />

        <path
          d="M86 57 C104 48 118 45 130 45 C142 45 156 48 174 57"
          strokeWidth="0.65"
          opacity="0.42"
        />

        <path
          d="M82 67 C101 59 115 55 130 55 C145 55 159 59 178 67"
          strokeWidth="0.58"
          opacity="0.34"
        />
      </g>

      {/* =========================================================
          EYE SOCKET MESH
          ========================================================= */}

      <g
        fill="none"
        stroke={c}
        strokeLinecap="round"
      >
        <path
          d="M78 78 C89 69 104 68 117 76 C108 86 91 89 78 78Z"
          strokeWidth="1"
          opacity="0.72"
        />

        <path
          d="M182 78 C171 69 156 68 143 76 C152 86 169 89 182 78Z"
          strokeWidth="1"
          opacity="0.72"
        />

        <path
          d="M84 78 C94 74 104 75 111 79"
          strokeWidth="0.62"
          opacity="0.46"
        />

        <path
          d="M176 78 C166 74 156 75 149 79"
          strokeWidth="0.62"
          opacity="0.46"
        />
      </g>

      {/* =========================================================
          EYES
          ========================================================= */}

      <g fill={c} filter="url(#bullGlow)">
        <circle
          cx="101"
          cy="79"
          r="2"
          className="agent-viz-pulse"
        />

        <circle
          cx="159"
          cy="79"
          r="2"
          className="agent-viz-pulse"
          style={{ animationDelay: "0.25s" }}
        />
      </g>

      {/* =========================================================
          NOSE / MUZZLE
          ========================================================= */}

      <g
        fill="none"
        stroke={c}
        strokeLinecap="round"
      >
        <path
          d="M117 76 C114 91 114 106 120 119"
          strokeWidth="0.8"
          opacity="0.48"
        />

        <path
          d="M143 76 C146 91 146 106 140 119"
          strokeWidth="0.8"
          opacity="0.48"
        />

        <path
          d="M120 119 C124 114 136 114 140 119"
          strokeWidth="0.95"
          opacity="0.66"
        />

        <path
          d="M111 126 C116 119 144 119 149 126 C145 139 115 139 111 126Z"
          strokeWidth="1"
          opacity="0.76"
        />

        <path
          d="M118 128 C122 125 126 126 128 129"
          strokeWidth="0.68"
          opacity="0.54"
        />

        <path
          d="M142 128 C138 125 134 126 132 129"
          strokeWidth="0.68"
          opacity="0.54"
        />

        <path
          d="M113 143 C124 149 136 149 147 143"
          strokeWidth="0.82"
          opacity="0.52"
        />
      </g>

      {/* =========================================================
          CHEEK / JAW SURFACE
          ========================================================= */}

      <g fill="none" stroke={c}>
        <path
          d="M67 91 C78 101 86 111 91 128 C96 144 105 154 117 162"
          strokeWidth="0.68"
          opacity="0.4"
        />

        <path
          d="M193 91 C182 101 174 111 169 128 C164 144 155 154 143 162"
          strokeWidth="0.68"
          opacity="0.4"
        />

        <path
          d="M72 103 C87 112 96 124 101 141"
          strokeWidth="0.6"
          opacity="0.34"
        />

        <path
          d="M188 103 C173 112 164 124 159 141"
          strokeWidth="0.6"
          opacity="0.34"
        />
      </g>

      {/* =========================================================
          HORN CONNECTION MESH
          ========================================================= */}

      <g fill="none" stroke={c}>
        <path
          d="M87 45 C78 38 69 33 57 29"
          strokeWidth="0.68"
          opacity="0.42"
        />

        <path
          d="M173 45 C182 38 191 33 203 29"
          strokeWidth="0.68"
          opacity="0.42"
        />

        <path
          d="M83 50 C72 44 63 40 51 37"
          strokeWidth="0.58"
          opacity="0.3"
        />

        <path
          d="M177 50 C188 44 197 40 209 37"
          strokeWidth="0.58"
          opacity="0.3"
        />
      </g>

      {/* =========================================================
          HOLOGRAM PARTICLES
          ========================================================= */}

      <g fill={c}>
        <circle
          cx="54"
          cy="65"
          r="0.9"
          opacity="0.62"
          className="agent-viz-pulse"
        />

        <circle
          cx="206"
          cy="65"
          r="0.9"
          opacity="0.62"
          className="agent-viz-pulse"
          style={{ animationDelay: "0.4s" }}
        />

        <circle
          cx="48"
          cy="111"
          r="0.8"
          opacity="0.42"
          className="agent-viz-pulse"
        />

        <circle
          cx="212"
          cy="111"
          r="0.8"
          opacity="0.42"
          className="agent-viz-pulse"
          style={{ animationDelay: "0.7s" }}
        />

        <circle
          cx="79"
          cy="155"
          r="0.8"
          opacity="0.46"
        />

        <circle
          cx="181"
          cy="155"
          r="0.8"
          opacity="0.46"
        />
      </g>

      {/* =========================================================
          SUBTLE SCANNING BEAM
          ========================================================= */}

      <path
        d="M39 92 H221"
        stroke={c}
        strokeWidth="0.5"
        opacity="0.16"
        className="agent-viz-scan"
      />
    </svg>
  );
}

/* ================================================================
   MARKET STRUCTURE
   ================================================================ */

function MarketStructureViz({ direction }) {
  /*
   * ONLY LONG is upgraded in this pass.
   *
   * LONG  -> Bull holographic head
   * SHORT -> existing visualization
   * NEUTRAL -> existing visualization
   */

  if (direction === "LONG") {
    return <BullHeadViz direction={direction} />;
  }

  const c = colorFor(direction);

  const points =
    direction === "SHORT"
      ? "2,4 14,20 26,14 38,32 50,26 62,38"
      : "2,21 14,14 26,27 38,17 50,25 62,19";

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      <polyline
        points={points}
        fill="none"
        stroke={c}
        strokeWidth="1.4"
        opacity="0.85"
      />

      {points.split(" ").map((p, i) => {
        const [x, y] = p.split(",");

        return (
          <circle
            key={i}
            cx={x}
            cy={y}
            r="2"
            fill={c}
            className="agent-viz-pulse"
            style={{
              animationDelay: `${i * 0.15}s`,
            }}
          />
        );
      })}
    </svg>
  );
}

/* ================================================================
   BREAKOUT
   ================================================================ */

function BreakoutViz({ direction }) {
  const c = colorFor(direction);
  const active = direction !== "NEUTRAL";

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      <line
        x1="2"
        y1="21"
        x2="62"
        y2="21"
        stroke={c}
        strokeWidth="1"
        strokeDasharray="3 2"
        opacity="0.6"
      />

      {active && (
        <rect
          x="30"
          y={direction === "LONG" ? 4 : 21}
          width="6"
          height="17"
          fill={c}
          opacity="0.35"
          className="agent-viz-beam"
        />
      )}

      <circle
        cx={active ? 34 : 20}
        cy={
          active
            ? direction === "LONG"
              ? 6
              : 36
            : 21
        }
        r="2.2"
        fill={c}
      />
    </svg>
  );
}

/* ================================================================
   FIBONACCI
   ================================================================ */

function FibonacciViz({ direction }) {
  const c = colorFor(direction);
  const levels = [6, 14, 21, 28, 36];

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      {levels.map((y, i) => (
        <line
          key={i}
          x1="4"
          y1={y}
          x2="60"
          y2={y}
          stroke={c}
          strokeWidth="0.8"
          opacity={0.3 + i * 0.12}
        />
      ))}

      <circle
        cx="32"
        cy={levels[2]}
        r="2"
        fill={c}
        className="agent-viz-pulse"
      />
    </svg>
  );
}

/* ================================================================
   ELLIOTT WAVE
   ================================================================ */

function ElliottWaveViz({ direction }) {
  const c = colorFor(direction);

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      <path
        d="M2,30 L12,12 L22,24 L32,6 L42,26 L52,16 L62,30"
        fill="none"
        stroke={c}
        strokeWidth="1.4"
        opacity="0.85"
        className="agent-viz-wave"
      />
    </svg>
  );
}

/* ================================================================
   VOLUME
   ================================================================ */

function VolumeViz({ direction }) {
  const c = colorFor(direction);
  const bars = [10, 22, 14, 30, 18, 26, 12];

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      {bars.map((h, i) => (
        <rect
          key={i}
          x={4 + i * 8.5}
          y={38 - h}
          width="5"
          height={h}
          fill={c}
          opacity={0.4 + (i / bars.length) * 0.5}
          className="agent-viz-bar"
          style={{
            animationDelay: `${i * 0.08}s`,
          }}
        />
      ))}
    </svg>
  );
}

/* ================================================================
   MOMENTUM
   ================================================================ */

function MomentumViz({ direction }) {
  const c = colorFor(direction);

  const angle =
    direction === "LONG"
      ? -50
      : direction === "SHORT"
      ? 50
      : 0;

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      <circle
        cx="32"
        cy="24"
        r="14"
        fill="none"
        stroke={c}
        strokeWidth="1"
        opacity="0.3"
      />

      <line
        x1="32"
        y1="24"
        x2={
          32 +
          12 *
            Math.sin((angle * Math.PI) / 180)
        }
        y2={
          24 -
          12 *
            Math.cos((angle * Math.PI) / 180)
        }
        stroke={c}
        strokeWidth="1.6"
        className="agent-viz-needle"
      />

      <circle
        cx="32"
        cy="24"
        r="1.6"
        fill={c}
      />
    </svg>
  );
}

/* ================================================================
   SUPPORT / RESISTANCE
   ================================================================ */

function SupportResistanceViz({ direction }) {
  const c = colorFor(direction);

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      {[8, 18, 28, 38].map((y, i) => (
        <rect
          key={i}
          x="4"
          y={y}
          width="56"
          height="4"
          fill={c}
          opacity={
            i === 1 || i === 2
              ? 0.55
              : 0.2
          }
        />
      ))}
    </svg>
  );
}

/* ================================================================
   TREND
   ================================================================ */

function TrendViz({ direction }) {
  const c = colorFor(direction);
  const up = direction === "LONG";
  const rows = [0, 1, 2];

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      {rows.map((r) => {
        const base = up
          ? 34 - r * 6
          : 8 + r * 6;

        const end = up
          ? base - 14
          : base + 14;

        return (
          <path
            key={r}
            d={`M2,${base} Q32,${(base + end) / 2} 62,${end}`}
            fill="none"
            stroke={c}
            strokeWidth="1.1"
            opacity={0.85 - r * 0.22}
          />
        );
      })}
    </svg>
  );
}

/* ================================================================
   PATTERN
   ================================================================ */

function PatternViz({ direction }) {
  const c = colorFor(direction);

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      <polygon
        points="8,34 32,8 56,34"
        fill="none"
        stroke={c}
        strokeWidth="1.3"
        opacity="0.8"
      />

      <circle
        cx="32"
        cy="8"
        r="2"
        fill={c}
        className="agent-viz-pulse"
      />
    </svg>
  );
}

/* ================================================================
   FAIR VALUE GAP
   ================================================================ */

function FairValueGapViz({ direction }) {
  const c = colorFor(direction);

  return (
    <svg
      viewBox="0 0 64 42"
      className="agent-viz-svg"
    >
      <rect
        x="8"
        y="10"
        width="48"
        height="8"
        fill={c}
        opacity="0.25"
      />

      <rect
        x="8"
        y="24"
        width="48"
        height="8"
        fill={c}
        opacity="0.4"
      />

      <line
        x1="8"
        y1="10"
        x2="56"
        y2="10"
        stroke={c}
        strokeWidth="0.8"
        opacity="0.6"
      />

      <line
        x1="8"
        y1="32"
        x2="56"
        y2="32"
        stroke={c}
        strokeWidth="0.8"
        opacity="0.6"
      />
    </svg>
  );
}

/* ================================================================
   AGENT MAP
   ================================================================ */

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

/* ================================================================
   MAIN COMPONENT
   ================================================================ */

export const AgentVisualization = ({
  agentId,
  direction,
}) => {
  const Viz = VIZ_BY_AGENT[agentId];

  if (!Viz) return null;

  return (
    <div
      className="agent-viz-wrap"
      data-testid={`agent-viz-${agentId}`}
    >
      <Viz direction={direction} />
    </div>
  );
};