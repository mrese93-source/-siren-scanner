"use client";
import { ScannerStatus } from "@/types";
import { Activity, Zap } from "lucide-react";

interface Props {
  status: ScannerStatus | null;
  lastUpdate: Date | null;
}

export default function Header({ status, lastUpdate }: Props) {
  return (
    <header className="border-b border-[#1f2937] bg-[#111827]/80 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
        {/* Logo */}
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-blue-500 flex items-center justify-center">
            <Zap size={16} className="text-white" />
          </div>
          <div>
            <h1 className="text-base font-bold text-white leading-none">TradeFlow AI</h1>
            <p className="text-[10px] text-slate-500 leading-none">Crypto Signal Scanner</p>
          </div>
        </div>

        {/* Status bar */}
        <div className="flex items-center gap-4 text-xs text-slate-400">
          {status && (
            <>
              <span className="hidden sm:flex items-center gap-1.5">
                <Activity size={12} className="text-emerald-400" />
                {status.symbols_scanned} symbols
              </span>
              <span className="hidden sm:block">
                {status.active_signals} active signals
              </span>
              <span className="hidden md:block">
                {status.total_signals_today} today
              </span>
            </>
          )}
          {/* Live dot */}
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400 live-dot" />
            <span className="text-emerald-400 font-medium">LIVE</span>
          </div>
          {lastUpdate && (
            <span className="hidden sm:block text-slate-600">
              {lastUpdate.toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
