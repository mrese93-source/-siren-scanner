import os
import time
import json
import threading
from collections import deque, defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import websocket  # pip install websocket-client

# =========================
# Railway keep-alive server
# =========================

PORT = int(os.environ.get("PORT", "8080"))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return  # silence default logging


def run_server():
    print(f"HTTP server on port {PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


threading.Thread(target=run_server, daemon=True).start()

# =========================
# Config
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Alert cooldown
ALERT_COOLDOWN_SEC = 4 * 60 * 60  # 4 hours

# Movement thresholds (real-time)
MOVE_1MIN_STRONG = 2.5  # 2.5% in 1 minute
MOVE_3MIN_STRONG = 4.0  # 4% in 3 minutes
MOVE_5MIN_STRONG = 5.0  # 5% in 5 minutes

# OI filter
MIN_OI = 1_000_000
MAX_OI = 300_000_000

# =========================
# State
# =========================

# symbol -> deque of (timestamp, price)
price_history = defaultdict(lambda: deque(maxlen=600))  # ~10 minutes

# symbol -> last alert timestamp
alerted = {}

# symbol -> metadata dict
symbol_metadata = {}

# Lock for thread safety
lock = threading.Lock()

# =========================
# Utils
# =========================

def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def send_telegram(msg: str):
    try:
        if not TELEGRAM_TOKEN or not CHAT_ID:
            print("⚠️ Missing TELEGRAM_TOKEN or CHAT_ID", flush=True)
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code != 200:
            print(f"Telegram API error: {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)


def calc_change(prices: deque, lookback_sec: int) -> float:
    """
    Calculate % change over last N seconds.
    prices: deque of (timestamp, price)
    """
    if len(prices) < 2:
        return 0.0

    now = time.time()
    target_time = now - lookback_sec

    old_price = None
    # find first price with ts >= target_time
    for ts, p in prices:
        if ts >= target_time:
            old_price = p
            break

    if old_price is None or old_price == 0:
        return 0.0

    curr_price = prices[-1][1]
    return ((curr_price - old_price) / old_price) * 100.0


# =========================
# Metadata updater (REST every 30s)
# =========================

def update_metadata():
    """
    Fetch OI, volume, funding from REST API every 30s
    """
    while True:
        try:
            url = "https://api.bybit.com/v5/market/tickers?category=linear"
            r = requests.get(url, timeout=10)
            data = r.json()

            tickers = data.get("result", {}).get("list", []) or []

            with lock:
                for t in tickers:
                    symbol = t.get("symbol") or ""
                    if not symbol:
                        continue

                    oi = safe_float(t.get("openInterestValue", 0))
                    turnover24h = safe_float(t.get("turnover24h", 0))
                    funding = safe_float(t.get("fundingRate", 0)) * 100.0
                    volume24h = safe_float(t.get("volume24h", 0))

                    symbol_metadata[symbol] = {
                        "oi": oi,
                        "turnover24h": turnover24h,
                        "funding": funding,
                        "volume24h": volume24h,
                        "last_update": time.time()
                    }

            print(f"✅ Updated metadata for {len(tickers)} symbols", flush=True)

        except Exception as e:
            print(f"Metadata error: {e}", flush=True)

        time.sleep(30)


threading.Thread(target=update_metadata, daemon=True).start()

# =========================
# Symbol list
# =========================

def get_top_symbols():
    """
    Get top symbols by turnover among those passing OI filter.
    Returns list like ['BTCUSDT', 'ETHUSDT', ...]
    """
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        data = r.json()
        tickers = data.get("result", {}).get("list", []) or []

        valid = []
        for t in tickers:
            symbol = t.get("symbol") or ""
            oi = safe_float(t.get("openInterestValue", 0))
            turnover = safe_float(t.get("turnover24h", 0))
            if symbol and (MIN_OI <= oi <= MAX_OI):
                valid.append((symbol, turnover))

        valid.sort(key=lambda x: x[1], reverse=True)
        top_symbols = [s for s, _ in valid[:100]]

        print(f"Tracking {len(top_symbols)} symbols", flush=True)
        return top_symbols

    except Exception as e:
        print(f"Error getting symbols: {e}", flush=True)
        return [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "MATICUSDT", "DOTUSDT", "AVAXUSDT"
        ]


# =========================
# Signal checker
# =========================

def check_signal(symbol: str, price: float):
    """
    Decide whether to send LONG/SHORT alert based on recent movement.
    """
    now = time.time()

    with lock:
        # cooldown
        last = alerted.get(symbol)
        if last and (now - last) < ALERT_COOLDOWN_SEC:
            return

        prices = price_history[symbol]
        if len(prices) < 30:
            return

        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)

        # OI filter
        if oi < MIN_OI or oi > MAX_OI:
            return

        funding = meta.get("funding", 0)
        change_1m = calc_change(prices, 60)
        change_3m = calc_change(prices, 180)
        change_5m = calc_change(prices, 300)

        signals = []
        score = 0

        # LONG
        if change_1m >= MOVE_1MIN_STRONG:
            signals.append(f"🚀 +{round(change_1m, 1)}% תוך דקה!")
            score += 4
        if change_3m >= MOVE_3MIN_STRONG:
            signals.append(f"📈 +{round(change_3m, 1)}% ב-3 דקות")
            score += 3
        if change_5m >= MOVE_5MIN_STRONG:
            signals.append(f"⚡ +{round(change_5m, 1)}% ב-5 דקות")
            score += 2

        # SHORT
        if change_1m <= -MOVE_1MIN_STRONG:
            signals.append(f"💥 {round(change_1m, 1)}% תוך דקה!")
            score += 4
        if change_3m <= -MOVE_3MIN_STRONG:
            signals.append(f"📉 {round(change_3m, 1)}% ב-3 דקות")
            score += 3
        if change_5m <= -MOVE_5MIN_STRONG:
            signals.append(f"⚡ {round(change_5m, 1)}% ב-5 דקות")
            score += 2

        # acceleration
        if abs(change_1m) > abs(change_3m) * 0.7 and abs(change_1m) >= 2.0:
            signals.append("⚡ התנעה מואצת")
            score += 2

        if score < 4:
            return

        # direction rules (יותר יציב)
        is_long = (change_1m > 0 and change_3m > 0)
        is_short = (change_1m < 0 and change_3m < 0)

        if not (is_long or is_short):
            return

        direction = "🟢 LONG" if is_long else "🔴 SHORT"

        if score >= 8:
            strength = "🔥🔥🔥🔥 קריטי!"
        elif score >= 6:
            strength = "🔥🔥🔥 חזק מאוד"
        else:
            strength = "🔥🔥 חזק"

        signals_txt = "\n".join(f"- {s}" for s in signals)

        msg = (
            f"{direction} <b>{symbol}</b>\n"
            f"{strength}\n\n"
            f"<b>סיגנלים:</b>\n{signals_txt}\n\n"
            f"⏱️ 1m: {round(change_1m, 1)}% | 3m: {round(change_3m, 1)}% | 5m: {round(change_5m, 1)}%\n"
            f"💵 מחיר: ${price}\n"
            f"💹 Funding: {round(funding, 4)}%\n"
            f"📊 OI: ${round(oi/1_000_000, 1)}M"
        )

        send_telegram(msg)
        alerted[symbol] = now
        print(f"🎯 ALERT: {symbol} {direction} | 1m={round(change_1m,1)}% | score={score}", flush=True)


# =========================
# WebSocket handlers
# =========================

def on_message(ws, message: str):
    try:
        data = json.loads(message)

        topic = data.get("topic")
        if not topic or not topic.startswith("tickers."):
            return

        ticker_data = data.get("data") or {}
        symbol = ticker_data.get("symbol") or ""
        last_price = safe_float(ticker_data.get("lastPrice", 0))

        if not symbol or last_price <= 0:
            return

        now = time.time()

        with lock:
            price_history[symbol].append((now, last_price))

        check_signal(symbol, last_price)

    except Exception as e:
        print(f"Message error: {e}", flush=True)


def on_error(ws, error):
    print(f"WebSocket error: {error}", flush=True)


def on_close(ws, close_status_code, close_msg):
    print(f"WebSocket closed ({close_status_code}) {close_msg}", flush=True)
    # reconnect handled by outer loop


def on_open(ws):
    print("WebSocket connected!", flush=True)

    symbols = get_top_symbols()

    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        topics = [f"tickers.{s}" for s in batch]

        subscribe_msg = {"op": "subscribe", "args": topics}
        ws.send(json.dumps(subscribe_msg))

        print(f"Subscribed to {len(batch)} symbols (batch {i//batch_size + 1})", flush=True)
        time.sleep(0.5)


def start_websocket():
    ws_url = "wss://stream.bybit.com/v5/public/linear"

    ws = websocket.WebSocketApp(
        ws_url,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )

    # ping כדי לשמור חיבור חי
    ws.run_forever(ping_interval=20, ping_timeout=10)


# =========================
# Main
# =========================

if __name__ == "__main__":
    print("🚀 Starting REAL-TIME scanner…", flush=True)
    print("⚡ WebSocket: תופס תנועות תוך שניות", flush=True)

    print("⏳ Waiting for initial metadata...", flush=True)
    time.sleep(3)

    while True:
        try:
            start_websocket()
        except Exception as e:
            print(f"WebSocket crashed: {e}", flush=True)

        print("Reconnecting in 10s...", flush=True)
        time.sleep(10)
