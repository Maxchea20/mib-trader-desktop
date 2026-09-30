import { useEffect, useRef } from "react";

const UP_COLOR = "#ef5350";   // upper (falling) trendline
const DN_COLOR = "#26a69a";   // lower (rising) trendline

/**
 * useTrendBreakOverlay
 *
 * Draws the backend's Trend Break trendlines (pivot -> break / next pivot)
 * and a marker on every break bar, exactly as computed by
 * backend/src/trend_break/trendline.py. The frontend does no trendline math.
 * Each segment is a native two-point line series, so slanted lines stay
 * attached to price/time when panning and zooming.
 */
export function useTrendBreakOverlay({ chartRef, candleSeriesRef, data, timeframe }) {
  const seriesRef = useRef([]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return undefined;

    seriesRef.current.forEach((s) => {
      try { chart.removeSeries(s); } catch (e) {}
    });
    seriesRef.current = [];

    if (!data || !Array.isArray(data.lines)) return undefined;

    data.lines.forEach((ln) => {
      const t0 = Number(ln.pivot_ts);
      const t1 = Number(ln.end_ts);
      if (!(t1 > t0)) return;
      const s = chart.addLineSeries({
        color: ln.kind === "upper" ? UP_COLOR : DN_COLOR,
        lineWidth: 2,
        lineStyle: ln.broken ? 0 : 2,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData([
        { time: t0, value: Number(ln.pivot_price) },
        { time: t1, value: Number(ln.end_price) },
      ]);
      seriesRef.current.push(s);
    });

    const breaks = (data.breaks || []).filter((b) => Number.isFinite(Number(b.ts)));
    if (breaks.length) {
      const m = chart.addLineSeries({
        color: "rgba(0,0,0,0)",
        lineWidth: 1,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      const pts = breaks
        .map((b) => ({ time: Number(b.ts), value: Number(b.close), b }))
        .sort((a, c) => a.time - c.time)
        .filter((p, i, arr) => i === 0 || p.time !== arr[i - 1].time);
      m.setData(pts.map(({ time, value }) => ({ time, value })));
      m.setMarkers(
        pts.map(({ time, b }) => {
          const up = b.direction === "LONG";
          const d = b.dist_atr != null ? ` ${Number(b.dist_atr).toFixed(2)}ATR` : "";
          return {
            time,
            position: up ? "belowBar" : "aboveBar",
            color: up ? DN_COLOR : UP_COLOR,
            shape: up ? "arrowUp" : "arrowDown",
            text: `${up ? "B↑" : "B↓"} +${Number(b.dist).toFixed(1)}${d}`,
          };
        })
      );
      seriesRef.current.push(m);
    }

    return () => {
      seriesRef.current.forEach((s) => {
        try { chart.removeSeries(s); } catch (e) {}
      });
      seriesRef.current = [];
    };
  }, [chartRef, candleSeriesRef, data, timeframe]);
}
