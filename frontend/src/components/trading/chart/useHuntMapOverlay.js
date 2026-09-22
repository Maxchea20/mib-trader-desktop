import { useEffect, useState } from "react";
import { buildCandleIndexByTime, getXForTimestamp } from "./timeToX";

function num(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fallbackMap(candles, timeframe) {
  if (!candles?.length || String(timeframe).toLowerCase() !== "15m") {
    return { high: null, low: null, ts: null };
  }
  const prior = candles.length >= 2 ? candles[candles.length - 2] : candles[candles.length - 1];
  return { high: num(prior?.high), low: num(prior?.low), ts: num(prior?.ts) };
}

function candleBox(timeScale, candleIndexByTime, ts, timeframe) {
  let xStart = getXForTimestamp(timeScale, candleIndexByTime, ts);
  const tf = String(timeframe || "15m").toLowerCase();
  let xEnd = getXForTimestamp(timeScale, candleIndexByTime, ts + 900);
  if (xEnd == null && xStart != null) {
    const idx = candleIndexByTime.get(Number(ts));
    if (idx != null) {
      try {
        xEnd = timeScale.logicalToCoordinate(idx + (tf === "5m" ? 3 : 1));
      } catch (e) {}
    }
  }
  if (xStart == null && xEnd == null) return null;
  if (xStart == null) xStart = xEnd;
  if (xEnd == null) xEnd = xStart;
  let left = Math.min(xStart, xEnd);
  let right = Math.max(xStart, xEnd);
  if (right - left < 10) {
    left -= 8;
    right += 8;
  }
  return { left, width: Math.max(12, right - left) };
}

export function useHuntMapOverlay({ chartRef, candleSeriesRef, containerRef, hunt, candles, timeframe }) {
  const [lines, setLines] = useState([]);

  useEffect(() => {
    const compute = () => {
      const series = candleSeriesRef.current;
      const chart = chartRef.current;
      const cont = containerRef.current;
      if (!series || !chart || !cont || !candles?.length) {
        setLines([]);
        return;
      }

      const pack = hunt?.hunt && typeof hunt.hunt === "object" ? hunt.hunt : {};
      const fb = fallbackMap(candles, timeframe);
      const high = num(pack.map_high) ?? num(hunt?.map_high) ?? fb.high;
      const low = num(pack.map_low) ?? num(hunt?.map_low) ?? fb.low;
      const mapTs = num(pack.map_ts) ?? num(hunt?.map_ts) ?? fb.ts;
      const timeScale = chart.timeScale();
      const candleIndexByTime = buildCandleIndexByTime(candles);
      const h = cont.clientHeight;
      const out = [];

      if (mapTs != null && (high != null || low != null)) {
        const box = candleBox(timeScale, candleIndexByTime, mapTs, timeframe);
        if (box) {
          const add = (price, kind) => {
            if (price == null) return;
            const y = series.priceToCoordinate(price);
            if (y == null || y < 12 || y > h - 8) return;
            out.push({
              key: `hunt-map-${kind}`,
              left: box.left,
              width: box.width,
              top: y,
              focus: false,
              color: kind === "high" ? "#00f59b" : "#ff3b56",
              label: kind === "high" ? "\u2193 close above" : "\u2191 close below",
            });
          };
          add(high, "high");
          add(low, "low");
        }
      }

      const breaks = Array.isArray(hunt?.breaks) ? hunt.breaks : [];
      breaks.forEach((br, i) => {
        const price = num(br.price);
        const ts = num(br.ts);
        if (price == null || ts == null) return;
        const box = candleBox(timeScale, candleIndexByTime, ts, timeframe);
        if (!box) return;
        const y = series.priceToCoordinate(price);
        if (y == null || y < 12 || y > h - 8) return;
        const up = String(br.side || "") === "up";
        const kind = String(br.kind || "CHoCH/BOS");
        out.push({
          key: `hunt-break-${i}-${ts}`,
          left: box.left,
          width: box.width,
          top: y,
          focus: true,
          color: up ? "#38bdf8" : "#eab308",
          label: up ? `${kind} \u2191` : `${kind} \u2193`,
        });
      });

      setLines(out);
    };

    compute();
    const chart = chartRef.current;
    let unsub = () => {};
    if (chart) {
      const handler = () => compute();
      chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
      unsub = () => chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    }
    const onResize = () => compute();
    window.addEventListener("resize", onResize);
    const iv = setInterval(compute, 500);
    return () => {
      clearInterval(iv);
      unsub();
      window.removeEventListener("resize", onResize);
    };
  }, [hunt, candles, timeframe]);

  return lines;
}
