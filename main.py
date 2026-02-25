import requests
import os
import time
import threading
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get(“PORT”, “8080”))

class Handler(BaseHTTPRequestHandler):
def do_GET(self):
self.send_response(200)
self.end_headers()
self.wfile.write(b”OK”)
def log_message(self, format, *args):
return

def run_server():
server = HTTPServer((“0.0.0.0”, PORT), Handler)
print(“HTTP server listening on 0.0.0.0:” + str(PORT), flush=True)
server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

TELEGRAM_TOKEN = os.environ.get(“TELEGRAM_TOKEN”)
CHAT_ID = os.environ.get(“CHAT_ID”)

history = {}
alerted = {}
market_history = deque(maxlen=5)
last_market_alert = “”

def send_telegram(msg):
try:
if not TELEGRAM_TOKEN or not CHAT_ID:
print(“Missing TELEGRAM_TOKEN or CHAT_ID”, flush=True)
return
url = “https://api.telegram.org/bot” + TELEGRAM_TOKEN + “/sendMessage”
requests.post(url, data={“chat_id”: CHAT_ID, “text”: msg, “parse_mode”: “HTML”}, timeout=10)
except Exception as e:
print(“Telegram error: “ + str(e), flush=True)

def get_bybit_tickers():
try:
url = “https://api.bybit.com/v5/market/tickers?category=linear”
r = requests.get(url, timeout=10)
return r.json()[“result”][“list”]
except Exception as e:
print(“Bybit error: “ + str(e), flush=True)
return []

def analyze_market(tickers):
gainers = 0
losers = 0
for t in tickers:
try:
change = float(t[“price24hPcnt”]) * 100
if change > 2:
gainers += 1
elif change < -2:
losers += 1
except Exception:
continue
total = gainers + losers
if total == 0:
return “neutral”, 50
bull_pct = (gainers / total) * 100
if bull_pct >= 70:
trend = “bull”
elif bull_pct <= 30:
trend = “bear”
else:
trend = “neutral”
return trend, bull_pct

def detect_market_move(current_trend, bull_pct):
market_history.append({“trend”: current_trend, “bull_pct”: bull_pct})
if len(market_history) < 3:
return None, None
recent = list(market_history)[-3:]
bull_values = [m[“bull_pct”] for m in recent]
change = bull_values[-1] - bull_values[0]
if change >= 20:
return “up_strong”, “\u26a0\ufe0f <b>Market Alert</b>\n\U0001f30a \u05d4\u05e9\u05d5\u05e7 \u05de\u05ea\u05d7\u05d9\u05dc \u05dc\u05e2\u05dc\u05d5\u05ea \u05d1\u05db\u05d5\u05d7”
elif change <= -20:
return “down_strong”, “\u26a0\ufe0f <b>Market Alert</b>\n\U0001f30a \u05d4\u05e9\u05d5\u05e7 \u05de\u05ea\u05d7\u05d9\u05dc \u05dc\u05e8\u05d3\u05ea \u05d1\u05db\u05d5\u05d7”
elif bull_values[-1] >= 75 and all(v >= 65 for v in bull_values):
return “up_stable”, “\u26a0\ufe0f <b>Market Alert</b>\n\U0001f4c8 \u05de\u05d2\u05de\u05ea \u05e2\u05dc\u05d9\u05d9\u05d4 \u05d9\u05e6\u05d9\u05d1\u05d4 \u05d1\u05e9\u05d5\u05e7”
elif bull_values[-1] <= 25 and all(v <= 35 for v in bull_values):
return “down_stable”, “\u26a0\ufe0f <b>Market Alert</b>\n\U0001f4c9 \u05de\u05d2\u05de\u05ea \u05d9\u05e8\u05d9\u05d3\u05d4 \u05d9\u05e6\u05d9\u05d1\u05d4 \u05d1\u05e9\u05d5\u05e7”
return None, None

def get_history(symbol):
if symbol not in history:
history[symbol] = deque(maxlen=10)
return history[symbol]

def calc_volume_avg(hist_list):
if len(hist_list) < 3:
return None
return sum(h[“volume”] for h in hist_list) / len(hist_list)

def calc_oi_trend(hist):
if len(hist) < 3:
return 0
changes = []
items = list(hist)
for i in range(1, len(items)):
prev = items[i-1][“oi”]
curr = items[i][“oi”]
if prev > 0:
changes.append(((curr - prev) / prev) * 100)
if not changes:
return 0
return sum(changes) / len(changes)

def calc_funding_trend(hist):
if len(hist) < 3:
return 0
items = list(hist)
return items[-1][“funding”] - items[0][“funding”]

def scan():
global last_market_alert
print(“Scanning…”, flush=True)
tickers = get_bybit_tickers()
if not tickers:
print(“No data”, flush=True)
return

