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

HEADERS = {"User-Agent": "Mozilla/5.0"}

# thresholds
MOVE_1M = 2.5
MOVE_5M = 5.0
MOVE_15M = 8.0

VOLUME_SPIKE = 2.0
CVD_DIVERGENCE = 4.0
WHALE_MOVE = 6.0
ACCUM_STABILITY = 1.2

ORDERBOOK_DEPTH_MIN = 15000
SPREAD_MAX = 0.6

# =========================
# STATE
# =========================

lock = threading.Lock()

price_history = defaultdict(lambda: deque(maxlen=2000))
volume_history = defaultdict(lambda: deque(maxlen=200))

symbol_meta = {}
last_price = {}

cvd = defaultdict(float)
cvd_hist = defaultdict(lambda: deque(maxlen=200))

last_alert = defaultdict(float)

session = requests.Session()
session.headers.update(HEADERS)

# =========================
# UTILS
# =========================

def now():
    return time.time()

def f(x):
    try:
        return float(x)
    except:
        return 0.0

def pct(a, b):
    if a <= 0:
        return 0
    return ((b - a) / a) * 100

def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        session.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

def get_json(url):
    try:
        return session.get(url, timeout=8).json()
    except:
        return None

# =========================
# METADATA
# =========================

def update_meta():
    while True:
        data = get_json("https://api.bybit.com/v5/market/tickers?category=linear")
        if data:
            with lock:
                for t in data.get("result", {}).get("list", []):
                    s = t.get("symbol")
                    if not s:
                        continue
                    symbol_meta[s] = {
                        "oi": f(t.get("openInterestValue")),
                        "funding": f(t.get("fundingRate")) * 100,
                        "vol": f(t.get("turnover24h")),
                    }
        time.sleep(20)

# =========================
# ORDERBOOK (LIGHT)
# =========================

def orderbook(symbol):
    url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=25"
    d = get_json(url)
    if not d:
        return None

    try:
        ob = d["result"]
        bids = ob["b"]
        asks = ob["a"]

        best_bid = f(bids[0][0])
        best_ask = f(asks[0][0])

        mid = (best_bid + best_ask) / 2
        spread = ((best_ask - best_bid) / mid) * 100

        depth = sum(f(b[1]) for b in bids[:10]) + sum(f(a[1]) for a in asks[:10])

        return {
            "spread": spread,
            "depth": depth
        }
    except:
        return None

# =========================
# CORE ENGINE
# =========================

def engine(symbol, price):
    with lock:
        hist = price_history[symbol]
        meta = symbol_meta.get(symbol, {})
        prev = last_price.get(symbol, 0)

    if len(hist) < 30:
        return

    p1 = pct(hist[-2][1], price)
    p5 = pct(hist[-6][1], price)
    p15 = pct(hist[-20][1], price)

    funding = meta.get("funding", 0)
    vol = meta.get("vol", 0)

    score = 0
    signals = []

    # momentum
    if abs(p1) > MOVE_1M:
        signals.append(f"1m {round(p1,2)}%")
        score += 3

    if abs(p5) > MOVE_5M:
        signals.append(f"5m {round(p5,2)}%")
        score += 3

    if abs(p15) > MOVE_15M:
        signals.append(f"15m {round(p15,2)}%")
        score += 4

    # funding
    if abs(funding) > 0.3:
        signals.append(f"funding {round(funding,4)}%")
        score += 2

    # volume spike
    if len(hist) > 10:
        vspike = abs(pct(hist[-10][1], price))
        if vspike > VOLUME_SPIKE:
            signals.append("volume spike")
            score += 2

    # whale move
    if abs(p5) > WHALE_MOVE:
        signals.append("whale move detected")
        score += 3

    # CVD
    cvd_delta = cvd[symbol]
    if abs(cvd_delta) > CVD_DIVERGENCE:
        signals.append("CVD pressure")
        score += 3

    # ACCUMULATION
    stability = abs(pct(hist[0][1], price))
    if stability < ACCUM_STABILITY and vol > 400000:
        signals.append("accumulation zone")
        score += 2

    # ORDERBOOK FILTER
    ob = orderbook(symbol)
    if ob:
        if ob["spread"] > SPREAD_MAX:
            return
        if ob["depth"] < ORDERBOOK_DEPTH_MIN:
            return

    # FINAL DECISION
    if score < 7:
        return

    direction = "LONG" if p1 > 0 else "SHORT"

    send(
        f"🚨 PRO+ ULTIMATE {symbol}\n"
        f"{direction}\n"
        + "\n".join(signals)
        + f"\nPrice: {price} | Score: {score}"
    )

# =========================
# CVD
# =========================

def update_cvd(symbol, price, prev):
    if prev <= 0:
        return
    delta = price - prev
    cvd[symbol] += delta
    cvd_hist[symbol].append((now(), cvd[symbol]))

# =========================
# WS
# =========================

def on_message(ws, msg):
    try:
        data = json.loads(msg)

        if "tickers." not in data.get("topic", ""):
            return

        d = data["data"]
        symbol = d.get("symbol")
        price = f(d.get("lastPrice"))

        if not symbol or price <= 0:
            return

        ts = now()

        with lock:
            prev = last_price.get(symbol, 0)
            price_history[symbol].append((ts, price))
            last_price[symbol] = price

        engine(symbol, price)
        update_cvd(symbol, price, prev)

    except:
        pass

def on_open(ws):
    print("PRO+ ULTIMATE ONLINE")

def start():
    ws = websocket.WebSocketApp(
        "wss://stream.bybit.com/v5/public/linear",
        on_message=on_message,
        on_open=on_open
    )

    ws.run_forever(ping_interval=20, ping_timeout=10)

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    print("🔥 PRO+ ULTIMATE STARTED")

    threading.Thread(target=update_meta, daemon=True).start()

    while True:
        try:
            start()
        except Exception as e:
            print("restart", e)
            time.sleep(5)
