"use client";
import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Signal } from "@/types";
import { api } from "@/lib/api";
import TradeSetup from "@/components/TradeSetup";
import { ArrowLeft, Clock, TrendingUp, TrendingDown, Eye } from "lucide-react";
import clsx from "clsx";

function PctBadge({ value, label }: { value: number; label: string }) {
  const color = value > 0 ? "text-emerald-400 bg-emerald-400/10" : value < 0 ? "text-red-400 bg-red-400/10" : "text-slate-500 bg-slate-700";
  return (
    <div className="text-center">
      <div className="text-xs text-slate-500 mb-0.5">{label}</div>
      <span className={clsx("text-xs font-mono font-semibold px-2 py-0.5 rounded", color)}>
        {value > 0 ? "+" : ""}{value.toFixed(2)}%
      </span>
    </div>
  );
}

export default function CoinPage() {
  const params = useParams();
  const symbol = (params.symbol as string).toUpperCase();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.signalsBySymbol(symbol)
      .then(setSignals)
      .finally(() => setLoading(false));
  }, [symbol]);

  const latest = signals[0] ?? null;

  return (
    <div className="min-h-screen bg-[#0a0e1a]">
      <div className="max-w-4xl mx-auto px-4 py-6">
        {/* Back */}
        <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-white mb-6 transition-colors">
          <ArrowLeft size={14} />
          Back to Dashboard
        </Link>

        {loading ? (
          <div className="text-center py-20 text-slate-600">Loading…</div>
        ) : !latest ? (
          <div className="text-center py-20 text-slate-600">
            No signals found for {symbol}
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="flex items-start justify-between mb-6">
              <div>
                <h1 className="text-3xl font-bold text-white">
                  {symbol.replace("USDT", "")}
                  <span className="text-slate-600 text-xl font-normal">/USDT</span>
                </h1>
                <div className="text-2xl font-mono font-bold mt-1 text-white">
                  ${latest.price < 1 ? latest.price.toFixed(5) : latest.price < 100 ? latest.price.toFixed(3) : latest.price.toFixed(2)}
                </div>
              </div>
              <div className="text-right">
                <div className={clsx(
                  "text-sm font-semibold px-3 py-1 rounded-full border",
                  latest.direction === "LONG"  && "text-emerald-400 bg-emerald-400/10 border-emerald-400/30",
                  latest.direction === "SHORT" && "text-red-400 bg-red-400/10 border-red-400/30",
                  latest.direction === "WATCH" && "text-amber-400 bg-amber-400/10 border-amber-400/30",
                )}>
                  {latest.direction === "LONG" && <TrendingUp size={14} className="inline mr-1" />}
                  {latest.direction === "SHORT" && <TrendingDown size={14} className="inline mr-1" />}
                  {latest.direction === "WATCH" && <Eye size={14} className="inline mr-1" />}
                  {latest.direction}
                </div>
                <div className="flex items-center gap-1 text-xs text-slate-500 mt-1 justify-end">
                  <Clock size={11} />
                  {new Date(latest.created_at).toLocaleTimeString()}
                </div>
              </div>
            </div>

            {/* Price changes */}
            <div className="grid grid-cols-5 gap-2 mb-6 bg-[#111827] border border-[#1f2937] rounded-xl p-4">
              <PctBadge value={latest.change_1m}  label="1m" />
              <PctBadge value={latest.change_5m}  label="5m" />
              <PctBadge value={latest.change_15m} label="15m" />
              <PctBadge value={latest.change_1h}  label="1h" />
              <PctBadge value={latest.change_24h} label="24h" />
            </div>

            {/* Market data */}
            <div className="grid grid-cols-3 gap-3 mb-6">
              <div className="bg-[#111827] border border-[#1f2937] rounded-xl p-3 text-center">
                <div className="text-xs text-slate-500 mb-1">Vol Spike</div>
                <div className={clsx("text-lg font-mono font-bold", latest.volume_spike_ratio >= 3 ? "text-amber-400" : "text-white")}>
                  {latest.volume_spike_ratio.toFixed(1)}x
                </div>
              </div>
              <div className="bg-[#111827] border border-[#1f2937] rounded-xl p-3 text-center">
                <div className="text-xs text-slate-500 mb-1">Open Interest</div>
                <div className="text-lg font-mono font-bold text-white">
                  {latest.open_interest > 1e6
                    ? `$${(latest.open_interest / 1e6).toFixed(1)}M`
                    : latest.open_interest > 0
                    ? `$${(latest.open_interest / 1e3).toFixed(0)}K`
                    : "—"}
                </div>
              </div>
              <div className="bg-[#111827] border border-[#1f2937] rounded-xl p-3 text-center">
                <div className="text-xs text-slate-500 mb-1">Funding</div>
                <div className={clsx("text-lg font-mono font-bold",
                  latest.funding_rate > 0.001 ? "text-red-400" :
                  latest.funding_rate < -0.001 ? "text-emerald-400" : "text-white"
                )}>
                  {(latest.funding_rate * 100).toFixed(4)}%
                </div>
              </div>
            </div>

            {/* Trade setup */}
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Trade Setup</h2>
            <TradeSetup signal={latest} />

            {/* Signal history */}
            {signals.length > 1 && (
              <div className="mt-6">
                <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Signal History</h2>
                <div className="space-y-2">
                  {signals.slice(1).map((s) => (
                    <div key={s.id} className="bg-[#111827] border border-[#1f2937] rounded-lg px-4 py-3 flex items-center justify-between text-sm">
                      <div className="flex items-center gap-3">
                        <span className={clsx("font-bold",
                          s.direction === "LONG" ? "text-emerald-400" :
                          s.direction === "SHORT" ? "text-red-400" : "text-amber-400"
                        )}>{s.direction}</span>
                        <span className="text-slate-500">{s.signal_type.replace(/_/g, " ")}</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-slate-500">
                        <span className="font-mono text-white">${s.price < 1 ? s.price.toFixed(5) : s.price.toFixed(3)}</span>
                        <span>{new Date(s.created_at).toLocaleTimeString()}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
