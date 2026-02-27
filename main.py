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
        return

def run_server():
    print(f"HTTP server on port {PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# =========================
# Config
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Cooldowns
ALERT_COOLDOWN_FAST_SEC = 45 * 60
ALERT_COOLDOWN_SLOW_SEC = 6 * 60 * 60
ALERT_COOLDOWN_FUNDING_SEC = 2 * 60 * 60

# FAST movement thresholds
MOVE_EXPLOSIVE_1M = 10.0
MOVE_1M = 2.5
MOVE_3M = 4.0
MOVE_5M = 5.0

# SLOW movement thresholds
MOVE_15M = 8.0
MOVE_1H = 15.0
MOVE_24H = 30.0

# Funding thresholds (early detection)
FUNDING_EXTREME_POS = 0.5
FUNDING_EXTREME_NEG = -0.5
FUNDING_ABS_FOR_BOOST = 0.5

TOP_N_SYMBOLS = 500
RESUBSCRIBE_EVERY_SEC = 10 * 60

# OI filter
MIN_OI_SOFT = 50_000
MAX_OI = 800_000_000

ALLOW_LOW_OI_IF_STRONG_MOVE = True
LOW_OI_FAST_SCORE_BYPASS = 7

# =========================
# 🔥 UPDATED VALUE HERE
# =========================

MIN_TURNOVER_24H_NORMAL = 400_000      # ← changed to 400K
MIN_TURNOVER_24H_EXPLOSIVE = 150_000

# Liquidity filters
ORDERBOOK_TTL_SEC = 25

MAX_SPREAD_NORMAL_PCT = 0.45
MAX_SPREAD_EXPLOSIVE_PCT = 0.80

DEPTH_BAND_NORMAL_PCT = 0.50
DEPTH_BAND_EXPLOSIVE_PCT = 0.80

MIN_DEPTH_NORMAL_USDT = 20_000
MIN_DEPTH_EXPLOSIVE_USDT = 8_000

ENABLE_1M_VOLUME_FILTER = True
MIN_1M_TURNOVER_USDT = 1200

# =========================
# State
# =========================

lock = threading.Lock()
price_history = defaultdict(lambda: deque(maxlen=2500))
symbol_metadata = {}

last_alert_fast = {}
last_alert_slow = {}
last_alert_funding = {}

anchors = {}
orderbook_cache = {}

ws_app = None
ws_lock = threading.Lock()
current_symbols = []

# =========================
# Utils
# =========================

def now_ts():
    return time.time()

def safe_float(x, default=0.0):
    try:
        return float(x)
    except:
        return default

def pct_change(old, new):
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100.0

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except:
        pass

# =========================
# Metadata updater
# =========================

def update_metadata():
    while True:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/tickers?category=linear",
                timeout=10
            )
            data = r.json().get("result", {}).get("list", [])

            with lock:
                for t in data:
                    symbol = t.get("symbol")
                    if not symbol:
                        continue

                    symbol_metadata[symbol] = {
                        "oi": safe_float(t.get("openInterestValue")),
                        "turnover24h": safe_float(t.get("turnover24h")),
                        "funding": safe_float(t.get("fundingRate")) * 100,
                        "price24hPcnt": safe_float(t.get("price24hPcnt")) * 100
                    }

        except:
            pass

        time.sleep(30)

threading.Thread(target=update_metadata, daemon=True).start()

# =========================
# Core signal checker
# =========================

def check_signals(symbol, price):

    with lock:
        prices = list(price_history[symbol])
        meta = symbol_metadata.get(symbol, {}).copy()

    if len(prices) < 5:
        return

    funding = meta.get("funding", 0)
    turnover24h = meta.get("turnover24h", 0)

    # 🔥 Turnover filter (400K)
    if turnover24h < MIN_TURNOVER_24H_NORMAL:
        return

    # Funding extreme alert
    if funding >= FUNDING_EXTREME_POS or funding <= FUNDING_EXTREME_NEG:
        send_telegram(
            f"⚠️ Funding חריג\n"
            f"<b>{symbol}</b>\n"
            f"Funding: {round(funding,3)}%\n"
            f"Price: {price}"
        )

# =========================
# WebSocket
# =========================

def on_message(ws, message):
    try:
        data = json.loads(message)
        topic = data.get("topic", "")
        if not topic.startswith("tickers."):
            return

        ticker = data.get("data", {})
        symbol = ticker.get("symbol")
        price = safe_float(ticker.get("lastPrice"))

        if not symbol or price <= 0:
            return

        with lock:
            price_history[symbol].append((now_ts(), price))

        check_signals(symbol, price)

    except:
        pass

def on_open(ws):
    print("WebSocket connected")
    symbols = get_top_symbols()
    subscribe_symbols(ws, symbols)

def subscribe_symbols(ws, symbols):
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        ws.send(json.dumps({
            "op": "subscribe",
            "args": [f"tickers.{s}" for s in batch]
        }))
        time.sleep(0.2)

def get_top_symbols():
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers?category=linear",
            timeout=10
        )
        data = r.json().get("result", {}).get("list", [])
        data.sort(key=lambda x: safe_float(x.get("turnover24h")), reverse=True)
        return [d["symbol"] for d in data[:TOP_N_SYMBOLS]]
    except:
        return ["BTCUSDT", "ETHUSDT"]

def start_websocket():
    ws = websocket.WebSocketApp(
        "wss://stream.bybit.com/v5/public/linear",
        on_message=on_message,
        on_open=on_open
    )
    ws.run_forever(ping_interval=20, ping_timeout=10)

# =========================
# Main
# =========================

if __name__ == "__main__":
    print("🚀 Scanner started (400K turnover filter)")
    time.sleep(3)

    while True:
        try:
            start_websocket()
        except:
            time.sleep(10)
