import requests
import os
import time
from collections import deque
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- Tiny web server so Railway keeps the container alive (port 8080) ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_server():
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()
# ----------------------------------------------------------------------


TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

history = {}
alerted = {}
market_history = deque(maxlen=5)


def send_telegram(msg: str):
    try:
        if not TELEGRAM_TOKEN or not CHAT_ID:
            print("Missing TELEGRAM_TOKEN or CHAT_ID (set them in Railway Variables).")
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print("Telegram error:", e)


def get_bybit_tickers():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        return r.json()["result"]["list"]
    except Exception as e:
        print("Bybit error:", e)
        return []


def analyze_market(tickers):
    gainers = 0
    losers = 0

    for t in tickers:
        try:
            change = float(t["price24hPcnt"]) * 100
            if change > 2:
                gainers += 1
            elif change < -2:
                losers += 1
        except Exception:
            continue

    total = gainers + losers
    if total == 0:
        return "neutral", 50

    bull_pct = (gainers / total) * 100
    if bull_pct >= 70:
        trend = "bull"
    elif bull_pct <= 30:
        trend = "bear"
    else:
        trend = "neutral"

    return trend, bull_pct


def detect_market_move(current_trend, bull_pct):
    market_history.append({"trend": current_trend, "bull_pct": bull_pct})

    if len(market_history) < 3:
        return None

    recent = list(market_history)[-3:]
    bull_values = [m["bull_pct"] for m in recent]
    change = bull_values[-1] - bull_values[0]

    if change >= 20:
        return "⚠️ <b>Market Alert</b>\n🌊 השוק מתחיל לעלות בכוח"
    if change <= -20:
        return "⚠️ <b>Market Alert</b>\n🌊 השוק מתחיל לרדת בכוח"
    if bull_values[-1] >= 75 and all(v >= 65 for v in bull_values):
        return "⚠️ <b>Market Alert</b>\n📈 מגמת עלייה יציבה בשוק"
    if bull_values[-1] <= 25 and all(v <= 35 for v in bull_values):
        return "⚠️ <b>Market Alert</b>\n📉 מגמת ירידה יציבה בשוק"

    return None


def scan():
    print("Scanning...")

    tickers = get_bybit_tickers()
    if not tickers:
        print("No data")
        return

    market_trend, bull_pct = analyze_market(tickers)
    market_alert = detect_market_move(market_trend, bull_pct)

    if market_alert:
        trend_emoji = "🟢" if market_trend == "bull" else "🔴" if market_trend == "bear" else "⚪"
        market_msg = market_alert + "\n" + trend_emoji + " " + str(round(bull_pct)) + "% מהמטבעות בעלייה"
        send_telegram(market_msg)
        print("Market alert sent")


print("Starting scanner...")

while True:
    try:
        scan()
    except Exception as e:
        print("Error:", e)

    time.sleep(180)
