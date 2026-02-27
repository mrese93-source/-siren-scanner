import os
import time
import json
import threading
from collections import deque, defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import websocket  # pip install websocket-client

# =========================
# Railway keep-alive
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

# Alert cooldown (signals)
ALERT_COOLDOWN_SEC = 4 * 60 * 60  # 4 hours

# ✅ Funding alert cooldown (separate, per symbol)
FUNDING_ALERT_COOLDOWN_SEC = 60 * 60  # 1 hour

# Movement thresholds (real-time)
MOVE_EXPLOSIVE = 10.0
MOVE_1MIN_STRONG = 2.5
MOVE_3MIN_STRONG = 4.0
MOVE_5MIN_STRONG = 5.0

# OI filter
MIN_OI = 100_000
MAX_OI = 500_000_000

# ✅ Funding extreme thresholds (in % because we multiply by 100 below)
FUNDING_EXTREME_NEGATIVE = -0.5
FUNDING_EXTREME_POSITIVE = 0.5

# Track top symbols count (same as your 150)
TOP_SYMBOLS = 150

# =========================
# State
# =========================

price_history = defaultdict(lambda: deque(maxlen=600))  # ~10 min history
alerted = {}            # last signal alert time per symbol
funding_alerted = {}    # ✅ last funding-only alert time per symbol
symbol_metadata = {}    # updated every 30s

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

def calc_change(prices, lookback_sec):
    """
    Calculate price change over last N seconds
    prices: deque of (timestamp, price)
    """
    if len(prices) < 2:
        return 0.0

    now = time.time()
    target_time = now - lookback_sec

    old_price = None
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
            tickers = (data.get("result") or {}).get("list", []) or []

            with lock:
                for t in tickers:
                    symbol = t.get("symbol", "")
                    if not symbol:
                        continue

                    oi = safe_float(t.get("openInterestValue", 0))
                    turnover24h = safe_float(t.get("turnover24h", 0))
                    funding = safe_float(t.get("fundingRate", 0)) * 100.0  # ✅ %
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
# Funding alert (ONLY funding extreme)
# =========================

def maybe_send_funding_alert(symbol: str, funding: float, price: float):
    """
    Sends a separate alert only when funding is extreme (>= 0.5% or <= -0.5%),
    with 1h cooldown per symbol to prevent spam.
    """
    if not (funding >= FUNDING_EXTREME_POSITIVE or funding <= FUNDING_EXTREME_NEGATIVE):
        return

    now = time.time()
    last = funding_alerted.get(symbol)
    if last is not None and (now - last) < FUNDING_ALERT_COOLDOWN_SEC:
        return

    direction = "🟢 Bias LONG" if funding < 0 else "🔴 Bias SHORT"
    state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        f"{direction} <b>{symbol}</b>\n"
        f"⚠️ <b>Funding חריג</b>\n\n"
        f"💹 Funding: <b>{round(funding, 4)}%</b> ({state})\n"
        f"💵 מחיר: ${price}\n"
    )

    send_telegram(msg)
    funding_alerted[symbol] = now
    print(f"⚠️ FUNDING ALERT: {symbol} funding={funding}%", flush=True)

# =========================
# Signal checker
# =========================

def check_signal(symbol, price):
    """
    Check if symbol has a signal based on real-time price movement
    """
    now = time.time()

    # Cooldown check
    if symbol in alerted and now - alerted[symbol] < ALERT_COOLDOWN_SEC:
        return

    with lock:
        prices = price_history[symbol]

        # Need some history (kept as your original)
        if len(prices) < 30:
            return

        # Get metadata
        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)
        funding = meta.get("funding", 0)
        volume24h = meta.get("volume24h", 0)

    # Filter by OI
    if oi < MIN_OI or oi > MAX_OI:
        return

    # Calculate changes
    change_1m = calc_change(prices, 60)
    change_3m = calc_change(prices, 180)
    change_5m = calc_change(prices, 300)

    # Detect signals
    signals = []
    score = 0

    # LONG signals
    if change_1m >= MOVE_EXPLOSIVE:
        signals.append(f"💣💣💣 +{round(change_1m, 1)}% תוך דקה - PUMP!")
        score += 7
    elif change_1m >= MOVE_1MIN_STRONG:
        signals.append(f"🚀 +{round(change_1m, 1)}% תוך דקה!")
        score += 4

    if change_3m >= MOVE_3MIN_STRONG:
        signals.append(f"📈 +{round(change_3m, 1)}% ב-3 דקות")
        score += 3

    if change_5m >= MOVE_5MIN_STRONG:
        signals.append(f"⚡ +{round(change_5m, 1)}% ב-5 דקות")
        score += 2

    # SHORT signals
    if change_1m <= -MOVE_EXPLOSIVE:
        signals.append(f"💣💣💣 {round(change_1m, 1)}% תוך דקה - DUMP!")
        score += 7
    elif change_1m <= -MOVE_1MIN_STRONG:
        signals.append(f"💥 {round(change_1m, 1)}% תוך דקה!")
        score += 4

    if change_3m <= -MOVE_3MIN_STRONG:
        signals.append(f"📉 {round(change_3m, 1)}% ב-3 דקות")
        score += 3

    if change_5m <= -MOVE_5MIN_STRONG:
        signals.append(f"⚡ {round(change_5m, 1)}% ב-5 דקות")
        score += 2

    # Momentum acceleration
    if abs(change_1m) > abs(change_3m) * 0.7 and abs(change_1m) >= 2.0:
        signals.append("⚡ התנעה מואצת")
        score += 2

    # ✅ Funding squeeze logic (kept, but thresholds are ±0.5%)
    if funding <= FUNDING_EXTREME_NEGATIVE and change_1m > 0:
        signals.append(f"💣 Funding קיצוני: {round(funding, 2)}% - SHORT SQUEEZE אפשרי!")
        score += 5
    elif funding >= FUNDING_EXTREME_POSITIVE and change_1m < 0:
        signals.append(f"💣 Funding קיצוני: +{round(funding, 2)}% - LONG SQUEEZE אפשרי!")
        score += 5

    # Need strong signal
    if score < 4:
        return

    # Determine direction
    is_long = change_1m > 0 and change_3m > 0
    is_short = change_1m < 0 and change_3m < 0

    if not (is_long or is_short):
        return

    direction = "🟢 LONG" if is_long else "🔴 SHORT"

    # Strength
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
# WebSocket
# =========================

