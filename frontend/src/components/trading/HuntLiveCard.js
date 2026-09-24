import React from "react";

const n = (v, d = 1) => {
  const x = Number(v);
  return Number.isFinite(x) ? x.toFixed(d) : "—";
};

export const HuntLiveCard = ({ hunt, auto }) => {
  const st = auto?.state || auto || {};
  const last = st.last_hunt || {};
  const h = hunt || last || {};
  const pack = h.hunt || {};
  const action = h.action || last.action || "WAIT";
  const side = h.direction || last.direction || "—";
  const slot = h.slot ?? pack.slot ?? last.slot;
  const timing = h.timing || last.timing || "—";
  const phase = h.timing_state || last.timing_state || "—";
  const gate = h.gate || last.gate || "—";
  const event = h.event || pack.event || last.event || "—";
  const origin = h.thesis_level || pack.level || last.thesis_level;
  const why = (h.why_state && h.why_state[0]) || st.last_reason || st.last_action || "";
  const equity = st.equity ?? st.available_balance;
  const openN = st.open_positions ?? 0;
  const entry = h.entry || last.entry;
  const sl = h.stop || last.stop;
  const tp = h.target || last.target;

  const fire = action === "FIRE";
  const rgb = fire && side === "SHORT" ? "255,59,86" : fire ? "0,245,155" : "251,191,36";

  return (
    <div className="panel p-3" data-testid="hunt-live-card" style={{ borderColor: `rgba(${rgb},0.35)` }}>
      <div className="flex items-center justify-between mb-2">
        <span className="widget-label">Hunt live · Isolated seat</span>
        <span className="font-mono-t text-[10px] text-slate-500">{gate}</span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 font-mono-t text-[11px]">
        <div>
          <div className="widget-label">Hunt</div>
          <div className="text-slate-100 font-bold mt-0.5" style={{ color: `rgb(${rgb})` }}>
            {action} {side !== "—" && side !== "NEUTRAL" ? side : ""}
          </div>
        </div>
        <div>
          <div className="widget-label">5m slot</div>
          <div className="text-slate-200 mt-0.5">{slot != null ? `${slot} / 3` : "—"}</div>
        </div>
        <div>
          <div className="widget-label">Timing</div>
          <div className="text-cyan-300 mt-0.5">{timing} · {phase}</div>
        </div>
        <div>
          <div className="widget-label">15m event</div>
          <div className="text-slate-200 mt-0.5">{event}</div>
        </div>
        <div>
          <div className="widget-label">Thesis origin</div>
          <div className="text-slate-200 mt-0.5">{n(origin, 1)}</div>
        </div>
        <div>
          <div className="widget-label">Seat</div>
          <div className="text-slate-200 mt-0.5">{openN > 0 ? `OPEN ${openN}` : "FLAT"}</div>
        </div>
        <div>
          <div className="widget-label">Equity</div>
          <div className="text-slate-200 mt-0.5">{equity != null ? `$${n(equity, 2)}` : "—"}</div>
        </div>
        <div>
          <div className="widget-label">SL / TP</div>
          <div className="text-slate-200 mt-0.5">
            {openN > 0 || fire ? `${n(sl, 1)} / ${n(tp, 1)}` : "—"}
          </div>
        </div>
      </div>
      {why ? (
        <div className="mt-2 font-mono-t text-[10px] text-slate-500 truncate" title={String(why)}>
          {String(why)}
        </div>
      ) : null}
    </div>
  );
};
