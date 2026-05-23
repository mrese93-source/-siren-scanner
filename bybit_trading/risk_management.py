import logging
import math
from typing import Optional, Tuple

from .config import config
from .bybit_client import BybitClient

log = logging.getLogger("risk_management")


class RiskManager:
    def __init__(self):
        self.client = BybitClient()

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calc_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
    ) -> Optional[float]:
        """
        Returns quantity (contracts) based on risk % per trade and SL distance.
        Returns None if sizing is impossible (zero risk, no balance, etc.).
        """
        balance = self.client.get_balance()
        if balance <= 0:
            log.warning("Balance is 0 — cannot size position.")
            return None

        risk_usdt = balance * (config["risk_pct_per_trade"] / 100)
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance == 0:
            log.warning(f"SL distance is 0 for {symbol}")
            return None

        raw_qty = risk_usdt / sl_distance

        # Round to instrument step size
        info = self.client.get_instrument_info(symbol)
        qty_step = self._parse_step(info, "qtyStep", 0.001)
        min_qty  = self._parse_step(info, "minOrderQty", qty_step)

        qty = self._round_down(raw_qty, qty_step)
        if qty < min_qty:
            log.info(f"{symbol}: sized qty {qty} < minQty {min_qty}, skipping")
            return None

        log.info(
            f"{symbol} | balance={balance:.2f} USDT | risk={risk_usdt:.2f} USDT | "
            f"SL dist={sl_distance:.4f} | qty={qty}"
        )
        return qty

    # ------------------------------------------------------------------
    # SL / TP calculation
    # ------------------------------------------------------------------

    def calc_sl_tp(
        self,
        side: str,
        entry: float,
        atr: float,
    ) -> Tuple[float, float]:
        """Calculate SL and TP based on ATR."""
        sl_dist = atr * config["sl_atr_mult"]
        tp_dist = sl_dist * config["tp_rr_ratio"]

        if side == "Buy":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        return round(sl, 6), round(tp, 6)

    # ------------------------------------------------------------------
    # Gate checks
    # ------------------------------------------------------------------

    def can_open_trade(self) -> bool:
        positions = self.client.get_positions()
        open_count = sum(1 for p in positions if float(p.get("size", 0)) != 0)
        if open_count >= config["max_open_trades"]:
            log.info(f"Max open trades reached ({open_count}/{config['max_open_trades']})")
            return False
        return True

    def already_in_position(self, symbol: str) -> bool:
        positions = self.client.get_positions()
        for p in positions:
            if p.get("symbol") == symbol and float(p.get("size", 0)) != 0:
                return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_step(info: dict, key: str, default: float) -> float:
        try:
            lot_size_filter = info.get("lotSizeFilter", {})
            return float(lot_size_filter.get(key, default))
        except Exception:
            return default

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        if step <= 0:
            return value
        factor = 1 / step
        return math.floor(value * factor) / factor
