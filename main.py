# FIXED VERSION — ONLY SYNTAX FIXES

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
CHAT_ID        = os.environ.get("CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# =========================
# Utils (FIXED INDENTATION)
# =========================

def now_ts():
    return time.time()

def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def pct_change(old, new):
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100.0

def http_get_json(url, timeout=10):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        if not r.text or not r.text.strip():
            return None
        return r.json()
    except Exception as e:
        print(f"HTTP error: {e} | {url}", flush=True)
        return None

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CHAT_ID", flush=True)
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"Telegram error: {r.status_code} | {r.text}", flush=True)
    except Exception as e:
        print(f"Telegram send error: {e}", flush=True)

# =========================
# IMPORTANT FIXES INSIDE STRINGS
# =========================

def send_funding_spike_alert(symbol, prev_f, new_f, price):
    delta = new_f - prev_f
    arrow = "⬆️" if delta > 0 else "⬇️"
    msg = (
        "📉 <b>Funding Spike</b>\n"
        f"<b>{symbol}</b>\n\n"
        f"קודם: <b>{round(prev_f,4)}%</b>\n"
        f"עכשיו: <b>{round(new_f,4)}%</b>\n"
        f"שינוי: <b>{arrow} {round(delta,4)}%</b>\n"
        + (f"💵 מחיר: ${price}\n" if price and price > 0 else "")
    )
    send_telegram(msg)

def send_funding_extreme_alert(symbol, funding, price):
    direction = "🟢 Bias LONG" if funding < 0 else "🔴 Bias SHORT"
    state     = "Short pays Long" if funding < 0 else "Long pays Short"
    msg = (
        f"{direction} <b>{symbol}</b>\n"
        "⚠️ <b>Funding חריג</b>\n\n"
        f"📉 Funding: <b>{round(funding,4)}%</b>\n"
        f"🧾 מצב: {state}\n"
        f"💵 מחיר: ${price}\n"
    )
    send_telegram(msg)

# =========================
# MAIN FIX
# =========================

def main():
    print("MOHAMED BOT V3 — CVD + LIQ + WHALE + VP + PSYCH LIVE", flush=True)

    threading.Thread(target=update_metadata, daemon=True).start()
    threading.Thread(target=resubscribe_loop, daemon=True).start()

    while True:
        try:
            start_websocket()
        except Exception as e:
            print(f"WS crashed: {e}", flush=True)
            time.sleep(10)

# =========================
# ENTRY POINT FIXED
# =========================

if __name__ == "__main__":
    main()
