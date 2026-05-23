"""
Main trading bot loop.

Run with:
    python -m bybit_trading.trading_bot
or:
    python bybit_trading/trading_bot.py
"""

import time
import logging
import signal
import sys

from .config import config
from .bybit_client import BybitClient
from .coin_scanner import CoinScanner
from .strategy import TradingStrategy
from .risk_management import RiskManager
from .telegram_notifier import TelegramNotifier


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logger() -> logging.Logger:
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%Y-%m-%d %H:%M:%S")
    return logging.getLogger("trading_bot")


# ---------------------------------------------------------------------------
# TradingBot
# ---------------------------------------------------------------------------

class TradingBot:
    def __init__(self):
        self.log       = _setup_logger()
        self.client    = BybitClient()
        self.scanner   = CoinScanner()
        self.strategy  = TradingStrategy()
        self.risk      = RiskManager()
        self.telegram  = TelegramNotifier()
        self.running   = True

        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        mode = "DEMO" if config["demo_mode"] else "LIVE"
        self.log.info(f"🤖 Bybit Trading Bot started — mode={mode}")
        self.telegram.info(f"🤖 Bot started in <b>{mode}</b> mode")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        while self.running:
            try:
                self._cycle()
            except Exception as e:
                self.log.exception(f"Unhandled error in cycle: {e}")
                self.telegram.error(str(e))
            time.sleep(config["scan_interval_sec"])

    def _cycle(self):
        self.log.info("=== Scan cycle started ===")

        # 1. Manage existing positions (check exits)
        self._manage_positions()

        # 2. Look for new entries (only if slots available)
        if not self.risk.can_open_trade():
            self.log.info("Max positions reached — skipping entry scan")
            return

        candidates = self.scanner.get_candidates()
        self.log.info(f"Scanning {len(candidates)} symbols for entries...")

        for symbol in candidates:
            if not self.running:
                break
            if self.risk.already_in_position(symbol):
                continue
            if not self.risk.can_open_trade():
                break

            signal_data = self.strategy.analyse_symbol(symbol)
            if signal_data is None:
                continue

            self._execute_entry(signal_data)
            time.sleep(0.3)   # avoid rate-limit burst

        self.log.info("=== Scan cycle complete ===")

    # ------------------------------------------------------------------
    # Entry execution
    # ------------------------------------------------------------------

    def _execute_entry(self, signal_data: dict):
        symbol = signal_data["symbol"]
        side   = signal_data["side"]
        entry  = signal_data["entry"]
        atr    = signal_data["atr"]
        reason = signal_data["reason"]

        sl, tp = self.risk.calc_sl_tp(side, entry, atr)
        qty    = self.risk.calc_position_size(symbol, entry, sl)

        if qty is None:
            self.log.info(f"{symbol}: position size is None — skipping")
            return

        self.log.info(
            f"Signal {side} {symbol} | entry={entry:.4f} | SL={sl:.4f} | TP={tp:.4f} | qty={qty}"
        )
        self.telegram.signal(symbol, side, entry, sl, tp, reason)

        result = self.client.place_order(symbol, side, qty, sl=sl, tp=tp)
        ret_code = result.get("retCode", -1)

        if ret_code == 0:
            order_id = result.get("result", {}).get("orderId", "")
            self.log.info(f"✅ Order placed: {symbol} {side} qty={qty} orderId={order_id}")
            self.telegram.order_placed(symbol, side, qty, sl, tp)
        else:
            msg = result.get("retMsg", "unknown error")
            self.log.error(f"❌ Order failed for {symbol}: {msg}")
            self.telegram.error(f"Order failed — {symbol} {side}: {msg}")

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _manage_positions(self):
        positions = self.client.get_positions()
        for pos in positions:
            size = float(pos.get("size", 0))
            if size == 0:
                continue

            symbol = pos["symbol"]
            side   = pos["side"]

            # Refresh technical picture for exit check
            snapshots = {}
            try:
                from .technical_analysis import TechnicalAnalysis
                ta = TechnicalAnalysis(config)
                for tf in config["timeframes"]:
                    candles = self.client.get_klines(symbol, tf, config["candle_limit"])
                    snap = ta.analyse(candles)
                    if snap:
                        snapshots[tf] = snap
            except Exception as e:
                self.log.warning(f"Could not analyse {symbol} for exit: {e}")
                continue

            if self.strategy.should_exit(pos, snapshots):
                self.log.info(f"Exit signal for {symbol} {side} — closing position")
                result = self.client.close_position(symbol, side, size)
                if result.get("retCode") == 0:
                    pnl = float(pos.get("unrealisedPnl", 0))
                    self.log.info(f"✅ Closed {symbol} | PnL={pnl:+.2f}")
                    self.telegram.order_closed(symbol, side, pnl)
                else:
                    self.log.error(f"Close failed for {symbol}: {result.get('retMsg')}")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown(self, *_):
        self.log.info("Shutdown signal received — stopping bot")
        self.telegram.info("🛑 Bot stopped")
        self.running = False
        sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
