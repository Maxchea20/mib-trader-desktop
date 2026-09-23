import React from "react";
import { dirStyle, fmt } from "../../lib/style";

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("en-US", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

const Money = ({ v, pct }) => {
  if (v === null || v === undefined) return <span className="text-slate-500">—</span>;
  const c = v >= 0 ? "text-emerald-400" : "text-rose-400";
  return (
    <span className={`font-mono-t ${c}`}>
      {v >= 0 ? "+" : ""}${fmt(v, 2)}
      {pct != null && (
        <span className="text-[10px] opacity-70"> ({v >= 0 ? "+" : ""}{fmt(pct, 2)}%)</span>
      )}
    </span>
  );
};

export const TradeTable = ({ rows, onClose, testid }) => (
  <div className="overflow-x-auto" data-testid={testid}>
    <table className="w-full text-left">
      <thead>
        <tr className="widget-label border-b border-[#1d2635]">
          <th className="px-3 py-2">Src</th>
          <th className="px-3 py-2">Side</th>
          <th className="px-3 py-2">Status</th>
          <th className="px-3 py-2">Entry</th>
          <th className="px-3 py-2">SL</th>
          <th className="px-3 py-2">TP</th>
          <th className="px-3 py-2">Exit</th>
          <th className="px-3 py-2">Profit</th>
          <th className="px-3 py-2">Fee</th>
          <th className="px-3 py-2">PnL</th>
          <th className="px-3 py-2">Opened</th>
          <th className="px-3 py-2"></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t) => {
          const st = dirStyle(t.side);
          const isOpen = t.status === "OPEN";
          return (
            <tr key={t.id} className="border-b border-[#161f2e] font-mono-t text-[11px]" data-testid={`paper-row-${t.id}`}>
              <td className="px-3 py-2">
                <span className={`text-[9px] px-1 py-0.5 rounded-sm border ${t.source === "AUTO" ? "border-cyan-500/50 text-cyan-400" : "border-slate-600 text-slate-400"}`}>
                  {t.source || "MANUAL"}
                </span>
                {t.thesis?.engine === "scenario" && (
                  <div className="mt-1 text-[9px] text-slate-500 whitespace-nowrap" title={t.thesis?.thesis_id || ""}>
                    <span className={`${t.thesis?.entry_method === "C" ? "text-cyan-400" : "text-slate-400"}`}>
                      {t.thesis?.case || "—"} · {t.thesis?.entry_method === "C" ? "C" : "A"}
                    </span>
                  </div>
                )}
              </td>
              <td className={`px-3 py-2 font-bold ${st.text}`}>{t.side}</td>
              <td className="px-3 py-2">
                <span className={isOpen ? "text-cyan-400" : "text-slate-400"}>{t.status}</span>
                {!isOpen && t.exit_reason && <span className="text-slate-600 ml-1">·{t.exit_reason}</span>}
              </td>
              <td className="px-3 py-2 text-slate-200">${fmt(t.entry_price, 1)}</td>
              <td className="px-3 py-2 text-rose-400/80">${fmt(t.sl_price, 1)}</td>
              <td className="px-3 py-2 text-emerald-400/80">${fmt(t.tp_price, 1)}</td>
              <td className="px-3 py-2 text-slate-300">{t.exit_price ? `$${fmt(t.exit_price, 1)}` : (isOpen ? `~$${fmt(t.mark_price, 1)}` : "—")}</td>
              <td className="px-3 py-2">{isOpen ? "—" : <Money v={t.gross_pnl} />}</td>
              <td className="px-3 py-2">{isOpen || t.fee == null ? "—" : <Money v={-Math.abs(t.fee)} />}</td>
              <td className="px-3 py-2">{isOpen ? <Money v={t.unrealized_pnl} pct={t.unrealized_pnl_pct} /> : <Money v={t.pnl} pct={t.pnl_pct} />}</td>
              <td className="px-3 py-2 text-slate-500">{fmtTime(t.opened_at)}</td>
              <td className="px-3 py-2">
                {isOpen && (
                  <button onClick={() => onClose(t.id)} data-testid={`paper-close-${t.id}`}
                    className="text-[10px] px-2 py-0.5 rounded-sm border border-slate-600 text-slate-300 hover:border-rose-500 hover:text-rose-400">
                    Close
                  </button>
                )}
              </td>
            </tr>
          );
        })}
        {rows.length === 0 && (
          <tr><td colSpan="12" className="px-3 py-6 text-center font-mono-t text-[11px] text-slate-600">No trades yet</td></tr>
        )}
      </tbody>
    </table>
  </div>
);