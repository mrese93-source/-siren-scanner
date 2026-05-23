"""
Multi-timeframe trading strategy.

Entry logic (LONG):
  - Daily / Weekly trend: EMA bullish stack (fast > mid > slow)
  - 4H: MACD cross up + RSI not overbought + volume spike
  - 4H price above EMA slow

Entry logic (SHORT):
  - Daily / Weekly trend: EMA bearish stack
  - 4H: MACD cross down + RSI not oversold + volume spike
  - 4H price below EMA slow
"""

import logging
from typing import Optional, Dict

from .config import config
from .technical_analysis import TechnicalAnalysis
from .bybit_client import BybitClient

log = logging.getLogger("strategy")


class TradingStrategy:
    def __init__(self):
        self.ta     = TechnicalAnalysis(config)
        self.client = BybitClient()

    def analyse_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Returns a trade signal dict or None.

        Signal dict keys: symbol, side, entry, atr, reason, tf_snapshots
        """
        snapshots = {}
        for tf in config["timeframes"]:
            candles = self.client.get_klines(symbol, tf, config["candle_limit"])
            snap = self.ta.analyse(candles)
            if snap is None:
                return None
            snapshots[tf] = snap

        primary = snapshots[config["primary_tf"]]   # 4H
        trend   = snapshots[config["trend_tf"]]     # Daily

        # ---- LONG ----
        if (
            trend["ema_bullish"]
            and primary["macd_cross_up"]
            and primary["rsi"] < config["rsi_ob"]
            and primary["vol_spike"]
            and primary["price_above_ema_slow"]
        ):
            return {
                "symbol":       symbol,
                "side":         "Buy",
                "entry":        primary["close"],
                "atr":          primary["atr"],
                "reason":       "EMA bullish + MACD cross up + vol spike",
                "tf_snapshots": snapshots,
            }

        # ---- SHORT ----
        if (
            trend["ema_bearish"]
            and primary["macd_cross_down"]
            and primary["rsi"] > config["rsi_os"]
            and primary["vol_spike"]
            and primary["price_below_ema_slow"]
        ):
            return {
                "symbol":       symbol,
                "side":         "Sell",
                "entry":        primary["close"],
                "atr":          primary["atr"],
                "reason":       "EMA bearish + MACD cross down + vol spike",
                "tf_snapshots": snapshots,
            }

        return None

    def should_exit(self, position: Dict, snapshots: Dict[str, Dict]) -> bool:
        """Simple exit: opposite MACD cross on primary TF."""
        side    = position.get("side", "")
        primary = snapshots.get(config["primary_tf"])
        if primary is None:
            return False
        if side == "Buy"  and primary["macd_cross_down"]:
            return True
        if side == "Sell" and primary["macd_cross_up"]:
            return True
        return False
