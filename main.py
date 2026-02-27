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
    print("HTTP server on port " + str(PORT), flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# =========================
# Config
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Base cooldown (fallback)
ALERT_COOLDOWN_SEC = 4 * 60 * 60

# Funding cooldown (separate)
FUNDING_ALERT_COOLDOWN_SEC = 60 * 60

MOVE_EXPLOSIVE = 10.0
MOVE_1MIN_STRONG = 1.5
MOVE_3MIN_STRONG = 3.0
MOVE_5MIN_STRONG = 4.0

MIN_OI = 100_000
MAX_OI = 500_000_000

# Funding thresholds (in % because we multiply by 100 below)
FUNDING_EXTREME_NEGATIVE = -0.5
FUNDING_EXTREME_POSITIVE = 0.5

TOP_SYMBOLS = 150
SYMBOLS_REFRESH_SEC = 30 * 60  # refresh every 30 minutes

# =========================
# State
# =========================

price_history = defaultdict(lambda: deque(maxlen=600))

# ✅ cooldown per symbol + direction (LONG/SHORT)
alerted = {}  # key = "SYMBOL|LONG" or "SYMBOL|SHORT" -> last_ts

# ✅ funding-only cooldown
funding_alerted = {}  # key = "SYMBOL" -> last_ts

symbol_metadata = {}

tracked_symbols = []
ws_instance = None

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
            print("Missing TELEGRAM_TOKEN or CHAT_ID", flush=True)
            return
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code != 200:
            print("Telegram error: " + str(r.status_code) + " " + r.text, flush=True)
    except Exception as e:
        print("Telegram error: " + str(e), flush=True)

def calc_change(prices, lookback_sec):
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
# Metadata updater
# =========================

def update_metadata():
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
                    symbol_metadata[symbol] = {
                        "oi": safe_float(t.get("openInterestValue", 0)),
                        "turnover24h": safe_float(t.get("turnover24h", 0)),
                        "funding": safe_float(t.get("fundingRate", 0)) * 100.0,
                        "volume24h": safe_float(t.get("volume24h", 0)),
                        "last_update": time.time()
                    }

            print("Updated metadata for " + str(len(tickers)) + " symbols", flush=True)
        except Exception as e:
            print("Metadata error: " + str(e), flush=True)

        time.sleep(30)

threading.Thread(target=update_metadata, daemon=True).start()

# =========================
# Get top symbols
# =========================

def get_top_symbols():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        data = r.json()
        tickers = (data.get("result") or {}).get("list", []) or []

        valid = []
        for t in tickers:
            symbol = t.get("symbol", "")
            oi = safe_float(t.get("openInterestValue", 0))
            turnover = safe_float(t.get("turnover24h", 0))
            if symbol and (MIN_OI <= oi <= MAX_OI):
                valid.append((symbol, turnover))

        valid.sort(key=lambda x: x[1], reverse=True)
        top = [s for s, _ in valid[:TOP_SYMBOLS]]
        print("Tracking " + str(len(top)) + " symbols", flush=True)
        return top
    except Exception as e:
        print("Error getting symbols: " + str(e), flush=True)
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# =========================
# Subscribe / Refresh symbols
# =========================

def subscribe_symbols(ws, symbols):
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        topics = ["tickers." + s for s in batch]
        ws.send(json.dumps({"op": "subscribe", "args": topics}))
        print("Subscribed batch " + str(i // batch_size + 1) + ": " + str(len(batch)) + " symbols", flush=True)
        time.sleep(0.6)

def refresh_symbols_loop():
    global tracked_symbols, ws_instance
    time.sleep(SYMBOLS_REFRESH_SEC)  # wait before first refresh
    while True:
        try:
            print("Refreshing symbol list…", flush=True)
            new_symbols = get_top_symbols()

            with lock:
                old_set = set(tracked_symbols)
                new_set = set(new_symbols)
                added = list(new_set - old_set)
                tracked_symbols = new_symbols

            if added and ws_instance:
                print("New symbols to track: " + str(len(added)) + " -> " + str(added[:5]), flush=True)
                subscribe_symbols(ws_instance, added)

            print("Symbol list refreshed. Total: " + str(len(new_symbols)), flush=True)

        except Exception as e:
            print("Symbol refresh error: " + str(e), flush=True)

        time.sleep(SYMBOLS_REFRESH_SEC)

threading.Thread(target=refresh_symbols_loop, daemon=True).start()

# =========================
# Funding alert
# =========================

def maybe_send_funding_alert(symbol, funding, price):
    if not (funding >= FUNDING_EXTREME_POSITIVE or funding <= FUNDING_EXTREME_NEGATIVE):
        return

    now = time.time()
    last = funding_alerted.get(symbol)
    if last is not None and (now - last) < FUNDING_ALERT_COOLDOWN_SEC:
        return

    direction = "🟢 Bias LONG" if funding < 0 else "🔴 Bias SHORT"
    state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        direction + " <b>" + symbol + "</b>\n"
        + "⚠️ <b>Funding חריג</b>\n\n"
        + "💹 Funding: <b>" + str(round(funding, 4)) + "%</b> (" + state + ")\n"
        + "💵 מחיר: $" + str(price) + "\n"
    )

    send_telegram(msg)
    funding_alerted[symbol] = now
    print("FUNDING ALERT: " + symbol + " funding=" + str(funding) + "%", flush=True)

# =========================
# ✅ Smart cooldown (both requested)
# 1) per direction (LONG/SHORT)
# 2) shorter cooldown when score is higher
# =========================

def cooldown_for_score(score: int) -> int:
    # You asked to keep both behaviors; these values are sensible defaults.
    # Stronger score => allow more frequent alerts.
    if score >= 8:
        return 30 * 60   # 30 minutes
    if score >= 6:
        return 60 * 60   # 1 hour
    if score >= 4:
        return 2 * 60 * 60  # 2 hours
    return ALERT_COOLDOWN_SEC  # fallback (4 hours)

# =========================
# Signal checker
# =========================

def check_signal(symbol, price):
    now = time.time()

    with lock:
        prices = price_history[symbol]
        if len(prices) < 30:
            return
        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)
        funding = meta.get("funding", 0)

    if oi < MIN_OI or oi > MAX_OI:
        return

    change_1m = calc_change(prices, 60)
    change_3m = calc_change(prices, 180)
    change_5m = calc_change(prices, 300)

    signals = []
    score = 0

    # LONG
    if change_1m >= MOVE_EXPLOSIVE:
        signals.append("💣💣💣 +" + str(round(change_1m, 1)) + "% תוך דקה - PUMP!")
        score += 7
    elif change_1m >= MOVE_1MIN_STRONG:
        signals.append("🚀 +" + str(round(change_1m, 1)) + "% תוך דקה!")
        score += 4

    if change_3m >= MOVE_3MIN_STRONG:
        signals.append("📈 +" + str(round(change_3m, 1)) + "% ב-3 דקות")
        score += 3

    if change_5m >= MOVE_5MIN_STRONG:
        signals.append("⚡ +" + str(round(change_5m, 1)) + "% ב-5 דקות")
        score += 2

    # SHORT
    if change_1m <= -MOVE_EXPLOSIVE:
        signals.append("💣💣💣 " + str(round(change_1m, 1)) + "% תוך דקה - DUMP!")
        score += 7
    elif change_1m <= -MOVE_1MIN_STRONG:
        signals.append("💥 " + str(round(change_1m, 1)) + "% תוך דקה!")
        score += 4

    if change_3m <= -MOVE_3MIN_STRONG:
        signals.append("📉 " + str(round(change_3m, 1)) + "% ב-3 דקות")
        score += 3

    if change_5m <= -MOVE_5MIN_STRONG:
        signals.append("⚡ " + str(round(change_5m, 1)) + "% ב-5 דקות")
        score += 2

    # Momentum
    if abs(change_1m) > abs(change_3m) * 0.7 and abs(change_1m) >= 2.0:
        signals.append("⚡ התנעה מואצת")
        score += 2

    # Funding squeeze
    if funding <= FUNDING_EXTREME_NEGATIVE and change_1m > 0:
        signals.append("💣 Funding קיצוני: " + str(round(funding, 2)) + "% - SHORT SQUEEZE!")
        score += 5
    elif funding >= FUNDING_EXTREME_POSITIVE and change_1m < 0:
        signals.append("💣 Funding קיצוני: +" + str(round(funding, 2)) + "% - LONG SQUEEZE!")
        score += 5

    if score < 3:
        return

    is_long = change_1m > 0 and change_3m > 0
    is_short = change_1m < 0 and change_3m < 0
    if not (is_long or is_short):
        return

    direction = "LONG" if is_long else "SHORT"  # used for cooldown key
    emoji_dir = "🟢 LONG" if is_long else "🔴 SHORT"

    # ✅ per-direction + dynamic cooldown
    key = symbol + "|" + direction
    cooldown = cooldown_for_score(score)
    last = alerted.get(key)
    if last is not None and (now - last) < cooldown:
        return

    if score >= 8:
        strength = "🔥🔥🔥🔥 קריטי!"
    elif score >= 6:
        strength = "🔥🔥🔥 חזק מאוד"
    else:
        strength = "🔥🔥 חזק"

    signals_txt = "\n".join("- " + s for s in signals)

    msg = (
        emoji_dir + " <b>" + symbol + "</b>\n"
        + strength + "\n\n"
        + "<b>סיגנלים:</b>\n" + signals_txt + "\n\n"
        + "⏱️ 1m: " + str(round(change_1m, 1)) + "% | 3m: " + str(round(change_3m, 1)) + "% | 5m: " + str(round(change_5m, 1)) + "%\n"
        + "💵 מחיר: $" + str(price) + "\n"
        + "💹 Funding: " + str(round(funding, 4)) + "%\n"
        + "📊 OI: $" + str(round(oi / 1_000_000, 1)) + "M\n"
        + "🧊 Cooldown: " + str(int(cooldown / 60)) + "m (per-direction)"
    )

    send_telegram(msg)
    alerted[key] = now
    print("ALERT: " + symbol + " " + emoji_dir + " | 1m=" + str(round(change_1m, 1)) + "% | score=" + str(score), flush=True)

# =========================
# WebSocket
# =========================

def on_message(ws, message):
    try:
        data = json.loads(message)

        # subscribe ack/error
        if "success" in data:
            if not data.get("success", False):
                print("WS ACK error: " + str(data), flush=True)
            return

        topic = data.get("topic", "")
        if not topic.startswith("tickers."):
            return

        ticker_data = data.get("data", {}) or {}
        symbol = ticker_data.get("symbol", "")
        last_price = safe_float(ticker_data.get("lastPrice", 0))

        if not symbol or last_price <= 0:
            return

        now = time.time()
        with lock:
            price_history[symbol].append((now, last_price))
            funding = (symbol_metadata.get(symbol, {}) or {}).get("funding", 0.0)

        # Funding alert (independent)
        maybe_send_funding_alert(symbol, funding, last_price)

        # Signal alert
        check_signal(symbol, last_price)

    except Exception as e:
        print("Message error: " + str(e), flush=True)

def on_error(ws, error):
    print("WebSocket error: " + str(error), flush=True)

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed (" + str(close_status_code) + ") " + str(close_msg), flush=True)

def on_open(ws):
    global ws_instance, tracked_symbols
    ws_instance = ws
    print("WebSocket connected!", flush=True)

    symbols = get_top_symbols()
    with lock:
        tracked_symbols = symbols

    subscribe_symbols(ws, symbols)
    print("Subscribed: " + str(len(symbols)) + " symbols", flush=True)

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
    print("Starting REAL-TIME scanner…", flush=True)
    time.sleep(3)

    while True:
        try:
            start_websocket()
        except Exception as e:
            print("WebSocket crashed: " + str(e), flush=True)

        print("Reconnecting in 10s…", flush=True)
        time.sleep(10)
