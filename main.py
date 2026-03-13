import os
import time
import json
import threading
from collections import deque, defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import websocket

# =========================
# Railway keep-alive
# =========================

PORT = int(os.environ.get("PORT", "8080"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


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

FAST_ANTI_SPAM_SEC = 30 * 60
SLOW_ANTI_SPAM_SEC = 60 * 60

FUNDING_ALERT_COOLDOWN_SEC = 60 * 60
ACCUMULATION_ALERT_COOLDOWN_SEC = 2 * 60 * 60
FUNDING_SPIKE_ALERT_COOLDOWN_SEC = 60 * 60

FUNDING_SPIKE_MIN_DELTA = 0.50

MOVE_EXPLOSIVE_1M = 10.0
MOVE_1M = 2.5
MOVE_3M = 4.0
MOVE_5M = 5.0
MOVE_15M = 8.0
MOVE_1H = 15.0
MOVE_24H = 30.0

FUNDING_EXTREME_POS = 0.5
FUNDING_EXTREME_NEG = -0.5
FUNDING_ABS_FOR_BOOST = 0.5

TOP_N_SYMBOLS = 500
RESUBSCRIBE_EVERY_SEC = 10 * 60

MIN_OI_SOFT = 50_000
MAX_OI = 800_000_000

ALLOW_LOW_OI_IF_STRONG_MOVE = True
LOW_OI_FAST_SCORE_BYPASS = 7

MIN_TURNOVER_24H_NORMAL = 400_000
MIN_TURNOVER_24H_EXPLOSIVE = 150_000

ORDERBOOK_TTL_SEC = 25
MAX_SPREAD_NORMAL_PCT = 0.45
MAX_SPREAD_EXPLOSIVE_PCT = 0.80
DEPTH_BAND_NORMAL_PCT = 0.50
DEPTH_BAND_EXPLOSIVE_PCT = 0.80
MIN_DEPTH_NORMAL_USDT = 20_000
MIN_DEPTH_EXPLOSIVE_USDT = 8_000

ENABLE_1M_VOLUME_FILTER = True
MIN_1M_TURNOVER_USDT = 800

OI_TREND_MIN_PCT = 1.5
VOLUME_SPIKE_MIN = 1.8
PRICE_STABLE_MAX_PCT = 1.2
FUNDING_TREND_MIN = 0.01
ACCUM_HISTORY_MIN = 4

# =========================
# State
# =========================

lock = threading.Lock()
price_history = defaultdict(lambda: deque(maxlen=2500))
symbol_metadata = {}
oi_history = defaultdict(lambda: deque(maxlen=20))
funding_history = defaultdict(lambda: deque(maxlen=20))
volume_history = defaultdict(lambda: deque(maxlen=20))

last_fast_alert = {}
last_slow_alert = {}

last_alert_funding = {}
last_accum_alert = {}
last_funding_spike_alert = {}
anchors = {}
orderbook_cache = {}

last_price_cache = {}
last_funding_seen = {}

ws_app = None
ws_lock = threading.Lock()
current_symbols = []

# =========================
# Utils
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


def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code != 200:
            print("Telegram error: " + str(r.status_code), flush=True)
    except Exception as e:
        print("Telegram error: " + str(e), flush=True)


def calc_change(prices_list, lookback_sec):
    if len(prices_list) < 2:
        return 0.0

    target_time = now_ts() - lookback_sec
    old_price = None

    for ts, p in prices_list:
        if ts >= target_time:
            old_price = p
            break

    if old_price is None:
        return 0.0

    return pct_change(old_price, prices_list[-1][1])


def calc_trend(hist_deque):
    items = list(hist_deque)
    if len(items) < ACCUM_HISTORY_MIN:
        return 0.0

    changes = []
    for i in range(1, len(items)):
        if items[i - 1] > 0:
            changes.append(((items[i] - items[i - 1]) / items[i - 1]) * 100.0)

    if not changes:
        return 0.0

    return sum(changes) / len(changes)


def strength_from_score(score):
    if score >= 10:
        return "🔥🔥🔥🔥 קריטי!"
    if score >= 7:
        return "🔥🔥🔥 חזק מאוד"
    return "🔥🔥 חזק"


def update_anchors(symbol, ts, price):
    with lock:
        a = anchors.get(symbol, {})

        t15 = a.get("t15")
        if (t15 is None) or (ts - t15[0] > 20 * 60):
            a["t15"] = (ts, price)

        t60 = a.get("t60")
        if (t60 is None) or (ts - t60[0] > 75 * 60):
            a["t60"] = (ts, price)

        anchors[symbol] = a


def fetch_orderbook_metrics(symbol):
    try:
        url = (
            "https://api.bybit.com/v5/market/orderbook"
            f"?category=linear&symbol={symbol}&limit=50"
        )
        r = requests.get(url, timeout=6, headers=HEADERS)
        ob = r.json().get("result", {}) or {}
        bids = ob.get("b", []) or []
        asks = ob.get("a", []) or []

        if not bids or not asks:
            return (False, 0.0, None)

        best_bid = safe_float(bids[0][0])
        best_ask = safe_float(asks[0][0])

        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return (False, 0.0, None)

        mid = (best_bid + best_ask) / 2.0
        spread_pct = ((best_ask - best_bid) / mid) * 100.0

        return (True, spread_pct, {"mid": mid, "bids": bids, "asks": asks})
    except Exception:
        return (False, 0.0, None)


def calc_depth_within_band(payload, band_pct):
    try:
        mid = payload["mid"]
        lower = mid * (1.0 - band_pct / 100.0)
        upper = mid * (1.0 + band_pct / 100.0)

        bid_depth = sum(
            safe_float(px) * safe_float(qty)
            for px, qty in payload["bids"]
            if safe_float(px) >= lower
        )
        ask_depth = sum(
            safe_float(px) * safe_float(qty)
            for px, qty in payload["asks"]
            if safe_float(px) <= upper
        )

        return bid_depth + ask_depth
    except Exception:
        return 0.0


def get_orderbook_cached(symbol):
    ts = now_ts()
    c = orderbook_cache.get(symbol)

    if c and (ts - c["ts"] <= ORDERBOOK_TTL_SEC):
        return c

    ok, spread_pct, payload = fetch_orderbook_metrics(symbol)
    c = {"ts": ts, "ok": ok, "spread_pct": spread_pct, "payload": payload}
    orderbook_cache[symbol] = c
    return c


def fetch_1m_turnover_usdt(symbol):
    try:
        url = (
            "https://api.bybit.com/v5/market/kline"
            f"?category=linear&symbol={symbol}&interval=1&limit=1"
        )
        r = requests.get(url, timeout=6, headers=HEADERS)
        lst = r.json().get("result", {}).get("list", []) or []

        if lst and len(lst[0]) > 6:
            return safe_float(lst[0][6])

        return 0.0
    except Exception:
        return 0.0


# =========================
# Alerts
# =========================

def send_funding_spike_alert(symbol, prev_f, new_f, price):
    delta = new_f - prev_f
    arrow = "⬆️" if delta > 0 else "⬇️"

    msg = (
        "📉 <b>Funding Spike</b>\n"
        + "<b>" + symbol + "</b>\n\n"
        + "קודם: <b>" + str(round(prev_f, 4)) + "%</b>\n"
        + "עכשיו: <b>" + str(round(new_f, 4)) + "%</b>\n"
        + "שינוי: <b>" + arrow + " " + str(round(delta, 4)) + "%</b>\n"
        + ("💵 מחיר: $" + str(price) + "\n" if price and price > 0 else "")
    )
    send_telegram(msg)


def send_fast_alert(symbol, price, change_1m, change_3m, change_5m, signals, score):
    with lock:
        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)
        funding = meta.get("funding", 0)

    is_long = (change_1m > 0 and change_3m > 0)
    direction = "🟢 LONG" if is_long else "🔴 SHORT"
    funding_state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        direction + " <b>" + symbol + "</b>\n"
        + strength_from_score(score) + "\n\n"
        + "<b>סיגנלים (מהיר):</b>\n"
        + "\n".join("- " + s for s in signals) + "\n\n"
        + "⏱️ 1m: " + str(round(change_1m, 1))
        + "% | 3m: " + str(round(change_3m, 1))
        + "% | 5m: " + str(round(change_5m, 1)) + "%\n"
        + "💵 מחיר: $" + str(price) + "\n"
        + "📉 Funding: " + str(round(funding, 4)) + "% (" + funding_state + ")\n"
        + "📊 OI: $" + str(round(oi / 1_000_000, 3)) + "M"
    )
    send_telegram(msg)