def get_top_symbols():
    """
    Get top symbols by turnover24h (filtered by OI)
    """
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        data = r.json()
        tickers = (data.get("result") or {}).get("list", []) or []

        valid_tickers = []
        for t in tickers:
            symbol = t.get("symbol", "")
            oi = safe_float(t.get("openInterestValue", 0))
            turnover = safe_float(t.get("turnover24h", 0))

            if symbol and (MIN_OI <= oi <= MAX_OI):
                valid_tickers.append((symbol, turnover))

        valid_tickers.sort(key=lambda x: x[1], reverse=True)
        top_symbols = [s for s, _ in valid_tickers[:TOP_SYMBOLS]]

        print(f"📡 Tracking {len(top_symbols)} symbols (including small caps)", flush=True)
        return top_symbols

    except Exception as e:
        print(f"Error getting symbols: {e}", flush=True)
        return [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "MATICUSDT", "DOTUSDT", "AVAXUSDT"
        ]

def subscribe_symbols(ws, symbols):
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        topics = [f"tickers.{s}" for s in batch]
        ws.send(json.dumps({"op": "subscribe", "args": topics}))
        print(f"📨 Subscribed batch {i//batch_size + 1}: {len(batch)} symbols", flush=True)
        time.sleep(0.6)  # חשוב כדי לא לקבל rate-limit

def on_message(ws, message):
    try:
        data = json.loads(message)

        # ✅ subscribe ack/errors (helps debug when only 1 coin works)
        if "success" in data:
            if not data.get("success", False):
                print(f"❌ WS ACK error: {data}", flush=True)
            return

        if "topic" not in data:
            return

        topic = data["topic"]
        if not topic.startswith("tickers."):
            return

        ticker_data = data.get("data", {}) or {}
        symbol = ticker_data.get("symbol", "")
        last_price = safe_float(ticker_data.get("lastPrice", 0))

        if not symbol or last_price <= 0:
            return

        now = time.time()

        # Store price
        with lock:
            price_history[symbol].append((now, last_price))
            funding = (symbol_metadata.get(symbol, {}) or {}).get("funding", 0.0)

        # ✅ Funding alert (independent)
        maybe_send_funding_alert(symbol, funding, last_price)

        # Check signal
        check_signal(symbol, last_price)

    except Exception as e:
        print(f"Message error: {e}", flush=True)

def on_error(ws, error):
    print(f"WebSocket error: {error}", flush=True)

def on_close(ws, close_status_code, close_msg):
    # ✅ do NOT reconnect from here (prevents recursion/duplicated sockets)
    print(f"WebSocket closed ({close_status_code}) {close_msg}", flush=True)

def on_open(ws):
    print("WebSocket connected!", flush=True)
    symbols = get_top_symbols()
    subscribe_symbols(ws, symbols)
    print(f"✅ Subscribed initial: {len(symbols)} symbols", flush=True)

def start_websocket():
    ws_url = "wss://stream.bybit.com/v5/public/linear"
    ws = websocket.WebSocketApp(
        ws_url,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    ws.run_forever(ping_interval=20, ping_timeout=10)

# =========================
# Main
# =========================

if __name__ == "__main__":
    print("🚀 Starting REAL-TIME scanner…", flush=True)
    print("⚡ WebSocket: תופס תנועות תוך שניות", flush=True)

    print("⏳ Waiting for initial data...", flush=True)
    time.sleep(3)

    while True:
        try:
            start_websocket()
        except Exception as e:
            print(f"WebSocket crashed: {e}", flush=True)

        print("Reconnecting in 10s...", flush=True)
        time.sleep(10)
