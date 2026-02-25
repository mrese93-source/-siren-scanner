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
# Imports
# =========================

import requests  # noqa: E402

# =========================
# Config - OPTIMIZED FOR SPEED
# =========================

SCAN_INTERVAL_SEC = 60             # every 1 minute
LOOKBACK_5M_SEC = 300              # 5min for scalping
LOOKBACK_15M_SEC = 900             # 15min for swing
LOOKBACK_1H_SEC = 3600             # 1H for trend confirmation
ALERT_COOLDOWN_SEC = 4 * 60 * 60   # 4 hours

# Filters
MIN_OI = 1_000_000
MAX_OI = 300_000_000

# Pump/Dump - STRICTER
EXTREME_MOVE_1H = 15.0
PUMP_24H_PCT = 25.0
DUMP_24H_PCT = -25.0

# Volume - MORE AGGRESSIVE
VOL_DELTA_MIN_USD = 150_000
VOL_SPIKE_EXTREME = 8.0
VOL_SPIKE_STRONG = 5.0
VOL_SPIKE_MED = 3.0

# Movement - SCALPING FOCUSED
MOVE_EXPLOSIVE = 8.0
MOVE_STRONG = 4.0
MOVE_MED = 2.0

# Funding
FUNDING_HIGH = 0.10
FUNDING_EXTREME = 0.15
FUNDING_TREND = 0.05

# Price Acceleration
ACCELERATION_THRESHOLD = 1.5

# Large Order Detection
LARGE_ORDER_MIN_USD = 500_000

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# =========================
# State
# =========================

symbol_state = {}
alerted = {}
market_history = deque(maxlen=5)
last_market_alert_key = ""


