import { useEffect, useState } from "react";
import { buildCandleIndexByTime, getXForTimestamp } from "./timeToX";

const MIN_DISTANCE_ATR = 0.15;
const MAX_VISIBLE_EVENTS = 8;

export function useStructureEventOverlay({ chartRef, candleSeriesRef, containerRef, candles, events }) {
  const [lines, setLines] = useState([]);

  useEffect(() => {
    const compute = () => {
      const series = candleSeriesRef.current;
      const chart = chartRef.current;
      const cont = containerRef.current;

      if (!series || !chart || !cont || !candles?.length || !events?.length) {
        setLines([]);
        return;
      }

      const w = cont.clientWidth;
      const timeScale = chart.timeScale();
      const candleIndexByTime = buildCandleIndexByTime(candles);

      let priceScaleWidth = 58;
      try {
        const actualWidth = chart.priceScale("right").width();
        if (Number.isFinite(actualWidth) && actualWidth > 0) priceScaleWidth = actualWidth;
      } catch (e) {}
      const plotRight = Math.max(0, w - priceScaleWidth);
      const out = [];

      const significant = events.filter((evt) => {
        const name = String(evt?.event || "").toUpperCase();
        return evt && (name === "BOS" || name === "CHOCH") && evt.timestamp != null
          && (evt.distance_atr == null || evt.distance_atr >= MIN_DISTANCE_ATR);
      });
      const recentEvents = significant.slice(-MAX_VISIBLE_EVENTS);

      recentEvents.forEach((evt, index) => {
        const price = evt.reference_price != null ? evt.reference_price : evt.price;
        if (price == null) return;

        const endTs = evt.timestamp;
        const startTs = evt.swing_timestamp != null ? evt.swing_timestamp : endTs;

        const xStart = getXForTimestamp(timeScale, candleIndexByTime, startTs);
        const xEnd = getXForTimestamp(timeScale, candleIndexByTime, endTs);
        if (xStart == null || xEnd == null) return;

        const left = Math.max(0, Math.min(xStart, xEnd));
        const right = Math.min(plotRight, Math.max(xStart, xEnd));
        if (right <= left) return;

        const y = series.priceToCoordinate(price);
        if (y == null) return;
        const h = cont.clientHeight;
        if (y < 16 || y > h) return;

        const name = String(evt.event).toUpperCase() === "BOS" ? "BOS" : "CHoCH";
        const color = name === "BOS" ? "#38bdf8" : "#eab308";
        const dir = String(evt.direction || "").toUpperCase();
        const bullish = dir === "LONG" || dir === "BULLISH" || dir === "UP";

        out.push({
          key: `structevt-${index}-${evt.timestamp}`,
          left,
          width: Math.max(2, right - left),
          top: y,
          color,
          label: `${name} ${bullish ? "▲" : "▼"} ${Number(price).toFixed(2)}`,
          price,
        });
      });

      setLines(out);
    };

    compute();
    const chart = chartRef.current;
    let unsubRange = () => {};
    if (chart) {
      const handler = () => compute();
      chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
      unsubRange = () => chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    }
    const resizeHandler = () => compute();
    window.addEventListener("resize", resizeHandler);
    const iv = setInterval(compute, 500);
    return () => {
      clearInterval(iv);
      unsubRange();
      window.removeEventListener("resize", resizeHandler);
    };
  }, [events, candles]);

  return lines;
}
