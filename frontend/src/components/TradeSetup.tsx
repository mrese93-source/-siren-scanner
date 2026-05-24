"use client";
import { Signal } from "@/types";
import { TrendingUp, TrendingDown, Target, Shield, ArrowUpRight, ArrowDownRight } from "lucide-react";
import clsx from "clsx";

interface Props {
  signal: Signal;
}

function Row({ label, value, highlight }: { label: string; value: string; highlight?: string }) {
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-[#1a2030] last:border-0">
      <span className="text-slate-500 text-sm">{label}</span>
      <span className={clsx("font-mono font-semibold text-sm", highlight ?? "text-white")}>{value}</span>
    </div>
  );
}

export default function TradeSetup({ signal }: Props) {
  const isLong = signal.direction === "LONG";
  const isShort = signal.direction === "SHORT";

  const dirColor = isLong ? "text-emerald-400" : isShort ? "text-red-400" : "text-amber-400";
  const dirBg = isLong ? "from-emerald-500/10 to-transparent border-emerald-500/20"
              : isShort ? "from-red-500/10 to-transparent border-red-500/20"
              : "from-amber-500/10 to-transparent border-amber-500/20";
  const DirIcon = isLong ? TrendingUp : isShort ? TrendingDown : Target;

  const formatPrice = (p: number) =>
    p < 1 ? `$${p.toFixed(5)}` : p < 100 ? `$${p.toFixed(3)}` : `$${p.toFixed(2)}`;

  return (
    <div className={clsx("bg-gradient-to-b border rounded-xl p-5", dirBg)}>
      {/* Direction badge */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <DirIcon size={18} className={dirColor} />
          <span className={clsx("text-lg font-bold", dirColor)}>{signal.direction}</span>
          <span className="text-slate-500 text-sm">— {signal.signal_type.replace(/_/g, " ")}</span>
        </div>
        <div className="text-right">
          <div className="text-xs text-slate-500">AI Score</div>
          <div className={clsx("text-xl font-bold font-mono", dirColor)}>{signal.ai_score.toFixed(0)}</div>
        </div>
      </div>

      {/* Confidence */}
      <div className="mb-4">
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>Confidence</span>
          <span className="text-white font-semibold">{signal.confidence.toFixed(0)}%</span>
        </div>
        <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
          <div
            className={clsx("h-full rounded-full transition-all", isLong ? "bg-emerald-500" : isShort ? "bg-red-500" : "bg-amber-500")}
            style={{ width: `${signal.confidence}%` }}
          />
        </div>
      </div>

      {/* Levels */}
      <div className="bg-[#0a0e1a]/60 rounded-lg p-4 mb-4">
        <h3 className="text-xs text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <Target size={12} />
          Trade Levels
        </h3>
        <Row label="Entry Zone" value={`${formatPrice(signal.entry_low)} – ${formatPrice(signal.entry_high)}`} />
        <Row
          label="Stop Loss"
          value={formatPrice(signal.stop_loss)}
          highlight="text-red-400"
        />
        <Row
          label="Take Profit 1"
          value={formatPrice(signal.take_profit_1)}
          highlight="text-emerald-400"
        />
        <Row
          label="Take Profit 2"
          value={formatPrice(signal.take_profit_2)}
          highlight="text-emerald-400"
        />
        <Row
          label="Take Profit 3"
          value={formatPrice(signal.take_profit_3)}
          highlight="text-emerald-400"
        />
        <div className="mt-3 pt-3 border-t border-[#1a2030] flex items-center justify-between">
          <span className="text-slate-500 text-sm flex items-center gap-1">
            <Shield size={12} /> Risk/Reward
          </span>
          <span className={clsx("font-mono text-lg font-bold", signal.risk_reward >= 2 ? "text-emerald-400" : "text-amber-400")}>
            {signal.risk_reward.toFixed(1)}x
          </span>
        </div>
      </div>

      {/* Why this signal */}
      <div className="bg-[#0a0e1a]/60 rounded-lg p-4">
        <h3 className="text-xs text-slate-500 uppercase tracking-wider mb-2">Why this signal</h3>
        <p className="text-sm text-slate-300 leading-relaxed">{signal.explanation}</p>
      </div>
    </div>
  );
}
