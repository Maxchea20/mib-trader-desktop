import { useEffect, useState } from "react";
import { buildCandleIndexByTime, getXForTimestamp } from "./timeToX";

// Only render breaks with real conviction behind them — a level that got
// crossed by a hair (low distance_atr) is exactly the kind of minor,
// noisy break that makes a busy/volatile stretch look cluttered with
// lines that don't mean much. Raise this if it's still too busy, lower
// it if genuinely meaningful breaks are getting hidden.
const MIN_DISTANCE_ATR = 0.4;

// Even after filtering by significance, cap how many render at once —
// a genuinely volatile stretch can still produce more real breaks than
// are useful to look at simultaneously. Keeps only the most recent ones.
const MAX_VISIBLE_EVENTS = 8;

/**
 * useStructureEventOverlay
 *
 * Computes horizontal line segments for BOS/CHoCH structural break
 * events — replacing the earlier arrow-marker approach, per direct
 * feedback that arrows alone don't show WHICH price level actually got
 * broken or make BOS vs CHoCH easy to tell apart at a glance.
 *
 * Each line runs from the swing point that got broken (using the
 * backend's `swing_index` to look up that candle's actual timestamp) to
 * the candle where the break happened (`event.timestamp`), at a constant
 * price (`event.reference_price` — the level itself, since a break is
 * defined as price crossing that fixed level). The BOS/CHoCH label sits
 * at the right end of the line, right where the break occurred.
 *
 * Filters out low-conviction breaks (see MIN_DISTANCE_ATR) and caps the
 * total shown (see MAX_VISIBLE_EVENTS) — per feedback that showing every
 * historical break at once (the backend keeps the last 50) looked
 * cluttered compared to reference chart tools, which only surface
 * meaningful, recent breaks.
 */
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

      // Filter by significance first, then keep only the most recent N —
      // no point computing pixel positions for events we're going to
      // discard anyway.
      const significant = events.filter(
        (evt) => evt && (evt.event === "BOS" || evt.event === "CHoCH")
          && evt.event !== "NONE" && evt.timestamp != null
          && (evt.distance_atr == null || evt.distance_atr >= MIN_DISTANCE_ATR)
      );
      const recentEvents = significant.slice(-MAX_VISIBLE_EVENTS);

      recentEvents.forEach((evt, index) => {
        if (evt.reference_price == null) return;

        // The swing point's OWN timestamp is where the line starts —
        // swing_index is a candle-array index from the backend, so look
        // up that candle directly rather than assuming any fixed offset.
        const swingCandle = evt.swing_index != null ? candles[evt.swing_index] : null;
        const startTs = swingCandle ? swingCandle.ts : evt.timestamp;

        const xStart = getXForTimestamp(timeScale, candleIndexByTime, startTs);
        const xEnd = getXForTimestamp(timeScale, candleIndexByTime, evt.timestamp);
        if (xStart == null || xEnd == null) return;

        const left = Math.max(0, Math.min(xStart, xEnd));
        const right = Math.min(plotRight, Math.max(xStart, xEnd));
        if (right <= left) return; // fully scrolled off, nothing to draw

        const y = series.priceToCoordinate(evt.reference_price);
        if (y == null) return;

        // If the broken price level is above or below the currently
        // visible price range (e.g. you've zoomed/panned to a different
        // window), don't draw anything for it at all — a structural
        // break is a single price point, not a range, so there's no
        // sensible "partial" version of it the way an FVG zone can be
        // partially clipped. Rendering it anyway is exactly what caused
        // a floating label with no visible line to collide with the
        // chart's toolbar buttons.
        //
        // The extra 16px top margin accounts for the label itself, which
        // draws 14px above the line — without this, a line sitting right
        // at y=2 would pass the y<0 check but its label would still
        // poke off the top edge.
        const h = cont.clientHeight;
        if (y < 16 || y > h) return;

        const bullish = evt.direction === "LONG";
        // BOS = continuation (existing app "breakout" blue), CHoCH =
        // reversal (existing app "dynamic" amber) — same color meaning
        // as everywhere else in the UI, so this reads consistently with
        // the rest of the chart rather than introducing a new palette.
        const color = evt.event === "BOS" ? "#38bdf8" : "#eab308";

        out.push({
          key: `structevt-${index}-${evt.timestamp}`,
          left,
          width: Math.max(2, right - left),
          top: y,
          color,
          label: `${evt.event} ${bullish ? "▲" : "▼"} ${evt.reference_price.toFixed(2)}`,
          price: evt.reference_price,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, candles]);

  return lines;
}