"use client";
import Link from "next/link";
import { Signal, Direction } from "@/types";
import { TrendingUp, TrendingDown, Eye, AlertTriangle } from "lucide-react";
import clsx from "clsx";

interface Props {
  signals: Signal[];
  filter: string;
  onFilterChange: (f: string) => void;
}

const directionConfig: Record<Direction, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  LONG:  { label: "LONG",  color: "text-emerald-400", bg: "bg-emerald-400/10 border-emerald-400/30", icon: <TrendingUp size={12} /> },
  SHORT: { label: "SHORT", color: "text-red-400",     bg: "bg-red-400/10 border-red-400/30",         icon: <TrendingDown size={12} /> },
  WATCH: { label: "WATCH", color: "text-amber-400",   bg: "bg-amber-400/10 border-amber-400/30",     icon: <Eye size={12} /> },
};

const riskColors: Record<string, string> = {
  LOW:    "text-emerald-400",
  MEDIUM: "text-amber-400",
  HIGH:   "text-red-400",
};

function PctCell({ value }: { value: number }) {
  const color = value > 0 ? "text-emerald-400" : value < 0 ? "text-red-400" : "text-slate-500";
  return <span className={clsx("font-mono text-xs", color)}>{value > 0 ? "+" : ""}{value.toFixed(2)}%</span>;
}

function ScoreBar({ score }: { score: number }) {
  const color = score >= 70 ? "bg-emerald-500" : score >= 45 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={clsx("h-full rounded-full transition-all", color)} style={{ width: `${score}%` }} />
      </div>
      <span className="font-mono text-xs text-slate-300">{score.toFixed(0)}</span>
    </div>
  );
}

export default function SignalTable({ signals, filter, onFilterChange }: Props) {
  const filters = ["ALL", "LONG", "SHORT", "WATCH"];

  return (
    <div className="bg-[#111827] border border-[#1f2937] rounded-xl overflow-hidden">
      {/* Filter tabs */}
      <div className="flex items-center gap-1 p-3 border-b border-[#1f2937]">
        {filters.map((f) => (
          <button
            key={f}
            onClick={() => onFilterChange(f)}
            className={clsx(
              "px-3 py-1 rounded-md text-xs font-semibold transition-all",
              filter === f
                ? "bg-slate-700 text-white"
                : "text-slate-500 hover:text-slate-300"
            )}
          >
            {f}
          </button>
        ))}
        <span className="ml-auto text-xs text-slate-600">{signals.length} signals</span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-slate-500 border-b border-[#1f2937]">
              <th className="text-left px-4 py-2.5 font-medium">Symbol</th>
              <th className="text-left px-3 py-2.5 font-medium">Signal</th>
              <th className="text-right px-3 py-2.5 font-medium">Price</th>
              <th className="text-right px-3 py-2.5 font-medium hidden sm:table-cell">1m</th>
              <th className="text-right px-3 py-2.5 font-medium hidden sm:table-cell">5m</th>
              <th className="text-right px-3 py-2.5 font-medium hidden md:table-cell">15m</th>
              <th className="text-right px-3 py-2.5 font-medium hidden md:table-cell">1h</th>
              <th className="text-right px-3 py-2.5 font-medium hidden lg:table-cell">24h</th>
              <th className="text-right px-3 py-2.5 font-medium hidden lg:table-cell">Vol Spike</th>
              <th className="text-right px-3 py-2.5 font-medium hidden xl:table-cell">Funding</th>
              <th className="text-right px-3 py-2.5 font-medium">AI Score</th>
              <th className="text-center px-3 py-2.5 font-medium hidden sm:table-cell">Risk</th>
            </tr>
          </thead>
          <tbody>
            {signals.length === 0 ? (
              <tr>
                <td colSpan={12} className="text-center py-12 text-slate-600">
                  <AlertTriangle size={24} className="mx-auto mb-2 opacity-50" />
                  No signals yet — scanner is warming up…
                </td>
              </tr>
            ) : (
              signals.map((s) => {
                const dc = directionConfig[s.direction] ?? directionConfig.WATCH;
                return (
                  <tr
                    key={s.id}
                    className="border-b border-[#1a2030] hover:bg-slate-800/40 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/coin/${s.symbol}`}
                        className="flex items-center gap-2 group"
                      >
                        <div className={clsx("w-2 h-2 rounded-full flex-shrink-0", {
                          "bg-emerald-400": s.direction === "LONG",
                          "bg-red-400":     s.direction === "SHORT",
                          "bg-amber-400":   s.direction === "WATCH",
                        })} />
                        <span className="font-semibold text-white group-hover:text-blue-400 transition-colors">
                          {s.symbol.replace("USDT", "")}
                          <span className="text-slate-600 font-normal text-xs">/USDT</span>
                        </span>
                      </Link>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex flex-col gap-1">
                        <span className={clsx("inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded border", dc.bg, dc.color)}>
                          {dc.icon} {dc.label}
                        </span>
                        <span className="text-[10px] text-slate-500">{s.signal_type.replace("_", " ")}</span>
                      </div>
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-white text-sm">
                      ${s.price < 1 ? s.price.toFixed(5) : s.price < 100 ? s.price.toFixed(3) : s.price.toFixed(2)}
                    </td>
                    <td className="px-3 py-3 text-right hidden sm:table-cell"><PctCell value={s.change_1m} /></td>
                    <td className="px-3 py-3 text-right hidden sm:table-cell"><PctCell value={s.change_5m} /></td>
                    <td className="px-3 py-3 text-right hidden md:table-cell"><PctCell value={s.change_15m} /></td>
                    <td className="px-3 py-3 text-right hidden md:table-cell"><PctCell value={s.change_1h} /></td>
                    <td className="px-3 py-3 text-right hidden lg:table-cell"><PctCell value={s.change_24h} /></td>
                    <td className="px-3 py-3 text-right hidden lg:table-cell">
                      <span className={clsx("font-mono text-xs", s.volume_spike_ratio >= 3 ? "text-amber-400" : "text-slate-400")}>
                        {s.volume_spike_ratio.toFixed(1)}x
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right hidden xl:table-cell">
                      <span className={clsx("font-mono text-xs", s.funding_rate > 0.001 ? "text-red-400" : s.funding_rate < -0.001 ? "text-emerald-400" : "text-slate-400")}>
                        {(s.funding_rate * 100).toFixed(4)}%
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right">
                      <ScoreBar score={s.ai_score} />
                    </td>
                    <td className="px-3 py-3 text-center hidden sm:table-cell">
                      <span className={clsx("text-xs font-semibold", riskColors[s.risk_level])}>
                        {s.risk_level}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
