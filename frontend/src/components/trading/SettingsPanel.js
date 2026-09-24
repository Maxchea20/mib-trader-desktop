import React from "react";
import { X, SlidersHorizontal } from "lucide-react";
import { SystemHealthPanel } from "./SystemHealthPanel";

export const SettingsPanel = ({ open, onClose, syncStatus, auto }) => (
  <>
    {open && <div className="fixed inset-0 z-[55] bg-black/50" onClick={onClose} />}
    <div
      data-testid="settings-panel"
      className={`fixed top-0 right-0 h-full w-[380px] z-[56] bg-[#0b0f16] border-l border-[#1d2635] transition-transform duration-300 overflow-y-auto ${open ? "translate-x-0" : "translate-x-full"}`}
    >
      <div className="flex items-center justify-between px-4 h-14 border-b border-[#1d2635]">
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="w-4 h-4 text-cyan-400" />
          <span className="font-head font-bold text-lg text-white">CONFIG</span>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-white"><X className="w-5 h-5" /></button>
      </div>
      <div className="p-4 font-mono-t text-[12px] text-slate-400 leading-relaxed">
        Agent weights and vote thresholds are removed.
        Hunt Brain does not count agent votes.
        SL / TP / capital / leverage stay on the Backtest screen.
      </div>
      <div className="px-4 pb-4">
        <SystemHealthPanel syncStatus={syncStatus} auto={auto} />
      </div>
    </div>
  </>
);