```
now = time.time()
found = 0

market_trend, bull_pct = analyze_market(tickers)
alert_key, alert_msg = detect_market_move(market_trend, bull_pct)

if alert_key and alert_key != last_market_alert:
    trend_emoji = "\U0001f7e2" if market_trend == "bull" else "\U0001f534" if market_trend == "bear" else "\u26aa"
    market_msg = alert_msg + "\n" + trend_emoji + " " + str(round(bull_pct)) + "% \u05de\u05d4\u05de\u05d8\u05d1\u05e2\u05d5\u05ea \u05d1\u05e2\u05dc\u05d9\u05d9\u05d4"
    send_telegram(market_msg)
    last_market_alert = alert_key
    print("Market alert sent: " + alert_key, flush=True)

for t in tickers:
    try:
        symbol = t["symbol"]
        price = float(t["lastPrice"])
        change_24h = float(t["price24hPcnt"]) * 100
        volume = float(t["turnover24h"])
        funding = float(t.get("fundingRate", 0)) * 100
        oi = float(t.get("openInterestValue", 0))

        if oi < 1000000 or oi > 300000000:
            continue
        if volume < 500000 or volume > 150000000:
            continue
        if price <= 0:
            continue

        hist = get_history(symbol)
        hist.append({"price": price, "oi": oi, "volume": volume, "funding": funding, "time": now})

        if symbol in alerted and now - alerted[symbol] < 21600:
            continue

        if len(hist) < 3:
            continue

        items = list(hist)
        prev_price = items[-2]["price"]
        change_15m = ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0

        vol_avg = calc_volume_avg(list(hist)[:-1])
        vol_spike = (volume / vol_avg) if vol_avg and vol_avg > 0 else 1

        oi_trend = calc_oi_trend(hist)
        funding_trend = calc_funding_trend(hist)

        long_signals = []
        short_signals = []

        # ---- LONG ----
        if vol_spike >= 5 and change_24h >= 0:
            long_signals.append(("Volume x" + str(round(vol_spike, 1)) + " \u05de\u05d4\u05e8\u05d2\u05d9\u05dc \U0001f4a5", 3))
        elif vol_spike >= 3 and change_24h >= 0:
            long_signals.append(("Volume x" + str(round(vol_spike, 1)) + " \u05de\u05d4\u05e8\u05d2\u05d9\u05dc", 2))

        if change_15m >= 5:
            long_signals.append(("+" + str(round(change_15m, 1)) + "% \u05d1-15 \u05d3\u05e7\u05d5\u05ea \U0001f680", 3))
        elif change_15m >= 3:
            long_signals.append(("+" + str(round(change_15m, 1)) + "% \u05d1-15 \u05d3\u05e7\u05d5\u05ea", 2))

        if oi_trend >= 3 and abs(change_15m) < 2:
            long_signals.append(("OI \u05e2\u05d5\u05dc\u05d4 \u05d1\u05e9\u05e7\u05d8 - \u05e6\u05d1\u05d9\u05e8\u05d4 \u05dc\u05e4\u05e0\u05d9 \u05de\u05d4\u05dc\u05da \U0001f50d", 3))
        elif oi_trend >= 2:
            long_signals.append(("OI \u05e2\u05d5\u05dc\u05d4 \u05d1\u05e2\u05e7\u05d1\u05d9\u05d5\u05ea", 2))

        if funding_trend >= 0.05 and funding >= 0.05:
            long_signals.append(("Funding \u05de\u05d8\u05e4\u05e1 - \u05dc\u05d7\u05e5 \u05dc\u05d5\u05e0\u05d2\u05d9\u05dd \U0001f4b9", 2))
        elif funding >= 0.1:
            long_signals.append(("Funding \u05d2\u05d1\u05d5\u05d4 \U0001f525", 2))

        if 10 <= change_24h <= 60:
            long_signals.append(("24h: +" + str(round(change_24h, 1)) + "%", 1))

        if market_trend == "bear" and change_24h >= 10:
            long_signals.append(("\u05e2\u05d5\u05dc\u05d4 \u05e0\u05d2\u05d3 \u05e9\u05d5\u05e7 \u05d9\u05d5\u05e8\u05d3 \u26a1", 3))

        # ---- SHORT - בדיוק אותם תנאים הפוך ----
        if vol_spike >= 5 and change_24h <= 0:
            short_signals.append(("Volume x" + str(round(vol_spike, 1)) + " \u05de\u05d4\u05e8\u05d2\u05d9\u05dc \U0001f4a5", 3))
        elif vol_spike >= 3 and change_24h <= 0:
            short_signals.append(("Volume x" + str(round(vol_spike, 1)) + " \u05de\u05d4\u05e8\u05d2\u05d9\u05dc", 2))

        if change_15m <= -5:
            short_signals.append((str(round(change_15m, 1)) + "% \u05d1-15 \u05d3\u05e7\u05d5\u05ea \U0001f4a5", 3))
        elif change_15m <= -3:
            short_signals.append((str(round(change_15m, 1)) + "% \u05d1-15 \u05d3\u05e7\u05d5\u05ea", 2))

        if oi_trend >= 3 and abs(change_15m) < 2:
            short_signals.append(("OI \u05e2\u05d5\u05dc\u05d4 \u05d1\u05e9\u05e7\u05d8 - \u05dc\u05d7\u05e5 \u05e9\u05d5\u05e8\u05d8\u05d9\u05dd \U0001f50d", 3))
        elif oi_trend >= 2:
            short_signals.append(("OI \u05e2\u05d5\u05dc\u05d4 \u05d1\u05e2\u05e7\u05d1\u05d9\u05d5\u05ea", 2))

        if funding_trend <= -0.05 and funding <= -0.05:
            short_signals.append(("Funding \u05d9\u05d5\u05e8\u05d3 - \u05dc\u05d7\u05e5 \u05e9\u05d5\u05e8\u05d8\u05d9\u05dd \U0001f4b9", 2))
        elif funding <= -0.1:
            short_signals.append(("Funding \u05e9\u05dc\u05d9\u05dc\u05d9 \U0001fa78", 2))

        if -60 <= change_24h <= -10:
            short_signals.append(("24h: " + str(round(change_24h, 1)) + "%", 1))

        if market_trend == "bull" and change_24h <= -10:
            short_signals.append(("\u05d9\u05d5\u05e8\u05d3 \u05e0\u05d2\u05d3 \u05e9\u05d5\u05e7 \u05e2\u05d5\u05dc\u05d4 \u26a1", 3))

        long_score = sum(w for _, w in long_signals)
        short_score = sum(w for _, w in short_signals)

        long_has_strong = any(w >= 3 for _, w in long_signals)
        short_has_strong = any(w >= 3 for _, w in short_signals)

        long_qualifies = long_score >= 4 or (long_has_strong and long_score >= 3)
        short_qualifies = short_score >= 4 or (short_has_strong and short_score >= 3)

        if not long_qualifies and not short_qualifies:
            continue

        is_long = long_score >= short_score and long_qualifies
        is_short = short_score > long_score and short_qualifies

        if not (is_long or is_short):
            continue

        if is_long:
            direction = "\U0001f7e2 LONG"
            signals_used = long_signals
            score = long_score
        else:
            direction = "\U0001f534 SHORT"
            signals_used = short_signals
            score = short_score

        if score >= 8:
            strength = "\U0001f525\U0001f525\U0001f525\U0001f525 \u05d7\u05d6\u05e7 \u05de\u05d0\u05d5\u05d3"
        elif score >= 6:
            strength = "\U0001f525\U0001f525\U0001f525 \u05d7\u05d6\u05e7"
        elif score >= 4:
            strength = "\U0001f525\U0001f525 \u05d1\u05d9\u05e0\u05d5\u05e0\u05d9-\u05d7\u05d6\u05e7"
        else:
            strength = "\U0001f525 \u05d1\u05d9\u05e0\u05d5\u05e0\u05d9"

        signals_txt = "\n".join("- " + s for s, _ in signals_used)

        if market_trend == "bull":
            market_txt = "\U0001f30d \u05e9\u05d5\u05e7: \u05e2\u05d5\u05dc\u05d4 (" + str(round(bull_pct)) + "% \u05d9\u05e8\u05d5\u05e7)"
        elif market_trend == "bear":
            market_txt = "\U0001f30d \u05e9\u05d5\u05e7: \u05d9\u05d5\u05e8\u05d3 (" + str(round(100 - bull_pct)) + "% \u05d0\u05d3\u05d5\u05dd)"
        else:
            market_txt = "\U0001f30d \u05e9\u05d5\u05e7: \u05e0\u05d9\u05d8\u05e8\u05dc\u05d9"

        msg = (
            direction + " <b>" + symbol + "</b>\n"
            + strength + "\n\n"
            + "<b>\u05e1\u05d9\u05d2\u05e0\u05dc\u05d9\u05dd:</b>\n" + signals_txt + "\n\n"
            + "24h: " + str(round(change_24h, 1)) + "% | 15m: " + str(round(change_15m, 1)) + "%\n"
            + "\U0001f4b5 \u05de\u05d7\u05d9\u05e8: $" + str(price) + "\n"
            + "\U0001f4e6 Volume: $" + str(round(volume / 1000000, 1)) + "M (x" + str(round(vol_spike, 1)) + ")\n"
            + "\U0001f4b9 Funding: " + str(round(funding, 4)) + "%\n"
            + market_txt
        )

        send_telegram(msg)
        alerted[symbol] = now
        found += 1
        print("Found: " + symbol + " " + direction + " | Score: " + str(score), flush=True)

    except Exception:
        continue

print("Scan done - " + str(found) + " signals", flush=True)
```

print(“Starting scanner…”, flush=True)
while True:
try:
scan()
except Exception as e:
print(“Error: “ + str(e), flush=True)
time.sleep(180)
