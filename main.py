import os
import time
import threading
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler

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
# Imports (requests required)
# =========================
import requests  # keep in requirements.txt: requests

# =========================
# Config
# =========================
SCAN_INTERVAL_SEC = 180           # run every 3 minutes
LOOKBACK_15M_SEC = 900           # 15 minutes
ALERT_COOLDOWN_SEC = 6 * 60 * 60 # per-symbol cooldown: 6 hours

# Filters (OI/volume sanity)
MIN_OI = 1_000_000
MAX_OI = 300_000_000

# Pump/Dump handling (DO NOT block signals — require confirmations / apply penalty)
PUMP_24H_PCT = 20.0
DUMP_24H_PCT = -20.0
PUMP_CONFIRM_MIN = 2             # confirmations required when pump/dump
PUMP_PENALTY = 2                 # score penalty when 24h pump/dump is extreme

# Volume spike based on delta turnover (not 24h absolute)
VOL_DELTA_MIN_USD = 200_000      # ignore tiny delta noise
VOL_SPIKE_STRONG = 5.0
VOL_SPIKE_MED = 3.0

# Movement thresholds
MOVE_STRONG = 5.0
MOVE_MED = 3.0

# Funding thresholds
FUNDING_HIGH = 0.10
FUNDING_TREND = 0.05

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# =========================
# State
# =========================
symbol_state = {}     # symbol -> dict of deques + prev values
alerted = {}          # symbol -> last alert timestamp (cooldown)
market_history = deque(maxlen=5)
last_market_alert_key = ""

def get_state(symbol):
    if symbol not in symbol_state:
        symbol_state[symbol] = {
            "prices": deque(maxlen=120),      # (ts, price)
            "turnover_delta": deque(maxlen=120),  # (ts, delta_turnover)
            "oi": deque(maxlen=120),          # (ts, oi_value)
            "funding": deque(maxlen=120),     # (ts, funding_pct)
            "prev_turnover24h": None,
        }
    return symbol_state[symbol]

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
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print("Telegram error:", str(e), flush=True)

def get_bybit_tickers():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        return r.json()["result"]["list"]
    except Exception as e:
        print("Bybit error:", str(e), flush=True)
        return []

def price_change_over_lookback(prices_deque, now_ts, lookback_sec):
    """
    Return % change from price closest to (now-lookback_sec) to current.
    """
    if len(prices_deque) < 2:
        return 0.0

    target = now_ts - lookback_sec
    old_price = None

    # find earliest price >= target (closest after target)
    for ts, p in prices_deque:
        if ts >= target:
            old_price = p
            break

    if old_price is None:
        # if we don't have enough history, use oldest
        old_price = prices_deque[0][1]

    curr_price = prices_deque[-1][1]
    if old_price <= 0:
        return 0.0
    return ((curr_price - old_price) / old_price) * 100.0

def calc_avg(values):
    if not values:
        return None
    return sum(values) / len(values)

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

