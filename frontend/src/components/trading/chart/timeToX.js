/**
 * Shared timestamp -> screen-X helper for chart overlays.
 *
 * Originally lived duplicated inside useFvgOverlay's getFvgX(); pulled out
 * here so useStructureEventOverlay can use the exact same priority-
 * ordered lookup instead of re-implementing it, per the "don't let each
 * overlay reinvent the same logic" goal of this whole chart refactor.
 *
 * Priority: 1. Actual candle index, 2. Lightweight Charts time index,
 * 3. Direct time coordinate. This prevents an overlay element from
 * disappearing simply because timeToCoordinate() returns null for an
 * edge-case timestamp.
 */
export function buildCandleIndexByTime(candles) {
  const map = new Map();
  candles.forEach((c, index) => map.set(Number(c.ts), index));
  return map;
}

export function getXForTimestamp(timeScale, candleIndexByTime, timestamp) {
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
}