def send_slow_alert(symbol, price, change_15m, change_1h, change_24h):
    with lock:
        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)
        funding = meta.get("funding", 0)

    direction = "🟢 LONG" if (change_1h > 0 or change_24h > 0) else "🔴 SHORT"
    max_move = max(abs(change_15m), abs(change_1h), abs(change_24h))

    if max_move >= 50:
        strength = "🔥🔥🔥🔥 תנועה חריגה מאוד"
    elif max_move >= 30:
        strength = "🔥🔥🔥 חזק מאוד"
    else:
        strength = "🔥🔥 חזק"

    signals = []
    if abs(change_15m) >= MOVE_15M:
        signals.append("⏳ 15m: " + str(round(change_15m, 1)) + "%")
    if abs(change_1h) >= MOVE_1H:
        signals.append("🕐 1h: " + str(round(change_1h, 1)) + "%")
    if abs(change_24h) >= MOVE_24H:
        signals.append("📅 24h: " + str(round(change_24h, 1)) + "%")

    funding_state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        direction + " <b>" + symbol + "</b>\n"
        + strength + "\n\n"
        + "<b>סיגנלים (איטי):</b>\n"
        + ("\n".join("- " + s for s in signals) if signals else "- תנועה משמעותית")
        + "\n\n"
        + "💵 מחיר: $" + str(price) + "\n"
        + "📉 Funding: " + str(round(funding, 4)) + "% (" + funding_state + ")\n"
        + "📊 OI: $" + str(round(oi / 1_000_000, 3)) + "M"
    )
    send_telegram(msg)


