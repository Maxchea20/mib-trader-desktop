import React, { useState } from "react";
import { probeMexc, pingMexc } from "../../lib/api";

export const MexcPingButton = ({ armed }) => {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const runProbe = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const d = await probeMexc();
      if (d.ok) {
        setMsg(`Probe OK · armed=${d.live_armed} · avail ${d.available_balance} USDT · open vol ${d.open_vol}`);
      } else {
        setMsg(`Probe FAIL · ${d.error || "not ready"}`);
      }
    } catch (e) {
      setMsg(`Probe FAIL · ${e?.response?.data?.error || e.message}`);
    }
    setBusy(false);
  };

  const runPing = async () => {
    if (!armed) {
      setMsg("Not armed. Add MEXC_LIVE_TRADING_ENABLED=true to .env and restart.");
      return;
    }
    if (!window.confirm("Open 1 Isolated BTC contract on MEXC then flatten immediately?")) return;
    setBusy(true);
    setMsg(null);
    try {
      const d = await pingMexc();
      if (d.ok) {
        setMsg(`REACHED MEXC · open ${d.open_order?.data || "ok"} · closed ${d.close_order?.data || "ok"}`);
      } else {
        setMsg(`PING FAIL @ ${d.stage} · ${d.error || "unknown"}`);
      }
    } catch (e) {
      setMsg(`PING FAIL · ${e?.response?.data?.error || e.message}`);
    }
    setBusy(false);
  };

  return (
    <div className="mb-3 flex flex-wrap items-center gap-2" data-testid="mexc-ping">
      <button type="button" disabled={busy} onClick={runProbe}
        className="px-3 py-1.5 border border-[#1d2635] bg-[#0d121b] text-slate-300 font-mono-t text-[11px] rounded-sm">
        {busy ? "…" : "PROBE MEXC"}
      </button>
      <button type="button" disabled={busy || !armed} onClick={runPing}
        className={`px-3 py-1.5 border font-mono-t text-[11px] rounded-sm ${armed ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1d2635] text-slate-600"}`}>
        PING 1 CONTRACT
      </button>
      {msg && <span className="font-mono-t text-[11px] text-slate-300">{msg}</span>}
    </div>
  );
};
