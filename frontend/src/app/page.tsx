"use client";
import { useState, useEffect, useCallback } from "react";
import { Signal, ScannerStatus } from "@/types";
import { api } from "@/lib/api";
import Header from "@/components/Header";
import SignalTable from "@/components/SignalTable";
import { RefreshCw, TrendingUp, TrendingDown, Eye, Zap } from "lucide-react";
import clsx from "clsx";

const POLL_INTERVAL = 30_000; // 30s

function StatCard({
  label, value, sub, color,
}: { label: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div className="bg-[#111827] border border-[#1f2937] rounded-xl p-4">
      <div className="text-xs text-slate-500 mb-1">{label}</div>
      <div className={clsx("text-2xl font-bold font-mono", color ?? "text-white")}>{value}</div>
      {sub && <div className="text-xs text-slate-600 mt-0.5">{sub}</div>}
    </div>
  );
}

export default function HomePage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [status, setStatus] = useState<ScannerStatus | null>(null);
  const [filter, setFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [sigs, stat] = await Promise.all([api.signals(), api.status()]);
      setSignals(sigs);
      setStatus(stat);
      setLastUpdate(new Date());
      setError(null);
    } catch (e) {
      setError("Cannot connect to backend. Is it running?");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchData]);

  const filtered = signals.filter((s) =>
    filter === "ALL" ? true : s.direction === filter
  );

  const longCount  = signals.filter((s) => s.direction === "LONG").length;
  const shortCount = signals.filter((s) => s.direction === "SHORT").length;
  const watchCount = signals.filter((s) => s.direction === "WATCH").length;
  const topScore   = signals.length > 0 ? Math.max(...signals.map((s) => s.ai_score)) : 0;

  return (
    <div className="min-h-screen bg-[#0a0e1a]">
      <Header status={status} lastUpdate={lastUpdate} />

      <main className="max-w-7xl mx-auto px-4 py-6">
        {/* Error */}
        {error && (
          <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Stats row */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          <StatCard
            label="🟢 LONG Signals"
            value={longCount}
            color="text-emerald-400"
            sub="Bullish setups"
          />
          <StatCard
            label="🔴 SHORT Signals"
            value={shortCount}
            color="text-red-400"
            sub="Bearish setups"
          />
          <StatCard
            label="🟡 WATCH"
            value={watchCount}
            color="text-amber-400"
            sub="Monitor these"
          />
          <StatCard
            label="⚡ Top AI Score"
            value={topScore.toFixed(0)}
            color="text-blue-400"
            sub="Best signal today"
          />
        </div>

        {/* Refresh button */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-white flex items-center gap-2">
            <Zap size={16} className="text-amber-400" />
            Live Signals
          </h2>
          <button
            onClick={fetchData}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {/* Table */}
        <SignalTable signals={filtered} filter={filter} onFilterChange={setFilter} />

        {/* Footer */}
        <div className="mt-8 text-center text-xs text-slate-700">
          TradeFlow AI — For educational purposes only. Not financial advice.
        </div>
      </main>
    </div>
  );
}
