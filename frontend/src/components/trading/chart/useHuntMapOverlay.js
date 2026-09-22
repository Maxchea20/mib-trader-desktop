import { useEffect, useState } from "react";

function num(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fallbackMap(candles, timeframe) {
  if (!candles?.length || String(timeframe).toLowerCase() !== "15m") return { high: null, low: null };
  const prior = candles.length >= 2 ? candles[candles.length - 2] : candles[candles.length - 1];
  return { high: num(prior?.high), low: num(prior?.low) };
}

export function useHuntMapOverlay({ chartRef, candleSeriesRef, containerRef, hunt, candles, timeframe }) {
  const [lines, setLines] = useState([]);

  useEffect(() => {
    const compute = () => {
      const series = candleSeriesRef.current;
      const chart = chartRef.current;
      const cont = containerRef.current;
      if (!series || !chart || !cont) {
        setLines([]);
        return;
      }

      const pack = hunt?.hunt && typeof hunt.hunt === "object" ? hunt.hunt : {};
      const fb = fallbackMap(candles, timeframe);
      const high = num(pack.map_high) ?? num(hunt?.map_high) ?? fb.high;
      const low = num(pack.map_low) ?? num(hunt?.map_low) ?? fb.low;
      if (high == null && low == null) {
        setLines([]);
        return;
      }

      let priceScaleWidth = 58;
      try {
        const actualWidth = chart.priceScale("right").width();
        if (Number.isFinite(actualWidth) && actualWidth > 0) priceScaleWidth = actualWidth;
      } catch (e) {}
      const plotRight = Math.max(80, cont.clientWidth - priceScaleWidth);
      const h = cont.clientHeight;

      const side = String(hunt?.direction || pack.side || "").toUpperCase();
      const focusHigh = side === "LONG" || side === "BULLISH" || side === "UP";
      const focusLow = side === "SHORT" || side === "BEARISH" || side === "DOWN";

      const out = [];
      const add = (price, kind) => {
        if (price == null) return;
        const y = series.priceToCoordinate(price);
        if (y == null || y < 14 || y > h - 8) return;
        const focus = kind === "high" ? focusHigh : focusLow;
        out.push({
          key: `hunt-map-${kind}`,
          top: y,
          width: plotRight,
          focus,
          color: kind === "high" ? "#00f59b" : "#ff3b56",
          label: focus
            ? (kind === "high" ? `↓ THIS LINE  close above  ${price.toFixed(1)}` : `↑ THIS LINE  close below  ${price.toFixed(1)}`)
            : (kind === "high" ? `long line  ${price.toFixed(1)}` : `short line  ${price.toFixed(1)}`),
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
