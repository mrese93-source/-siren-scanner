from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SignalOut(BaseModel):
    id: int
    symbol: str
    direction: str
    signal_type: str
    confidence: float
    risk_level: str
    ai_score: float
    price: float
    entry_low: Optional[float]
    entry_high: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    risk_reward: Optional[float]
    change_1m: Optional[float]
    change_5m: Optional[float]
    change_15m: Optional[float]
    change_1h: Optional[float]
    change_24h: Optional[float]
    volume_24h: Optional[float]
    volume_spike_ratio: Optional[float]
    open_interest: Optional[float]
    funding_rate: Optional[float]
    explanation: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ScannerStatus(BaseModel):
    last_scan: Optional[datetime]
    total_signals_today: int
    active_signals: int
    symbols_scanned: int
