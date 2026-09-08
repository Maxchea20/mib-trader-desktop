import { useEffect, useState } from "react";

/**
 * useFvgOverlay
 *
 * Computes screen-pixel rectangles ("bands") for currently-active FVG
 * zones, given the chart/series refs and the raw candle + FVG zone data
 * from the backend.
 *
 * Extracted verbatim from CandleChart.js's inline FVG-overlay useEffect —
 * same logic, same priority-ordered X-position lookup (exact candle match
 * -> lightweight-charts time index -> direct time coordinate), same
 * "never render filled FVGs" rule, same live recompute on zoom/pan/resize/
 * tick. Nothing behavioral changed here, only where the code lives.
 *
 * Kept separate from FvgOverlayLayer (which only knows how to draw a
 * labeled rectangle) so "how to compute FVG screen positions" and "how to
 * render a positioned box" don't have to be understood together.
 */
export function useFvgOverlay({ chartRef, candleSeriesRef, containerRef, candles, fvgZones }) {
  const [bands, setBands] = useState([]);

  useEffect(() => {
    const compute = () => {
      const series = candleSeriesRef.current;
      const chart = chartRef.current;
      const cont = containerRef.current;

      if (!series || !chart || !cont || !candles?.length) {
        setBands([]);
        return;
      }

      const h = cont.clientHeight;
      const w = cont.clientWidth;
      const timeScale = chart.timeScale();

      const out = [];

      // --- RIGHT EDGE OF CANDLE PLOT ---
      let priceScaleWidth = 58;
      try {
        const actualWidth = chart.priceScale("right").width();
        if (Number.isFinite(actualWidth) && actualWidth > 0) {
          priceScaleWidth = actualWidth;
        }
      } catch (e) {}

      const plotRight = Math.max(0, w - priceScaleWidth);

      // --- TIMESTAMP -> ACTUAL CANDLE INDEX ---
      const candleIndexByTime = new Map();
      candles.forEach((c, index) => {
        candleIndexByTime.set(Number(c.ts), index);
      });

      // --- GET FVG X POSITION ---
      // Priority: 1. Actual candle index, 2. Lightweight Charts time
      // index, 3. Direct time coordinate. This prevents the FVG from
      // disappearing simply because timeToCoordinate() returns null.
      const getFvgX = (timestamp) => {
        if (timestamp == null) return null;
        const ts = Number(timestamp);

        const candleIndex = candleIndexByTime.get(ts);
        if (candleIndex !== undefined) {
          try {
            const x = timeScale.logicalToCoordinate(candleIndex);
            if (x != null && Number.isFinite(x)) return x;
          } catch (e) {}
        }

        try {
          const logical = timeScale.timeToIndex(ts, true);
          if (logical != null) {
            const x = timeScale.logicalToCoordinate(logical);
            if (x != null && Number.isFinite(x)) return x;
          }
        } catch (e) {}

        try {
          const x = timeScale.timeToCoordinate(ts);
          if (x != null && Number.isFinite(x)) return x;
        } catch (e) {}

        return null;
      };

      // --- RENDER FVGs ---
      (fvgZones || []).forEach((zone, index) => {
        if (zone?.low == null || zone?.high == null || zone?.timestamp == null) {
          return;
        }

        // Never render filled FVGs.
        const state = zone.state || (zone.mitigated ? "partial" : "fresh");
        if (state === "filled") return;

        // Find actual creation candle X position.
        const xStart = getFvgX(zone.timestamp);
        if (xStart == null) return;

        // If the FVG started before the visible chart, clip to left edge.
        const left = Math.max(0, xStart);

        // If creation candle is beyond the right edge, nothing is visible.
        if (left >= plotRight) return;

        // --- PRICE -> SCREEN Y ---
        let yTop = series.priceToCoordinate(Math.max(zone.low, zone.high));
        let yBot = series.priceToCoordinate(Math.min(zone.low, zone.high));
        if (yTop == null || yBot == null) return;

        // Clip to chart height.
        yTop = Math.max(0, yTop);
        yBot = Math.min(h, yBot);
        if (yTop > h || yBot < 0) return;

        const height = Math.max(2, yBot - yTop);

        // Extend the FVG from creation candle to the current chart edge.
        const width = Math.max(2, plotRight - left);

        const bullish = zone.type === "fvg_bullish";

        // --- FVG VISUAL STYLE ---
        let background;
        let border;
        if (bullish) {
          if (state === "partial") {
            background = "rgba(16,185,129,0.08)";
            border = "rgba(16,185,129,0.30)";
          } else {
            background = "rgba(16,185,129,0.18)";
            border = "rgba(16,185,129,0.60)";
          }
        } else {
          if (state === "partial") {
            background = "rgba(244,63,94,0.08)";
            border = "rgba(244,63,94,0.30)";
          } else {
            background = "rgba(244,63,94,0.18)";
            border = "rgba(244,63,94,0.60)";
          }
        }

        out.push({
          key: `fvg-${index}-${zone.timestamp}`,
          kind: "fvg",
          left,
          width,
          top: yTop,
          height,
          color: background,
          borderColor: border,
          label: `FVG ${bullish ? "▲" : "▼"} ${state}`,
          state,
          quality: zone.quality,
        });
      });

      // IMPORTANT: no confluenceZones.forEach() here — Confluence is
      // intentionally not part of the FVG overlay.

      setBands(out);
    };

    // Initial calculation.
    compute();

    // Recalculate when the chart is zoomed or scrolled.
    const chart = chartRef.current;
    let unsubRange = () => {};
    if (chart) {
      const handler = () => compute();
      chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
      unsubRange = () => chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    }

    // Recalculate when the window changes size.
    const resizeHandler = () => compute();
    window.addEventListener("resize", resizeHandler);

    // Keep FVG synchronized with live chart movement.
    const iv = setInterval(compute, 500);

    return () => {
      clearInterval(iv);
      unsubRange();
      window.removeEventListener("resize", resizeHandler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fvgZones, candles]);

  return bands;
}