import os
import time
import json
import threading
from collections import deque, defaultdict

import requests
import websocket

# =========================
# Config
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

HTTP_TIMEOUT = 10

session = requests.Session()

# =========================
# State
# =========================

lock = threading.Lock()

price_history = defaultdict(lambda: deque(maxlen=2500))
oi_history = defaultdict(lambda: deque(maxlen=20))
funding_history = defaultdict(lambda: deque(maxlen=20))
volume_history = defaultdict(lambda: deque(maxlen=20))

symbol_metadata = {}

last_fast_alert = {}
last_slow_alert = {}
last_whale_alert = {}
last_liq_alert = {}

# =========================
# Utils
# =========================

def now_ts():
    return time.time()

def safe_float(x):
    try:
        return float(x)
    except:
        return 0.0

def pct_change(a, b):
    if a <= 0:
        return 0.0
    return ((b - a) / a) * 100.0

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)

# =========================
# Whale Detection
# =========================

def check_whale(symbol, price):
    ts = now_ts()

    if symbol in last_whale_alert and ts - last_whale_alert[symbol] < 3600:
        return

    oi = list(oi_history[symbol])
    if len(oi) < 3:
        return

    change = pct_change(oi[-2], oi[-1])

    if change >= 3:
        msg = f"🐋 Whale Accumulation\n{symbol}\nPrice: {price}\nOI +{round(change,2)}%"
    elif change <= -3:
        msg = f"🐋 Whale Distribution\n{symbol}\nPrice: {price}\nOI {round(change,2)}%"
    else:
        return

    send_telegram(msg)
    last_whale_alert[symbol] = ts

# =========================
# Liquidity Zones
# =========================

def check_liquidity(symbol, price):
    ts = now_ts()

    if symbol in last_liq_alert and ts - last_liq_alert[symbol] < 1800:
        return

    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=15&limit=50"
        data = session.get(url, timeout=5).json()

        highs = [float(c[2]) for c in data["result"]["list"]]
        lows = [float(c[3]) for c in data["result"]["list"]]

        alerts = []

        for h in highs:
            d = abs(h - price) / price * 100
            if d < 1:
                alerts.append(f"⬆️ {round(h,2)} ({round(d,2)}%)")

        for l in lows:
            d = abs(l - price) / price * 100
            if d < 1:
                alerts.append(f"⬇️ {round(l,2)} ({round(d,2)}%)")

        if alerts:
            msg = f"💧 Liquidity Alert\n{symbol}\nPrice: {price}\n" + "\n".join(alerts[:3])
            send_telegram(msg)
            last_liq_alert[symbol] = ts

    except Exception as e:
        print("Liquidity error:", e)

# =========================
# Signals
# =========================

def check_signals(symbol, price):
    data = price_history[symbol]

    if len(data) < 5:
        return

    c1 = pct_change(data[0][1], data[-1][1])
    c3 = pct_change(data[-3][1], data[-1][1])
    c5 = pct_change(data[-5][1], data[-1][1])

    score = 0

    if abs(c1) > 2:
        score += 2
    if abs(c3) > 3:
        score += 2
    if abs(c5) > 5:
        score += 2

    if score < 4:
        return

    direction = "🟢 LONG" if c1 > 0 else "🔴 SHORT"

    msg = f"{direction} {symbol}\n1m: {round(c1,2)}%\n3m: {round(c3,2)}%\n5m: {round(c5,2)}%\nPrice: {price}"

    send_telegram(msg)

# =========================
# Metadata Loop
# =========================

def metadata_loop():
    while True:
        try:
            data = session.get("https://api.bybit.com/v5/market/tickers?category=linear", timeout=10).json()

            for t in data["result"]["list"]:
                s = t["symbol"]

                oi = safe_float(t["openInterestValue"])
                funding = safe_float(t["fundingRate"]) * 100
                volume = safe_float(t["turnover24h"])

                oi_history[s].append(oi)
                funding_history[s].append(funding)
                volume_history[s].append(volume)

                symbol_metadata[s] = {
                    "oi": oi,
                    "funding": funding
                }

        except Exception as e:
            print("Metadata error:", e)

        time.sleep(30)

# =========================
# WebSocket
# =========================

def on_message(ws, message):
    try:
        data = json.loads(message)
        topic = data.get("topic", "")

        if not topic.startswith("tickers."):
            return

        d = data.get("data", {})
        symbol = d.get("symbol")
        price = safe_float(d.get("lastPrice"))

        if not symbol or price <= 0:
            return

        ts = now_ts()

        with lock:
            price_history[symbol].append((ts, price))

        check_signals(symbol, price)
        check_whale(symbol, price)
        check_liquidity(symbol, price)

    except Exception as e:
        print("WS message error:", e)

def on_open(ws):
    print("WS Connected")

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    for s in symbols:
        ws.send(json.dumps({
            "op": "subscribe",
            "args": [f"tickers.{s}"]
        }))

def start_ws():
    ws = websocket.WebSocketApp(
        "wss://stream.bybit.com/v5/public/linear",
        on_open=on_open,
        on_message=on_message
    )
    ws.run_forever(ping_interval=20)

# =========================
# Main
# =========================

if __name__ == "__main__":
    print("BOT STARTED")

    threading.Thread(target=metadata_loop, daemon=True).start()

    while True:
        try:
            start_ws()
        except Exception as e:
            print("CRASH:", e)
            time.sleep(5)