# =========================
# Market breadth
# =========================
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
# Core scan
# =========================
def scan():
    global last_market_alert_key

    print("Scanning...", flush=True)
    tickers = get_bybit_tickers()
    if not tickers:
        print("No data", flush=True)
        return

    now = time.time()

    market_trend, bull_pct = analyze_market(tickers)
    alert_key, alert_msg = detect_market_move(market_trend, bull_pct)

    if alert_key and alert_key != last_market_alert_key:
        trend_emoji = "🟢" if market_trend == "bull" else "🔴" if market_trend == "bear" else "⚪"
        market_msg = alert_msg + "\n" + trend_emoji + " " + str(round(bull_pct)) + "% מהמטבעות בעלייה"
        send_telegram(market_msg)
        last_market_alert_key = alert_key
        print("Market alert sent:", alert_key, flush=True)

    found = 0

    for t in tickers:
        try:
            symbol = t.get("symbol", "")
            if not symbol:
                continue

            price = safe_float(t.get("lastPrice", 0))
            change_24h = safe_float(t.get("price24hPcnt", 0)) * 100.0
            turnover24h = safe_float(t.get("turnover24h", 0))  # cumulative 24h
            funding = safe_float(t.get("fundingRate", 0)) * 100.0
            oi = safe_float(t.get("openInterestValue", 0))

            if price <= 0:
                continue
            if oi < MIN_OI or oi > MAX_OI:
                continue

            st = get_state(symbol)

            # turnover delta (real-time proxy volume)
            prev_turn = st["prev_turnover24h"]
            if prev_turn is None:
                st["prev_turnover24h"] = turnover24h
                # collect history but don't signal on first point
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

            # cooldown per symbol
            if symbol in alerted and now - alerted[symbol] < ALERT_COOLDOWN_SEC:
                continue

            # need some history
            if len(st["prices"]) < 6:
                continue

            # real 15m change
            change_15m = price_change_over_lookback(st["prices"], now, LOOKBACK_15M_SEC)

            # volume spike on turnover delta
            deltas = [x[1] for x in list(st["turnover_delta"])[-10:] if x[1] >= VOL_DELTA_MIN_USD]
            vol_avg = calc_avg(deltas[:-1]) if len(deltas) >= 3 else None
            vol_spike = (deltas[-1] / vol_avg) if vol_avg and vol_avg > 0 else 1.0

            oi_trend = calc_oi_trend(st["oi"])
            funding_trend = calc_funding_trend(st["funding"])

            long_signals = []
            short_signals = []

            # ======================
            # LONG signals
            # ======================
            if vol_spike >= VOL_SPIKE_STRONG and change_15m >= 0:
                long_signals.append((f"Volume spike x{round(vol_spike, 1)} 💥", 3))
            elif vol_spike >= VOL_SPIKE_MED and change_15m >= 0:
                long_signals.append((f"Volume spike x{round(vol_spike, 1)}", 2))

            if change_15m >= MOVE_STRONG:
                long_signals.append((f"+{round(change_15m, 1)}% ב-15 דקות 🚀", 3))
            elif change_15m >= MOVE_MED:
                long_signals.append((f"+{round(change_15m, 1)}% ב-15 דקות", 2))

            if oi_trend >= 2.0 and change_15m >= 0:
                long_signals.append(("OI עולה בעקביות", 2))
            if oi_trend >= 3.0 and abs(change_15m) < 2:
                long_signals.append(("OI עולה בשקט - צבירה 🔍", 3))

            if funding_trend >= FUNDING_TREND and funding >= FUNDING_TREND:
                long_signals.append(("Funding מטפס 💹", 2))
            elif funding >= FUNDING_HIGH:
                long_signals.append(("Funding גבוה 🔥", 2))

            if 10 <= change_24h <= 60:
                long_signals.append((f"24h: +{round(change_24h, 1)}%", 1))

            if market_trend == "bear" and change_24h >= 10:
                long_signals.append(("עולה נגד שוק יורד ⚡", 3))

            # ======================
            # SHORT signals (FIXED)
            #   - OI only helps SHORT if price is weak (change_15m negative)
            #   - blocks "short on breakout" (strong positive 15m)
            # ======================
            if vol_spike >= VOL_SPIKE_STRONG and change_15m <= 0:
                short_signals.append((f"Volume spike x{round(vol_spike, 1)} 💥", 3))
            elif vol_spike >= VOL_SPIKE_MED and change_15m <= 0:
                short_signals.append((f"Volume spike x{round(vol_spike, 1)}", 2))

            if change_15m <= -MOVE_STRONG:
                short_signals.append((f"{round(change_15m, 1)}% ב-15 דקות 💥", 3))
            elif change_15m <= -MOVE_MED:
                short_signals.append((f"{round(change_15m, 1)}% ב-15 דקות", 2))

            # OI trend supports SHORT only with weakness
            if oi_trend >= 3.0 and change_15m <= -1.0:
                short_signals.append(("OI עולה + מחיר נחלש (לחץ שורטים) 🔍", 3))
            elif oi_trend >= 2.0 and change_15m <= -0.5:
                short_signals.append(("OI עולה + חולשה במחיר", 2))

            if funding_trend <= -FUNDING_TREND and funding <= -FUNDING_TREND:
                short_signals.append(("Funding יורד", 2))
            elif funding <= -FUNDING_HIGH:
                short_signals.append(("Funding שלילי 🩸", 2))

            if -60 <= change_24h <= -10:
                short_signals.append((f"24h: {round(change_24h, 1)}%", 1))

            if market_trend == "bull" and change_24h <= -10:
                short_signals.append(("יורד נגד שוק עולה ⚡", 3))

            # block short on strong bullish momentum (avoid shorting breakouts)
            if change_15m >= 2.0:
                short_signals = [s for s in short_signals if s[1] <= 1]  # keep only weak notes

            # ======================
            # Score
            # ======================
            long_score = sum(w for _, w in long_signals)
            short_score = sum(w for _, w in short_signals)

            # Pump/Dump handling WITHOUT blocking signals:
            # - require confirmations OR apply penalty
            is_pump = change_24h >= PUMP_24H_PCT
            is_dump = change_24h <= DUMP_24H_PCT

            # confirmations for pump continuation
            if is_pump:
                conf = 0
                if change_15m >= 1.2:
                    conf += 1
                if vol_spike >= 2.0:
                    conf += 1
                if oi_trend >= 1.5:
                    conf += 1
                if market_trend == "bull":
                    conf += 1

                if conf < PUMP_CONFIRM_MIN:
                    # not blocked forever: just skip this cycle
                    continue
                long_score = max(0, long_score - PUMP_PENALTY)

            if is_dump:
                conf = 0
                if change_15m <= -1.2:
                    conf += 1
                if vol_spike >= 2.0:
                    conf += 1
                if oi_trend >= 1.5:
                    conf += 1
                if market_trend == "bear":
                    conf += 1

                if conf < PUMP_CONFIRM_MIN:
                    continue
                short_score = max(0, short_score - PUMP_PENALTY)

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

            # Build message
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

            trend_txt = "🌍 שוק: ניטרלי"
            if market_trend == "bull":
                trend_txt = f"🌍 שוק: עולה ({round(bull_pct)}% ירוק)"
            elif market_trend == "bear":
                trend_txt = f"🌍 שוק: יורד ({round(100 - bull_pct)}% אדום)"

            signals_txt = "\n".join("- " + s for s, _ in signals_used)

            msg = (
                f"{direction} <b>{symbol}</b>\n"
                f"{strength}\n\n"
                f"<b>סיגנלים:</b>\n{signals_txt}\n\n"
                f"24h: {round(change_24h, 1)}% | 15m: {round(change_15m, 1)}%\n"
                f"💵 מחיר: ${price}\n"
                f"📦 ΔVolume(24h): ${round(delta_turn/1_000_000, 3)}M | Spike: x{round(vol_spike, 1)}\n"
                f"💹 Funding: {round(funding, 4)}%\n"
                f"{trend_txt}"
            )

            send_telegram(msg)
            alerted[symbol] = now
            found += 1
            print(f"Alert: {symbol} {direction} score={score}", flush=True)

        except Exception as e:
            # don't crash
            continue

    print("Scan done - " + str(found) + " signals", flush=True)

# =========================
# Main loop
# =========================
print("Starting scanner...", flush=True)

while True:
    try:
        scan()
    except Exception as e:
        print("Error:", str(e), flush=True)
    time.sleep(SCAN_INTERVAL_SEC)