def send_accumulation_alert(symbol, price, direction, oi_trend, funding_trend, vol_spike, funding):
    funding_state = "Short pays Long" if funding < 0 else "Long pays Short"
    dir_txt = "🟢 LONG מוקדם" if direction == "long" else "🔴 SHORT מוקדם"

    signals = [
        "🔍 OI עולה בעקביות: " + str(round(oi_trend, 2)) + "% ממוצע",
        "🧘 מחיר יציב - צבירה לפני מהלך",
    ]

    if vol_spike >= VOLUME_SPIKE_MIN:
        signals.insert(1, "📦 Volume קפץ: x" + str(round(vol_spike, 1)) + " מהרגיל")

    if abs(funding_trend) >= FUNDING_TREND_MIN:
        signals.insert(1, "📉 Funding מתחיל לזוז: " + str(round(funding_trend, 4)) + "%")

    msg = (
        dir_txt + " <b>" + symbol + "</b>\n"
        + "💡 זיהוי מוקדם - לפני שהמחיר זז\n\n"
        + "<b>סיגנלים:</b>\n"
        + "\n".join("- " + s for s in signals) + "\n\n"
        + "💵 מחיר: $" + str(price) + "\n"
        + "📉 Funding: " + str(round(funding, 4)) + "% (" + funding_state + ")"
    )
    send_telegram(msg)


def send_funding_extreme_alert(symbol, funding, price):
    direction = "🟢 Bias LONG" if funding < 0 else "🔴 Bias SHORT"
    state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        direction + " <b>" + symbol + "</b>\n"
        + "⚠️ <b>Funding חריג</b>\n\n"
        + "📉 Funding: <b>" + str(round(funding, 4)) + "%</b>\n"
        + "🧾 מצב: " + state + "\n"
        + "💵 מחיר: $" + str(price) + "\n"
    )
    send_telegram(msg)


# =========================
# Metadata updater
# =========================

def update_metadata():
    while True:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/tickers?category=linear",
                timeout=10,
                headers=HEADERS,
            )
            data = r.json().get("result", {}).get("list", [])
            ts = now_ts()
            local = {}

            for t in data:
                symbol = t.get("symbol")
                if not symbol:
                    continue

                oi = safe_float(t.get("openInterestValue", 0))
                funding = safe_float(t.get("fundingRate", 0)) * 100.0
                turnover = safe_float(t.get("turnover24h", 0))

                prev_f = last_funding_seen.get(symbol)
                last_funding_seen[symbol] = funding

                if prev_f is not None:
                    delta = funding - prev_f
                    last_spike = last_funding_spike_alert.get(symbol, 0)
                    if (
                        abs(delta) >= FUNDING_SPIKE_MIN_DELTA
                        and (ts - last_spike) >= FUNDING_SPIKE_ALERT_COOLDOWN_SEC
                    ):
                        with lock:
                            price = last_price_cache.get(symbol, 0.0)
                        send_funding_spike_alert(symbol, prev_f, funding, price)
                        last_funding_spike_alert[symbol] = ts

                local[symbol] = {
                    "oi": oi,
                    "turnover24h": turnover,
                    "funding": funding,
                    "volume24h": safe_float(t.get("volume24h", 0)),
                    "price24hPcnt": safe_float(t.get("price24hPcnt", 0)) * 100.0,
                    "last_update": ts,
                }

                oi_history[symbol].append(oi)
                funding_history[symbol].append(funding)
                volume_history[symbol].append(turnover)

            with lock:
                symbol_metadata.update(local)

            print("Updated metadata for " + str(len(local)) + " symbols", flush=True)

        except Exception as e:
            print("Metadata error: " + str(e), flush=True)

        time.sleep(30)


