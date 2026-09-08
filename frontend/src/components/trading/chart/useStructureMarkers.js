import { useEffect } from "react";

/**
 * useStructureMarkers
 *
 * Applies HH/HL/LH/LL swing-point labels to the candle series, driven
 * entirely by the backend's MarketState (`marketState.swings`) — the
 * frontend does not detect pivots itself, it only labels what the
 * backend already found.
 *
 * BOS/CHoCH events are deliberately NOT rendered here as arrow markers
 * anymore — that approach was tried and replaced after feedback that a
 * floating arrow+text doesn't show which price level actually broke, and
 * reads ambiguously next to the similar-looking swing arrows. BOS/CHoCH
 * are now drawn as horizontal lines at the actual broken level, in
 * useStructureEventOverlay.js + StructureEventOverlayLayer.js — see
 * those for the real structural-break visualization.
 */
export function useStructureMarkers({ candleSeriesRef, marketState }) {
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    const state = marketState;
    if (!state?.swings) {
      try {
        series.setMarkers([]);
      } catch (e) {}
      return;
    }

    const highs = state.swings.highs || [];
    const lows = state.swings.lows || [];

    const swings = [
      ...highs.map((s) => ({ ...s, kind: "HIGH" })),
      ...lows.map((s) => ({ ...s, kind: "LOW" })),
    ].sort((a, b) => a.timestamp - b.timestamp);

    const markers = [];
    let previousHigh = null;
    let previousLow = null;

    swings.forEach((swing) => {
      let label = null;

      // HIGH
      if (swing.kind === "HIGH") {
        if (previousHigh !== null) {
          if (swing.price > previousHigh) label = "HH";
          else if (swing.price < previousHigh) label = "LH";
        }
        previousHigh = swing.price;
      }

      // LOW
      if (swing.kind === "LOW") {
        if (previousLow !== null) {
          if (swing.price > previousLow) label = "HL";
          else if (swing.price < previousLow) label = "LL";
        }
        previousLow = swing.price;
      }

      // First swing of each type has no comparison.
      if (!label) return;

      markers.push({
        time: swing.timestamp,
        position: swing.kind === "HIGH" ? "aboveBar" : "belowBar",
        color: swing.kind === "HIGH" ? "#ff3b56" : "#00f59b",
        shape: swing.kind === "HIGH" ? "arrowDown" : "arrowUp",
        text: label,
      });
    });

    // Native Lightweight Charts markers stay attached to their candles.
    try {
      series.setMarkers(markers);
    } catch (e) {
      console.error("Failed to set structure markers:", e);
    }
  }, [marketState]);
}