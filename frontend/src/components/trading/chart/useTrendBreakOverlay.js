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
      const color = ln.kind === "upper" ? UP_COLOR : DN_COLOR;
      const step = Number(data.tf_seconds) || 900;
      const seg = (t0, p0, t1, p1, dashed) => {
        if (!(t1 > t0)) return;
        // One point per bar: the time scale counts a bar per data time, so a
        // 2-point line running past the last candle would squash all the
        // future bars into one and look almost vertical.
        const n = Math.max(1, Math.round((t1 - t0) / step));
        const pts = [];
        for (let k = 0; k <= n; k += 1) {
          pts.push({ time: t0 + k * step, value: Number(p0) + ((Number(p1) - Number(p0)) * k) / n });
        }
        const s = chart.addLineSeries({
          color,
          lineWidth: 2,
          lineStyle: dashed ? 2 : 0,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        });
        s.setData(pts);
        seriesRef.current.push(s);
      };
      // solid: pivot -> breaking close (or newest bar); dashed: projected forward
      seg(Number(ln.pivot_ts), ln.pivot_price, Number(ln.end_ts), ln.end_price, false);
      if (ln.ext_ts != null) {
        seg(Number(ln.end_ts), ln.end_price, Number(ln.ext_ts), ln.ext_price, true);
      }
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