def get_state(symbol):
    if symbol not in symbol_state:
        symbol_state[symbol] = {
            "prices": deque(maxlen=180),          # 3 hours of data
            "turnover_delta": deque(maxlen=180),
            "oi": deque(maxlen=180),
            "funding": deque(maxlen=180),
            "prev_turnover24h": None,
            "price_5m_ago": None,
            "price_15m_ago": None,
            "price_1h_ago": None,
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
        requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print("Telegram error:", str(e), flush=True)


def get_bybit_tickers():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        data = r.json()
        return data["result"]["list"]
    except Exception as e:
        print("Bybit error:", str(e), flush=True)
        return []


def get_recent_trades(symbol, limit=100):
    """
    Get recent trades to detect large orders
    """
    try:
        url = f"https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={symbol}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            return []
        return data["result"]["list"]
    except Exception:
        return []


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
        prev = items[i - 1][1]
        curr = items[i][1]
        if prev > 0:
            changes.append(((curr - prev) / prev) * 100.0)
    return calc_avg(changes) if changes else 0.0


def calc_funding_trend(funding_deque):
    if len(funding_deque) < 3:
        return 0.0
    return funding_deque[-1][1] - funding_deque[0][1]


# =========================
# Advanced Detection
# =========================

def detect_price_acceleration(prices_deque, now_ts):
    """
    Detect if price is accelerating (moving faster than before)
    Returns: acceleration_ratio (1.0 = normal, >1.5 = accelerating)
    """
    if len(prices_deque) < 20:
        return 1.0

    recent_5m = price_change_over_lookback(prices_deque, now_ts, 300)          # last 5m
    prev_10m = price_change_over_lookback(prices_deque, now_ts - 300, 600)     # 5-15m ago baseline

    if abs(prev_10m) < 0.5 or prev_10m == 0:
        return 1.0

    ratio = abs(recent_5m) / abs(prev_10m)
    return ratio


def detect_large_orders(trades, price):
    """
    Detect whale orders from recent trades
    Returns: (total_buy_usd, total_sell_usd, net_bias)
    """
    buy_volume = 0.0
    sell_volume = 0.0

    for trade in trades:
        try:
            size = safe_float(trade.get("size", 0))
            side = trade.get("side", "")
            trade_price = safe_float(trade.get("price", 0))
            value_usd = size * trade_price

            if side == "Buy":
                buy_volume += value_usd
            else:
                sell_volume += value_usd
        except Exception:
            continue

    net_bias = buy_volume - sell_volume
    return buy_volume, sell_volume, net_bias


def detect_structure_break(prices_deque, now_ts):
    """
    Simple structure break detection:
    - Bullish: Making higher highs
    - Bearish: Making lower lows
    Returns: ("bullish_break", "bearish_break", "none")
    """
    if len(prices_deque) < 30:
        return "none"

    recent_prices = []
    target_ts = now_ts - 3600  # 1 hour ago

    for ts, p in prices_deque:
        if ts >= target_ts:
            recent_prices.append(p)

    if len(recent_prices) < 10:
        return "none"

    curr_price = recent_prices[-1]
    recent_high = max(recent_prices[:-5])
    recent_low = min(recent_prices[:-5])

    if curr_price > recent_high * 1.005:
        return "bullish_break"

    if curr_price < recent_low * 0.995:
        return "bearish_break"

    return "none"


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
            turnover24h = safe_float(t.get("turnover24h", 0))
            funding = safe_float(t.get("fundingRate", 0)) * 100.0
            oi = safe_float(t.get("openInterestValue", 0))

            if price <= 0:
                continue
            if oi < MIN_OI or oi > MAX_OI:
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

            # Cooldown
            if symbol in alerted and now - alerted[symbol] < ALERT_COOLDOWN_SEC:
                continue

            if len(st["prices"]) < 10:
                continue

            # Calculate changes
            change_5m = price_change_over_lookback(st["prices"], now, LOOKBACK_5M_SEC)
            change_15m = price_change_over_lookback(st["prices"], now, LOOKBACK_15M_SEC)
            change_1h = price_change_over_lookback(st["prices"], now, LOOKBACK_1H_SEC)

            # Advanced detection
            acceleration = detect_price_acceleration(st["prices"], now)
            structure = detect_structure_break(st["prices"], now)

            # Volume analysis
            deltas = [x[1] for x in list(st["turnover_delta"])[-10:] if x[1] >= VOL_DELTA_MIN_USD]
            vol_avg = calc_avg(deltas[:-1]) if len(deltas) >= 3 else None
            vol_spike = (deltas[-1] / vol_avg) if vol_avg and vol_avg > 0 else 1.0

            oi_trend = calc_oi_trend(st["oi"])
            funding_trend = calc_funding_trend(st["funding"])

            # ======================
            # FILTERS
            # ======================

            if abs(change_1h) >= EXTREME_MOVE_1H:
                continue

            if abs(funding) >= FUNDING_EXTREME:
                continue

            if oi_trend < -2.0 and abs(change_15m) > 3:
                continue

            if vol_spike < 1.5 and abs(change_15m) > 5:
                continue

            # ======================
            # SIGNAL COLLECTION
            # ======================

            long_signals = []
            short_signals = []

            # LONG
            if structure == "bullish_break":
                long_signals.append(("🔥 שבירת מבנה Bullish", 4))

            if acceleration >= ACCELERATION_THRESHOLD and change_5m > 0:
                long_signals.append((f"⚡ האצה x{round(acceleration, 1)}", 3))

            if vol_spike >= VOL_SPIKE_EXTREME and change_5m >= 0:
                long_signals.append((f"💥 Volume פיצוץ x{round(vol_spike, 1)}", 4))
            elif vol_spike >= VOL_SPIKE_STRONG and change_5m >= 0:
                long_signals.append((f"💥 Volume spike x{round(vol_spike, 1)}", 3))
            elif vol_spike >= VOL_SPIKE_MED and change_5m >= 0:
                long_signals.append((f"Volume spike x{round(vol_spike, 1)}", 2))

            if change_5m >= MOVE_EXPLOSIVE:
                long_signals.append((f"🚀 +{round(change_5m, 1)}% ב-5 דקות!", 4))
            elif change_5m >= MOVE_STRONG:
                long_signals.append((f"📈 +{round(change_5m, 1)}% ב-5 דקות", 3))

            if change_15m >= MOVE_STRONG and change_5m >= MOVE_MED:
                long_signals.append((f"+{round(change_15m, 1)}% ב-15 דקות", 2))

            if change_1h >= 3.0 and change_15m >= 2.0:
                long_signals.append(("✅ 1H + 15M בכיוון", 3))
            elif change_1h > 0 and change_15m >= MOVE_MED:
                long_signals.append(("✅ 1H חיובי", 1))

            if oi_trend >= 3.0 and change_5m >= 0:
                long_signals.append(("📊 OI עולה חזק", 3))
            elif oi_trend >= 2.0 and change_15m >= 0:
                long_signals.append(("OI עולה", 2))

            if funding_trend >= FUNDING_TREND and funding >= FUNDING_TREND and funding < FUNDING_HIGH:
                long_signals.append(("💹 Funding מטפס", 2))
            elif 0 < funding < 0.05:
                long_signals.append(("✅ Funding בריא", 1))

            if 5 <= change_24h <= 40:
                long_signals.append((f"24h: +{round(change_24h, 1)}%", 1))

            if market_trend == "bear" and change_15m >= 5:
                long_signals.append(("⚡ חזק נגד שוק יורד", 3))

            # SHORT
            if structure == "bearish_break":
                short_signals.append(("🔥 שבירת מבנה Bearish", 4))

            if acceleration >= ACCELERATION_THRESHOLD and change_5m < 0:
                short_signals.append((f"⚡ האצה x{round(acceleration, 1)}", 3))

            if vol_spike >= VOL_SPIKE_EXTREME and change_5m <= 0:
                short_signals.append((f"💥 Volume פיצוץ x{round(vol_spike, 1)}", 4))
            elif vol_spike >= VOL_SPIKE_STRONG and change_5m <= 0:
                short_signals.append((f"💥 Volume spike x{round(vol_spike, 1)}", 3))
            elif vol_spike >= VOL_SPIKE_MED and change_5m <= 0:
                short_signals.append((f"Volume spike x{round(vol_spike, 1)}", 2))

            if change_5m <= -MOVE_EXPLOSIVE:
                short_signals.append((f"💥 {round(change_5m, 1)}% ב-5 דקות!", 4))
            elif change_5m <= -MOVE_STRONG:
                short_signals.append((f"📉 {round(change_5m, 1)}% ב-5 דקות", 3))

            if change_15m <= -MOVE_STRONG and change_5m <= -MOVE_MED:
                short_signals.append((f"{round(change_15m, 1)}% ב-15 דקות", 2))

            if change_1h <= -3.0 and change_15m <= -2.0:
                short_signals.append(("✅ 1H + 15M בכיוון", 3))
            elif change_1h < 0 and change_15m <= -MOVE_MED:
                short_signals.append(("✅ 1H שלילי", 1))

            if oi_trend >= 3.0 and change_5m <= -1.0:
                short_signals.append(("📊 OI עולה + חולשה", 3))
            elif oi_trend >= 2.0 and change_15m <= -0.5:
                short_signals.append(("OI עולה + חולשה", 2))

            if funding_trend <= -FUNDING_TREND and funding <= -FUNDING_TREND:
                short_signals.append(("💹 Funding יורד", 2))
            elif -0.05 < funding < 0:
                short_signals.append(("✅ Funding בריא", 1))

            if -40 <= change_24h <= -5:
                short_signals.append((f"24h: {round(change_24h, 1)}%", 1))

            if market_trend == "bull" and change_15m <= -5:
                short_signals.append(("⚡ חלש נגד שוק עולה", 3))

            # Block short on strong bullish momentum
            if change_5m >= 3.0 or change_15m >= 5.0:
                short_signals = []

            # ======================
            # SCORING
            # ======================

            long_score = sum(w for _, w in long_signals)
            short_score = sum(w for _, w in short_signals)

            is_pump = change_24h >= PUMP_24H_PCT
            is_dump = change_24h <= DUMP_24H_PCT

            if is_pump or is_dump:
                conf = 0
                if vol_spike >= 3.0:
                    conf += 1
                if oi_trend >= 2.0:
                    conf += 1
                if acceleration >= 1.5:
                    conf += 1
                if abs(change_5m) >= 3.0:
                    conf += 1

                if conf < 3:
                    continue

            long_has_extreme = any(w >= 4 for _, w in long_signals)
            short_has_extreme = any(w >= 4 for _, w in short_signals)

            long_has_strong = any(w >= 3 for _, w in long_signals)
            short_has_strong = any(w >= 3 for _, w in short_signals)

            long_qualifies = (long_score >= 6 and long_has_strong) or (long_score >= 5 and long_has_extreme)
            short_qualifies = (short_score >= 6 and short_has_strong) or (short_score >= 5 and short_has_extreme)

            if not long_qualifies and not short_qualifies:
                continue

            is_long = long_qualifies and long_score >= short_score
            is_short = short_qualifies and short_score > long_score

            if not (is_long or is_short):
                continue

            # ======================
            # BUILD MESSAGE
            # ======================

            if is_long:
                direction = "🟢 LONG"
                signals_used = long_signals
                score = long_score
            else:
                direction = "🔴 SHORT"
                signals_used = short_signals
                score = short_score

            if score >= 10:
                strength = "🔥🔥🔥🔥🔥 קריטי!"
            elif score >= 8:
                strength = "🔥🔥🔥🔥 חזק מאוד"
            elif score >= 6:
                strength = "🔥🔥🔥 חזק"
            else:
                strength = "🔥🔥 בינוני-חזק"

            if market_trend == "bull":
                trend_txt = f"🌍 שוק: עולה ({round(bull_pct)}% ירוק)"
            elif market_trend == "bear":
                trend_txt = f"🌍 שוק: יורד ({round(100 - bull_pct)}% אדום)"
            else:
                trend_txt = "🌍 שוק: ניטרלי"

            signals_txt = "\n".join("- " + s for s, _ in signals_used)

            struct_txt = ""
            if structure != "none":
                struct_txt = f"📊 Structure: {structure}\n"

            accel_txt = ""
            if acceleration >= ACCELERATION_THRESHOLD:
                accel_txt = f"⚡ Acceleration: x{round(acceleration, 1)}\n"

            msg = (
                f"{direction} <b>{symbol}</b>\n"
                f"{strength}\n\n"
                f"<b>סיגנלים:</b>\n{signals_txt}\n\n"
                f"{struct_txt}"
                f"{accel_txt}"
                f"⏱️ 5m: {round(change_5m, 1)}% | 15m: {round(change_15m, 1)}% | 1H: {round(change_1h, 1)}%\n"
                f"📆 24h: {round(change_24h, 1)}%\n"
                f"💵 מחיר: ${price}\n"
                f"📦 ΔVolume: ${round(delta_turn/1_000_000, 2)}M | Spike: x{round(vol_spike, 1)}\n"
                f"📊 OI Trend: {round(oi_trend, 1)}%\n"
                f"💹 Funding: {round(funding, 4)}%\n"
                f"{trend_txt}"
            )

            send_telegram(msg)
            alerted[symbol] = now
            found += 1
            print(
                f"🎯 Alert: {symbol} {direction} score={score} | 5m={round(change_5m,1)}% | accel={round(acceleration,1)}",
                flush=True,
            )

        except Exception:
            continue

    print(f"✅ Scan done - {found} signals", flush=True)


# =========================
# Main loop
# =========================

print("🚀 Starting FAST scanner (scalping + swing optimized)…", flush=True)
print(f"📊 Scan every {SCAN_INTERVAL_SEC}s | Cooldown: {ALERT_COOLDOWN_SEC/3600}h", flush=True)

while True:
    try:
        scan()
    except Exception as e:
        print("❌ Error:", str(e), flush=True)
    time.sleep(SCAN_INTERVAL_SEC)
