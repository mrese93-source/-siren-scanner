import logging
import requests

from .config import config

log = logging.getLogger("telegram_notifier")


class TelegramNotifier:
    def __init__(self):
        self.token   = config["telegram_token"]
        self.chat_id = config["telegram_chat_id"]
        self._base   = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def _send(self, text: str):
        if not self.token or not self.chat_id:
            log.debug("Telegram not configured — skipping notification")
            return
        try:
            requests.post(
                self._base,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception as e:
            log.error(f"Telegram send error: {e}")

    # ------------------------------------------------------------------

    def signal(self, symbol: str, side: str, entry: float, sl: float, tp: float, reason: str):
        emoji = "🟢" if side == "Buy" else "🔴"
        label = "LONG" if side == "Buy" else "SHORT"
        msg = (
            f"{emoji} <b>{label} Signal — {symbol}</b>\n"
            f"Entry:  <code>{entry:.4f}</code>\n"
            f"SL:     <code>{sl:.4f}</code>\n"
            f"TP:     <code>{tp:.4f}</code>\n"
            f"Reason: {reason}"
        )
        self._send(msg)

    def order_placed(self, symbol: str, side: str, qty: float, sl: float, tp: float):
        emoji = "📥"
        label = "LONG" if side == "Buy" else "SHORT"
        msg = (
            f"{emoji} <b>Order placed — {label} {symbol}</b>\n"
            f"Qty: <code>{qty}</code>  |  SL: <code>{sl:.4f}</code>  |  TP: <code>{tp:.4f}</code>"
        )
        self._send(msg)

    def order_closed(self, symbol: str, side: str, pnl: float = None):
        emoji = "✅" if (pnl is None or pnl >= 0) else "❌"
        label = "LONG" if side == "Buy" else "SHORT"
        pnl_str = f" | PnL: <code>{pnl:+.2f} USDT</code>" if pnl is not None else ""
        msg = f"{emoji} <b>Position closed — {label} {symbol}</b>{pnl_str}"
        self._send(msg)

    def error(self, text: str):
        self._send(f"⚠️ <b>Bot Error</b>\n{text}")

    def info(self, text: str):
        self._send(f"ℹ️ {text}")
