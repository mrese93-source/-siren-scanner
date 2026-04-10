import os
import time
import json
import threading
from collections import deque, defaultdict

import requests
import websocket

# =========================
# CONFIG
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

session = requests.Session()

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

session.headers.update(HEADERS)

lock = threading.Lock()

# =========================
# STATE
# =========================

price_history = defaultdict(lambda: deque(maxlen=2000))
last_price_cache = {}

ws_app = None

# =========================
# UTILS
# =========================

def now_ts():
    return time.time()


def safe_float(x):
    try:
        return float(x)
    except:
        return 0.0


def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing Telegram config")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        session.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Telegram error:", e)


# =========================
# PRICE TRACKING
# =========================

def update_price(symbol, price):
    ts = now_ts()
    with lock:
        price_history[symbol].append((ts, price))
        last_price_cache[symbol] = price


def calc_change(hist):
    if len(hist) < 2:
        return 0.0
    old = hist[0][1]
    new = hist[-1][1]
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100


# =========================
# SIGNAL ENGINE (CORE)
# =========================

def check_signals(symbol, price):
    with lock:
        hist = list(price_history[symbol])

    if len(hist) < 10:
        return

    change = calc_change(hist)

    if abs(change) < 2:
        return

    direction = "🟢 LONG" if change > 0 else "🔴 SHORT"

    strength = ""
    if abs(change) > 10:
        strength = "🔥🔥🔥"
    elif abs(change) > 5:
        strength = "🔥🔥"
    else:
        strength = "🔥"

    msg = (
        f"{strength} {direction}\n"
        f"{symbol}\n"
        f"Change: {round(change, 2)}%"
    )

    send_telegram(msg)


# =========================
# WEBSOCKET
# =========================

def on_message(ws, message):
    try:
        data = json.loads(message)

        if "topic" not in data:
            return

        d = data.get("data", {})
        symbol = d.get("symbol")
        price = safe_float(d.get("lastPrice"))

        if not symbol or price <= 0:
            return

        update_price(symbol, price)
        check_signals(symbol, price)

    except Exception as e:
        print("WS error:", e)


def on_open(ws):
    print("WS connected")


def on_error(ws, error):
    print("WS error:", error)


def on_close(ws, code, msg):
    print("WS closed:", code, msg)


def start_ws():
    global ws_app

    ws = websocket.WebSocketApp(
        "wss://stream.bybit.com/v5/public/linear",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    ws_app = ws
    ws.run_forever(ping_interval=20, ping_timeout=10)


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    print("🚀 BOT V4 STABLE STARTED")

    while True:
        try:
            start_ws()
        except Exception as e:
            print("Restarting WS:", e)
            time.sleep(5)