threading.Thread(target=update_metadata, daemon=True).start()

# =========================
# Symbols
# =========================

def get_top_symbols():
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers?category=linear",
            timeout=10,
            headers=HEADERS,
        )
        tickers = r.json().get("result", {}).get("list", []) or []
        valid = []

        for t in tickers:
            symbol = t.get("symbol") or ""
            if not symbol:
                continue

            turnover = safe_float(t.get("turnover24h", 0))
            oi = safe_float(t.get("openInterestValue", 0))

            if turnover <= 0 or oi > MAX_OI:
                continue

            valid.append((symbol, turnover + oi * 0.01))

        valid.sort(key=lambda x: x[1], reverse=True)
        symbols = [s for s, _ in valid[:TOP_N_SYMBOLS]]

        print("Tracking " + str(len(symbols)) + " symbols", flush=True)
        return symbols

    except Exception as e:
        print("Error getting symbols: " + str(e), flush=True)
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def pass_oi_gate(symbol, fast_score, change_24h):
    with lock:
        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)

    if MIN_OI_SOFT <= oi <= MAX_OI:
        return True
    if abs(change_24h) >= MOVE_24H:
        return True
    if ALLOW_LOW_OI_IF_STRONG_MOVE and fast_score >= LOW_OI_FAST_SCORE_BYPASS:
        return True
    return False


# =========================
# Accumulation checker
# =========================

def check_accumulation(symbol, price):
    ts = now_ts()
    last = last_accum_alert.get(symbol)

    if last and (ts - last) < ACCUMULATION_ALERT_COOLDOWN_SEC:
        return

    with lock:
        oi_hist = list(oi_history[symbol])
        fund_hist = list(funding_history[symbol])
        vol_hist = list(volume_history[symbol])
        prices_list = list(price_history[symbol])
        funding = symbol_metadata.get(symbol, {}).get("funding", 0.0)

    if len(oi_hist) < ACCUM_HISTORY_MIN:
        return

    price_change = abs(calc_change(prices_list, 180))
    if price_change > PRICE_STABLE_MAX_PCT:
        return

    oi_trend = calc_trend(oi_hist)
    funding_trend = calc_trend(fund_hist)

    vol_spike = 1.0
    if len(vol_hist) >= 4:
        avg = sum(vol_hist[:-1]) / len(vol_hist[:-1])
        if avg > 0:
            vol_spike = vol_hist[-1] / avg

    if oi_trend < OI_TREND_MIN_PCT:
        return

    if vol_spike < VOLUME_SPIKE_MIN and abs(funding_trend) < FUNDING_TREND_MIN:
        return

    if funding >= 0.05 or funding_trend > 0:
        direction = "long"
    elif funding <= -0.05 or funding_trend < 0:
        direction = "short"
    else:
        return

    send_accumulation_alert(symbol, price, direction, oi_trend, funding_trend, vol_spike, funding)

    with lock:
        last_accum_alert[symbol] = ts

    print("ACCUM ALERT: " + symbol + " " + direction, flush=True)


# =========================
# Core signal checker
# =========================

def check_signals(symbol, price):
    ts = now_ts()

    with lock:
        last_fast = last_fast_alert.get(symbol)
        last_slow = last_slow_alert.get(symbol)

    fast_blocked = (last_fast is not None) and (ts - last_fast < FAST_ANTI_SPAM_SEC)
    slow_blocked = (last_slow is not None) and (ts - last_slow < SLOW_ANTI_SPAM_SEC
