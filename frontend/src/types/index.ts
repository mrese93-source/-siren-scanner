export type Direction = "LONG" | "SHORT" | "WATCH";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type SignalType =
  | "PUMP"
  | "DUMP"
  | "VOLUME_SPIKE"
  | "FUNDING"
  | "OI_CHANGE"
  | "SHORT_SQUEEZE"
  | "LONG_SQUEEZE"
  | "WATCH";

export interface Signal {
  id: number;
  symbol: string;
  direction: Direction;
  signal_type: SignalType;
  confidence: number;
  risk_level: RiskLevel;
  ai_score: number;
  price: number;
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2: number;
  take_profit_3: number;
  risk_reward: number;
  change_1m: number;
  change_5m: number;
  change_15m: number;
  change_1h: number;
  change_24h: number;
  volume_24h: number;
  volume_spike_ratio: number;
  open_interest: number;
  funding_rate: number;
  explanation: string;
  is_active: boolean;
  created_at: string;
}

export interface ScannerStatus {
  last_scan: string | null;
  total_signals_today: number;
  active_signals: number;
  symbols_scanned: number;
}
