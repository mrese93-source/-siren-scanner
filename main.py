import os
import time
import threading
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

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
    print(f"HTTP server listening on 0.0.0.0:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# =========================
# Env
# =========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# =========================
# Config (UPDATED)
# =========================
SCAN_INTERVAL_SEC = 180            # every 3 minutes
LOOKBACK_15M_SEC = 900            # 15 minutes
ALERT_COOLDOWN_SEC = 6 * 60 * 60  # 6 hours per symbol

# Less strict filters (UPDATED)
MIN_OI = 200_000
MIN_VOL = 100_000

# Volume spike config (delta turnover based)
VOL_DELTA_MIN_USD = 50_000
VOL_SPIKE_STRONG = 2.5
VOL_SPIKE_MED = 1.8

# Price move thresholds (UPDATED)
MOVE_MED = 1.5
MOVE_STRONG = 3.0
MOVE_VERY_STRONG = 5.0

# "Don't short a rocket" threshold
NO_SHORT_IF_15M_UP = 5.0

# Pump detector (adds LONG weight, does NOT block)
PUMP_15M = 4.0
PUMP_VOL_SPIKE = 2.0

history = {}     # symbol -> state
alerted = {}     # symbol -> last alert time
market_history = deque(maxlen=5)
last_market_alert_key = ""

# =========================
# Helpers
# =========================
def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def send_telegram(msg):
    try:
        if not TELEGRAM_TOKEN or not CHAT_ID:
            print("Missing TELEGRAM_TOKEN or CHAT_ID", flush=True)
            return
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print("Telegram error: " + str(e), flush=True)

def get_bybit_tickers():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        return r.json()["result"]["list"]
    except Exception as e:
        print("Bybit error: " + str(e), flush=True)
        return []

def get_state(symbol):
    if symbol not in history:
        history[symbol] = {
            "prices": deque(maxlen=120),           # (ts, price)
            "turnover_delta": deque(maxlen=120),   # (ts, delta_turnover24h)
            "oi": deque(maxlen=120),               # (ts, oi)
            "funding": deque(maxlen=120),          # (ts, funding)
            "prev_turnover24h": None,
        }
    return history[symbol]

def price_change_over_lookback(prices_deque, now_ts, lookback_sec):
    if len(prices_deque) < 2:
        return 0.0

    target = now_ts - lookback_sec
    old_price = None

    for ts, p in prices_deque:
        if ts >= target:
            old_price = p
            break

    if old_price is None:
        old_price = prices_deque[0][1]

    curr = prices_deque[-1][1]
    if old_price <= 0:
        return 0.0
    return ((curr - old_price) / old_price) * 100.0

def calc_avg(arr):
    if not arr:
        return None
    return sum(arr) / len(arr)

def calc_oi_trend(oi_deque):
    if len(oi_deque) < 3:
        return 0.0
    items = list(oi_deque)
    changes = []
    for i in range(1, len(items)):
        prev = items[i-1][1]
        curr = items[i][1]
        if prev > 0:
            changes.append(((curr - prev) / prev) * 100.0)
    return calc_avg(changes) if changes else 0.0

def calc_funding_trend(funding_deque):
    if len(funding_deque) < 3:
        return 0.0
    return funding_deque[-1][1] - funding_deque[0][1]

def analyze_market(tickers):
    gainers = 0
    losers = 0
    for t in tickers:
        ch = safe_float(t.get("price24hPcnt", 0)) * 100.0
        if ch > 2:
            gainers += 1
        elif ch < -2:
            losers += 1

    total = gainers + losers
    if total == 0:
        return "neutral", 50.0

    bull_pct = (gainers / total) * 100.0
    if bull_pct >= 70:
        return "bull", bull_pct
    if bull_pct <= 30:
        return "bear", bull_pct
    return "neutral", bull_pct

def detect_market_move(trend, bull_pct):
    market_history.append({"trend": trend, "bull_pct": bull_pct})
    if len(market_history) < 3:
        return None, None

    recent = list(market_history)[-3:]
    vals = [m["bull_pct"] for m in recent]
    delta = vals[-1] - vals[0]

    if delta >= 20:
        return "up_strong", "⚠️ <b>Market Alert</b>\n🌊 השוק מתחיל לעלות בכוח"
    if delta <= -20:
        return "down_strong", "⚠️ <b>Market Alert</b>\n🌊 השוק מתחיל לרדת בכוח"
    if vals[-1] >= 75 and all(v >= 65 for v in vals):
        return "up_stable", "⚠️ <b>Market Alert</b>\n📈 מגמת עלייה יציבה בשוק"
    if vals[-1] <= 25 and all(v <= 35 for v in vals):
        return "down_stable", "⚠️ <b>Market Alert</b>\n📉 מגמת ירידה יציבה בשוק"
    return None, None

# =========================
# Scan
# =========================
def scan():
    global last_market_alert_key

    print("Scanning...", flush=True)
    tickers = get_bybit_tickers()
    if not tickers:
        print("No data", flush=True)
        return

    now = time.time()
    found = 0

    market_trend, bull_pct = analyze_market(tickers)
    alert_key, alert_msg = detect_market_move(market_trend, bull_pct)

    if alert_key and alert_key != last_market_alert_key:
        trend_emoji = "🟢" if market_trend == "bull" else "🔴" if market_trend == "bear" else "⚪"
        send_telegram(alert_msg + "\n" + trend_emoji + " " + str(round(bull_pct)) + "% מהמטבעות בעלייה")
        last_market_alert_key = alert_key
        print("Market alert sent: " + alert_key, flush=True)

    for t in tickers:
        try:
            symbol = t.get("symbol", "")
            if not symbol:
                continue

            price = safe_float(t.get("lastPrice", 0))
            change_24h = safe_float(t.get("price24hPcnt", 0)) * 100.0
            turnover24h = safe_float(t.get("turnover24h", 0))
            funding = safe_float(t.get("fundingRate", 0)) * 100.0
            oi = safe_float(t.get("openInterestValue", 0))

            if price <= 0:
                continue

            # Less strict filters (UPDATED)
            if oi < MIN_OI:
                continue
            if turnover24h < MIN_VOL:
                continue

            st = get_state(symbol)

            prev_turn = st["prev_turnover24h"]
            if prev_turn is None:
                st["prev_turnover24h"] = turnover24h
                st["prices"].append((now, price))
                st["oi"].append((now, oi))
                st["funding"].append((now, funding))
                continue

            delta_turn = max(0.0, turnover24h - prev_turn)
            st["prev_turnover24h"] = turnover24h

            st["prices"].append((now, price))
            st["turnover_delta"].append((now, delta_turn))
            st["oi"].append((now, oi))
            st["funding"].append((now, funding))

            if symbol in alerted and now - alerted[symbol] < ALERT_COOLDOWN_SEC:
                continue

            if len(st["prices"]) < 6:
                continue

            change_15m = price_change_over_lookback(st["prices"], now, LOOKBACK_15M_SEC)

            deltas = [x[1] for x in list(st["turnover_delta"])[-10:] if x[1] >= VOL_DELTA_MIN_USD]
            vol_avg = calc_avg(deltas[:-1]) if len(deltas) >= 3 else None
            vol_spike = (deltas[-1] / vol_avg) if vol_avg and vol_avg > 0 else 1.0

            oi_trend = calc_oi_trend(st["oi"])
            funding_trend = calc_funding_trend(st["funding"])

            long_signals = []
            short_signals = []

            # ======================
            # LONG (UPDATED)
            # ======================
            if vol_spike >= VOL_SPIKE_STRONG and change_15m >= 0:
                long_signals.append(("Volume spike x" + str(round(vol_spike, 1)) + " 💥", 3))
            elif vol_spike >= VOL_SPIKE_MED and change_15m >= 0:
                long_signals.append(("Volume spike x" + str(round(vol_spike, 1)), 2))

            if change_15m >= MOVE_VERY_STRONG:
                long_signals.append(("+" + str(round(change_15m, 1)) + "% ב-15 דקות 🚀", 3))
            elif change_15m >= MOVE_STRONG:
                long_signals.append(("+" + str(round(change_15m, 1)) + "% ב-15 דקות", 2))
            elif change_15m >= MOVE_MED:
                long_signals.append(("+" + str(round(change_15m, 1)) + "% ב-15 דקות (מתחיל)", 1))

            # PUMP detector (does NOT cancel) - adds strong weight
            if change_15m >= PUMP_15M and vol_spike >= PUMP_VOL_SPIKE:
                long_signals.append(("PUMP detected 🚀", 4))

            if oi_trend >= 2.0 and change_15m >= 0:
                long_signals.append(("OI עולה בעקביות", 2))
            if oi_trend >= 3.0 and abs(change_15m) < 2:
                long_signals.append(("OI עולה בשקט - צבירה 🔍", 3))

            if funding_trend >= 0.05 and funding >= 0.05:
                long_signals.append(("Funding מטפס 💹", 2))
            elif funding >= 0.10:
                long_signals.append(("Funding גבוה 🔥", 2))

            if 10 <= change_24h <= 60:
                long_signals.append(("24h: +" + str(round(change_24h, 1)) + "%", 1))

            if market_trend == "bear" and change_24h >= 10:
                long_signals.append(("עולה נגד שוק יורד ⚡", 3))

            # ======================
            # SHORT (UPDATED)
            # ======================
            if vol_spike >= VOL_SPIKE_STRONG and change_15m <= 0:
                short_signals.append(("Volume spike x" + str(round(vol_spike, 1)) + " 💥", 3))
            elif vol_spike >= VOL_SPIKE_MED and change_15m <= 0:
                short_signals.append(("Volume spike x" + str(round(vol_spike, 1)), 2))

            if change_15m <= -MOVE_VERY_STRONG:
                short_signals.append((str(round(change_15m, 1)) + "% ב-15 דקות 💥", 3))
            elif change_15m <= -MOVE_STRONG:
                short_signals.append((str(round(change_15m, 1)) + "% ב-15 דקות", 2))
            elif change_15m <= -MOVE_MED:
                short_signals.append((str(round(change_15m, 1)) + "% ב-15 דקות (מתחיל)", 1))

            # OI helps SHORT only when price is weak (FIX)
            if oi_trend >= 3.0 and change_15m <= -1.0:
                short_signals.append(("OI עולה + מחיר נחלש (לחץ שורטים) 🔍", 3))
            elif oi_trend >= 2.0 and change_15m <= -0.5:
                short_signals.append(("OI עולה + חולשה במחיר", 2))

            if funding_trend <= -0.05 and funding <= -0.05:
                short_signals.append(("Funding יורד", 2))
            elif funding <= -0.10:
                short_signals.append(("Funding שלילי 🩸", 2))

            if -60 <= change_24h <= -10:
                short_signals.append(("24h: " + str(round(change_24h, 1)) + "%", 1))

            if market_trend == "bull" and change_24h <= -10:
                short_signals.append(("יורד נגד שוק עולה ⚡", 3))

            # Do NOT short a rocket (FIX)
            if change_15m > NO_SHORT_IF_15M_UP:
                short_signals = []

            # ======================
            # Score + qualify
            # ======================
            long_score = sum(w for _, w in long_signals)
            short_score = sum(w for _, w in short_signals)

            long_has_strong = any(w >= 3 for _, w in long_signals)
            short_has_strong = any(w >= 3 for _, w in short_signals)

            long_qualifies = long_score >= 4 or (long_has_strong and long_score >= 3)
            short_qualifies = short_score >= 4 or (short_has_strong and short_score >= 3)

            if not long_qualifies and not short_qualifies:
                continue

            is_long = long_qualifies and long_score >= short_score
            is_short = short_qualifies and short_score > long_score

            if not (is_long or is_short):
                continue

            if is_long:
                direction = "🟢 LONG"
                signals_used = long_signals
                score = long_score
            else:
                direction = "🔴 SHORT"
                signals_used = short_signals
                score = short_score

            if score >= 8:
                strength = "🔥🔥🔥🔥 חזק מאוד"
            elif score >= 6:
                strength = "🔥🔥🔥 חזק"
            elif score >= 4:
                strength = "🔥🔥 בינוני-חזק"
            else:
                strength = "🔥 בינוני"

            signals_txt = "\n".join("- " + s for s, _ in signals_used)

            if market_trend == "bull":
                market_txt = "🌍 שוק: עולה (" + str(round(bull_pct)) + "% ירוק)"
            elif market_trend == "bear":
                market_txt = "🌍 שוק: יורד (" + str(round(100 - bull_pct)) + "% אדום)"
            else:
                market_txt = "🌍 שוק: ניטרלי"

            msg = (
                direction + " <b>" + symbol + "</b>\n"
                + strength + "\n\n"
                + "<b>סיגנלים:</b>\n" + signals_txt + "\n\n"
                + "24h: " + str(round(change_24h, 1)) + "% | 15m: " + str(round(change_15m, 1)) + "%\n"
                + "💵 מחיר: $" + str(price) + "\n"
                + "📦 ΔVolume: $" + str(round(delta_turn / 1_000_000, 3)) + "M | Spike: x" + str(round(vol_spike, 2)) + "\n"
                + "💹 Funding: " + str(round(funding, 4)) + "%\n"
                + market_txt
            )

            send_telegram(msg)
            alerted[symbol] = now
            found += 1
            print("Found: " + symbol + " " + direction + " | Score: " + str(score), flush=True)

        except Exception:
            continue

    print("Scan done - " + str(found) + " signals", flush=True)

# =========================
# Run
# =========================
print("Starting scanner...", flush=True)

while True:
    try:
        scan()
    except Exception as e:
        print("Error: " + str(e), flush=True)
    time.sleep(SCAN_INTERVAL_SEC)
