import { useEffect, useState } from "react";
import { buildCandleIndexByTime, getXForTimestamp } from "./timeToX";

const MAX_VISIBLE_EVENTS = 16;

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
        return evt && (name === "BOS" || name === "CHOCH" || name === "CHoCH") && evt.timestamp != null;
      });
      const recentEvents = significant.slice(-MAX_VISIBLE_EVENTS);

      recentEvents.forEach((evt, index) => {
        const price = evt.reference_price != null ? evt.reference_price : evt.price;
        if (price == null) return;

        const endTs = evt.timestamp;
        let startTs = evt.swing_timestamp;
        if (startTs == null && evt.swing_index != null && candles[evt.swing_index]) {
          startTs = candles[evt.swing_index].ts;
        }
        if (startTs == null) startTs = endTs;

        let xStart = getXForTimestamp(timeScale, candleIndexByTime, startTs);
        let xEnd = getXForTimestamp(timeScale, candleIndexByTime, endTs);
        if (xEnd == null && xStart == null) return;
        if (xEnd == null) xEnd = xStart;
        if (xStart == null) xStart = xEnd;

        let left = Math.max(0, Math.min(xStart, xEnd));
        let right = Math.min(plotRight, Math.max(xStart, xEnd));
        if (right - left < 16) {
          right = Math.min(plotRight, left + 36);
          left = Math.max(0, right - 36);
        }

        const y = series.priceToCoordinate(price);
        if (y == null) return;
        const h = cont.clientHeight;
        if (y < 16 || y > h) return;

        const raw = String(evt.event || "").toUpperCase();
        const name = raw === "BOS" ? "BOS" : "CHoCH";
        const color = name === "BOS" ? "#38bdf8" : "#eab308";
        const dir = String(evt.direction || "").toUpperCase();
        const bullish = dir === "LONG" || dir === "BULLISH" || dir === "UP";

        out.push({
          key: `structevt-${index}-${evt.timestamp}`,
          left,
          width: Math.max(16, right - left),
          top: y,
          color,
          label: `${name} ${bullish ? "▲" : "▼"} ${Number(price).toFixed(0)}`,
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
