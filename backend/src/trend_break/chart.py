"""Display payload for the front-end chart: every trendline segment and every
break of the setup-timeframe trendline, straight from trendline.compute()
(no re-implementation, no math of its own)."""
from typing import Dict, Sequence

from . import trendline as tl


def chart_payload(candles: Sequence[dict], tf_seconds: int, length: int = 14,
                  mult: float = 1.0, max_lines: int = 40, extend_bars: int = 60) -> Dict:
    res = tl.compute(candles, length=length, mult=mult)
    if not candles:
        return {"lines": [], "breaks": [], "length": length, "mult": mult}
    last = res.last_index
    lines = []
    for k, ln in enumerate(res.lines):
        nxt = next((o.confirm_index for o in res.lines[k + 1:] if o.kind == ln.kind), None)
        end = min(last, nxt) if nxt is not None else last      # replaced by the next same-side pivot
        broken = [b for b in res.events if b.line == ln]
        if broken:
            end = min(end, broken[0].index)                     # drawn up to the breaking close
        ext = {}
        if nxt is None:                                         # still the live line of its side: project it forward
            e_idx = last + extend_bars
            ext = {"ext_ts": int(candles[last]["ts"]) + extend_bars * tf_seconds,
                   "ext_price": ln.value_at_index(e_idx)}
        lines.append({
            **ext,
            "kind": ln.kind, "pivot_ts": ln.pivot_ts, "pivot_price": ln.pivot_price,
            "confirm_ts": int(candles[ln.confirm_index]["ts"]),
            "end_ts": int(candles[end]["ts"]), "end_price": ln.value_at_index(end),
            "slope": ln.slope, "broken": bool(broken),
        })
    atr = res.atr or 0.0
    breaks = [{
        "ts": b.ts, "direction": b.direction, "close": b.close, "line_value": b.line_value,
        "dist": abs(b.close - b.line_value),
        "dist_atr": (abs(b.close - b.line_value) / b.line.slope / length) if b.line.slope else None,
    } for b in res.events]
    return {"lines": lines[-max_lines:], "breaks": breaks[-max_lines:], "length": length, "mult": mult}
