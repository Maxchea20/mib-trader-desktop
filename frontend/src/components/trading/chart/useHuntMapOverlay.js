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
      if ((high == null && low == null) || mapTs == null) {
        setLines([]);
        return;
      }

      const timeScale = chart.timeScale();
      const candleIndexByTime = buildCandleIndexByTime(candles);
      let xStart = getXForTimestamp(timeScale, candleIndexByTime, mapTs);
      const tf = String(timeframe || "15m").toLowerCase();
      const spanSec = tf === "5m" ? 900 : 900;
      let xEnd = getXForTimestamp(timeScale, candleIndexByTime, mapTs + spanSec);
      if (xEnd == null && xStart != null) {
        const idx = candleIndexByTime.get(Number(mapTs));
        if (idx != null) {
          try {
            xEnd = timeScale.logicalToCoordinate(idx + (tf === "5m" ? 3 : 1));
          } catch (e) {}
        }
      }
      if (xStart == null && xEnd == null) {
        setLines([]);
        return;
      }
      if (xStart == null) xStart = xEnd;
      if (xEnd == null) xEnd = xStart;
      let left = Math.min(xStart, xEnd);
      let right = Math.max(xStart, xEnd);
      if (right - left < 10) {
        left -= 8;
        right += 8;
      }

      const h = cont.clientHeight;
      const side = String(hunt?.direction || pack.side || "").toUpperCase();
      const focusHigh = side === "LONG" || side === "BULLISH" || side === "UP";
      const focusLow = side === "SHORT" || side === "BEARISH" || side === "DOWN";

      const out = [];
      const add = (price, kind) => {
        if (price == null) return;
        const y = series.priceToCoordinate(price);
        if (y == null || y < 12 || y > h - 8) return;
        const focus = kind === "high" ? focusHigh : focusLow;
        out.push({
          key: `hunt-map-${kind}`,
          left,
          width: Math.max(12, right - left),
          top: y,
          focus,
          color: kind === "high" ? "#00f59b" : "#ff3b56",
          label: kind === "high" ? `↓ close above` : `↑ close below`,
        });
      };
      add(high, "high");
      add(low, "low");
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
