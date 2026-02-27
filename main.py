import os
import time
import json
import threading
from collections import deque, defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import websocket  # pip install websocket-client

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
    print(f"HTTP server on port {PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# =========================
# Config
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Cooldowns
ALERT_COOLDOWN_FAST_SEC = 45 * 60       # 45 minutes
ALERT_COOLDOWN_SLOW_SEC = 6 * 60 * 60   # 6 hours
ALERT_COOLDOWN_FUNDING_SEC = 2 * 60 * 60  # funding timing + extreme

# FAST movement thresholds (minutes)
MOVE_EXPLOSIVE_1M = 10.0
MOVE_1M = 2.5
MOVE_3M = 4.0
MOVE_5M = 5.0

# SLOW movement thresholds (15m/1h/24h)
MOVE_15M = 8.0
MOVE_1H = 15.0
MOVE_24H = 30.0

# Funding extreme thresholds (in %)
# (שומר אותו דבר כמו שביקשת)
FUNDING_EXTREME_POS = 1.0     # +1% and above
FUNDING_EXTREME_NEG = -1.0    # -1% and below
FUNDING_ABS_FOR_BOOST = 0.5   # funding>|0.5| boosts score if move signal exists

# Funding timing alerts
FUNDING_SOON_WINDOW_SEC = 30 * 60      # 30 דקות לפני התשלום
FUNDING_IMMINENT_WINDOW_SEC = 7 * 60   # 7 דקות לפני התשלום (רק אם קיצוני)

# Tracking size and refresh
TOP_N_SYMBOLS = 500
RESUBSCRIBE_EVERY_SEC = 10 * 60

# OI filter (soft)
MIN_OI_SOFT = 50_000
MAX_OI = 800_000_000

# Allow low OI if strong fast move (catch tiny caps pumps)
ALLOW_LOW_OI_IF_STRONG_MOVE = True
LOW_OI_FAST_SCORE_BYPASS = 7

# -------- Anti-stuck (liquidity) - dynamic (לא לפספס) --------
MIN_TURNOVER_24H_NORMAL = 1_000_000     # סיגנל רגיל – לפחות 1M USDT מחזור יומי
MIN_TURNOVER_24H_EXPLOSIVE = 250_000    # EXPLOSIVE – גם 250K מספיק

# --- Advanced anti-stuck liquidity filters (only checked when score >= 4) ---
ORDERBOOK_TTL_SEC = 25  # cache orderbook per symbol

# Spread thresholds (%)
MAX_SPREAD_NORMAL_PCT = 0.35
MAX_SPREAD_EXPLOSIVE_PCT = 0.60

# Depth within band around mid (%)
DEPTH_BAND_NORMAL_PCT = 0.50
DEPTH_BAND_EXPLOSIVE_PCT = 0.80

MIN_DEPTH_NORMAL_USDT = 20_000
MIN_DEPTH_EXPLOSIVE_USDT = 8_000

# Optional: last 1m candle turnover filter
ENABLE_1M_VOLUME_FILTER = True
MIN_1M_TURNOVER_USDT = 3_000

# =========================
# State
# =========================

lock = threading.Lock()

# symbol -> deque of (timestamp, price)
price_history = defaultdict(lambda: deque(maxlen=2500))

# symbol -> metadata
symbol_metadata = {}

# cooldown states
last_alert_fast = {}
last_alert_slow = {}
last_alert_funding = {}  # we'll use keys like symbol+"_soon", symbol+"_imminent", symbol+"_extreme"

# anchors for 15m and 1h
anchors = {}

# orderbook cache (no lock needed; best effort)
orderbook_cache = {}  # symbol -> {"ts": float, "ok": bool, "spread_pct": float, "payload": dict|None}

# websocket global
ws_app = None
ws_lock = threading.Lock()
current_symbols = []

# =========================
# Utils
# =========================

def now_ts() -> float:
    return time.time()

def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def pct_change(old_price: float, new_price: float) -> float:
    if old_price <= 0:
        return 0.0
    return ((new_price - old_price) / old_price) * 100.0

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

def calc_change(prices_list: list, lookback_sec: int) -> float:
    """
    prices_list: list of (timestamp, price), sorted by time.
    """
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

def strength_from_score(score: int) -> str:
    if score >= 10:
        return "🔥🔥🔥🔥 קריטי!"
    if score >= 7:
        return "🔥🔥🔥 חזק מאוד"
    return "🔥🔥 חזק"

def update_anchors(symbol: str, ts: float, price: float):
    with lock:
        a = anchors.get(symbol, {})

        t15 = a.get("t15")
        if (t15 is None) or (ts - t15[0] > 20 * 60):
            a["t15"] = (ts, price)

        t60 = a.get("t60")
        if (t60 is None) or (ts - t60[0] > 75 * 60):
            a["t60"] = (ts, price)

        anchors[symbol] = a

def fetch_orderbook_metrics(symbol: str):
    """
    Returns (ok, spread_pct, payload)
    payload = {"mid": mid, "bids": bids, "asks": asks}
    bids/asks are lists of [price, size]
    """
    try:
        url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=50"
        r = requests.get(url, timeout=6)
        data = r.json()
        ob = data.get("result", {}) or {}
        bids = ob.get("b", []) or []
        asks = ob.get("a", []) or []

        if not bids or not asks:
            return (False, 0.0, None)

        best_bid = safe_float(bids[0][0], 0)
        best_ask = safe_float(asks[0][0], 0)
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return (False, 0.0, None)

        mid = (best_bid + best_ask) / 2.0
        spread_pct = ((best_ask - best_bid) / mid) * 100.0

        payload = {"mid": mid, "bids": bids, "asks": asks}
        return (True, spread_pct, payload)

    except Exception:
        return (False, 0.0, None)

def calc_depth_within_band(payload, band_pct: float) -> float:
    """
    Depth in USDT within +/- band_pct% from mid: (bid_depth + ask_depth)
    """
    try:
        mid = payload["mid"]
        bids = payload["bids"]
        asks = payload["asks"]

        lower = mid * (1.0 - band_pct / 100.0)
        upper = mid * (1.0 + band_pct / 100.0)

        bid_depth = 0.0
        for px, qty in bids:
            p = safe_float(px, 0)
            q = safe_float(qty, 0)
            if p < lower:
                break
            bid_depth += p * q

        ask_depth = 0.0
        for px, qty in asks:
            p = safe_float(px, 0)
            q = safe_float(qty, 0)
            if p > upper:
                break
            ask_depth += p * q

        return bid_depth + ask_depth
    except Exception:
        return 0.0

def get_orderbook_cached(symbol: str):
    ts = now_ts()
    c = orderbook_cache.get(symbol)
    if c and (ts - c["ts"] <= ORDERBOOK_TTL_SEC):
        return c

    ok, spread_pct, payload = fetch_orderbook_metrics(symbol)
    c = {"ts": ts, "ok": ok, "spread_pct": spread_pct, "payload": payload}
    orderbook_cache[symbol] = c
    return c

def fetch_1m_turnover_usdt(symbol: str) -> float:
    """
    Bybit kline: last 1m candle turnover (USDT).
    """
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=1&limit=1"
        r = requests.get(url, timeout=6)
        data = r.json()
        lst = data.get("result", {}).get("list", []) or []
        if not lst:
            return 0.0
        candle = lst[0]
        # format usually: [start, open, high, low, close, volume, turnover]
        if len(candle) > 6:
            return safe_float(candle[6], 0)
        return 0.0
    except Exception:
        return 0.0

# =========================
# REST Metadata updater
# =========================

def update_metadata():
    while True:
        try:
            url = "https://api.bybit.com/v5/market/tickers?category=linear"
            r = requests.get(url, timeout=10)
            data = r.json()

            tickers = data.get("result", {}).get("list", []) or []
            ts = now_ts()

            local = {}
            for t in tickers:
                symbol = t.get("symbol") or ""
                if not symbol:
                    continue

                oi = safe_float(t.get("openInterestValue", 0))
                turnover24h = safe_float(t.get("turnover24h", 0))
                funding = safe_float(t.get("fundingRate", 0)) * 100.0
                volume24h = safe_float(t.get("volume24h", 0))
                price24h_pcnt = safe_float(t.get("price24hPcnt", 0)) * 100.0

                # ✅ NEW: next funding time (ms timestamp usually)
                next_funding_ms = safe_float(t.get("nextFundingTime", 0))

                local[symbol] = {
                    "oi": oi,
                    "turnover24h": turnover24h,
                    "funding": funding,
                    "volume24h": volume24h,
                    "price24hPcnt": price24h_pcnt,
                    "nextFundingTimeMs": next_funding_ms,
                    "last_update": ts
                }

            with lock:
                symbol_metadata.update(local)

            print(f"✅ Updated metadata for {len(local)} symbols", flush=True)

        except Exception as e:
            print(f"Metadata error: {e}", flush=True)

        time.sleep(30)

threading.Thread(target=update_metadata, daemon=True).start()

# =========================
# Symbols selection
# =========================

def get_top_symbols():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        data = r.json()
        tickers = data.get("result", {}).get("list", []) or []

        valid = []
        for t in tickers:
            symbol = t.get("symbol") or ""
            if not symbol:
                continue

            turnover = safe_float(t.get("turnover24h", 0))
            oi = safe_float(t.get("openInterestValue", 0))

            if turnover <= 0:
                continue
            if oi > MAX_OI:
                continue

            score = turnover + (oi * 0.01)
            valid.append((symbol, score))

        valid.sort(key=lambda x: x[1], reverse=True)
        symbols = [s for s, _ in valid[:TOP_N_SYMBOLS]]

        print(f"📡 Tracking {len(symbols)} symbols (Top{TOP_N_SYMBOLS})", flush=True)
        return symbols

    except Exception as e:
        print(f"Error getting symbols: {e}", flush=True)
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

# =========================
# OI gate (soft)
# =========================

def pass_oi_gate(symbol: str, fast_score: int, change_24h: float) -> bool:
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
# Alerts builders
# =========================

def send_fast_alert(symbol, price, change_1m, change_3m, change_5m, signals, score):
    with lock:
        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)
        funding = meta.get("funding", 0)

    is_long = (change_1m > 0 and change_3m > 0)
    direction = "🟢 LONG" if is_long else "🔴 SHORT"

    msg = (
        f"{direction} <b>{symbol}</b>\n"
        f"{strength_from_score(score)}\n\n"
        f"<b>סיגנלים (מהיר):</b>\n"
        + "\n".join(f"- {s}" for s in signals)
        + "\n\n"
        f"⏱️ 1m: {round(change_1m,1)}% | 3m: {round(change_3m,1)}% | 5m: {round(change_5m,1)}%\n"
        f"💵 מחיר: ${price}\n"
        f"💹 Funding: {round(funding,4)}% ({'Short pays Long' if funding < 0 else 'Long pays Short'})\n"
        f"📊 OI: ${round(oi/1_000_000,3)}M"
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
        signals.append(f"⏳ 15m: {round(change_15m,1)}%")
    if abs(change_1h) >= MOVE_1H:
        signals.append(f"🕐 1h: {round(change_1h,1)}%")
    if abs(change_24h) >= MOVE_24H:
        signals.append(f"📅 24h: {round(change_24h,1)}%")

    msg = (
        f"{direction} <b>{symbol}</b>\n"
        f"{strength}\n\n"
        f"<b>סיגנלים (איטי):</b>\n"
        + ("\n".join(f"- {s}" for s in signals) if signals else "- תנועה משמעותית")
        + "\n\n"
        f"💵 מחיר: ${price}\n"
        f"💹 Funding: {round(funding,4)}% ({'Short pays Long' if funding < 0 else 'Long pays Short'})\n"
        f"📊 OI: ${round(oi/1_000_000,3)}M"
    )
    send_telegram(msg)

def send_funding_timing_alert(symbol: str, funding: float, price: float, minutes_left: int, kind: str):
    # kind: "soon" / "imminent"
    title = "⏳ <b>Funding Soon</b>" if kind == "soon" else "🚨 <b>Funding IMMINENT</b>"
    extra = "" if kind == "soon" else "\n⚠️ Funding קיצוני + תשלום קרוב = תנודתיות/סליפג׳ אפשריים"
    msg = (
        f"{title} <b>{symbol}</b>\n\n"
        f"💹 Funding: <b>{round(funding,4)}%</b> ({'Short pays Long' if funding < 0 else 'Long pays Short'})\n"
        f"🕒 לתשלום בעוד: <b>{minutes_left} דקות</b>\n"
        f"💵 מחיר: ${price}"
        f"{extra}"
    )
    send_telegram(msg)

def send_funding_extreme_alert(symbol: str, funding: float, price: float):
    direction = "🟢 Bias LONG" if funding < 0 else "🔴 Bias SHORT"
    msg = (
        f"{direction} <b>{symbol}</b>\n"
        f"⚠️ <b>Funding קיצוני</b>\n\n"
        f"💹 Funding: <b>{round(funding,4)}%</b>\n"
        f"🧾 מצב: {'Short pays Long' if funding < 0 else 'Long pays Short'}\n"
        f"💵 מחיר: ${price}\n\n"
        f"🔎 Funding קיצוני = שוק לא מאוזן (סיכון)."
    )
    send_telegram(msg)

# =========================
# Core signal checker
# =========================

def check_signals(symbol: str, price: float):
    ts = now_ts()

    # --- take snapshots under lock (no network while locked) ---
    with lock:
        prices_list = list(price_history[symbol])
        meta = symbol_metadata.get(symbol, {}).copy()
        a = anchors.get(symbol, {}).copy()

    if len(prices_list) < 5:
        return

    funding = meta.get("funding", 0.0)
    change_24h = meta.get("price24hPcnt", 0.0)
    turnover24h = meta.get("turnover24h", 0.0)
    next_ms = meta.get("nextFundingTimeMs", 0.0)

    # FAST changes
    change_1m = calc_change(prices_list, 60)
    change_3m = calc_change(prices_list, 180)
    change_5m = calc_change(prices_list, 300)

    signals = []
    score = 0

    # FAST LONG
    if change_1m >= MOVE_EXPLOSIVE_1M:
        signals.append(f"💣💣💣 +{round(change_1m, 1)}% בדקה (PUMP)")
        score += 8
    elif change_1m >= MOVE_1M:
        signals.append(f"🚀 +{round(change_1m, 1)}% בדקה")
        score += 4

    if change_3m >= MOVE_3M:
        signals.append(f"📈 +{round(change_3m, 1)}% ב-3 דקות")
        score += 3

    if change_5m >= MOVE_5M:
        signals.append(f"⚡ +{round(change_5m, 1)}% ב-5 דקות")
        score += 2

    # FAST SHORT
    if change_1m <= -MOVE_EXPLOSIVE_1M:
        signals.append(f"💣💣💣 {round(change_1m, 1)}% בדקה (DUMP)")
        score += 8
    elif change_1m <= -MOVE_1M:
        signals.append(f"💥 {round(change_1m, 1)}% בדקה")
        score += 4

    if change_3m <= -MOVE_3M:
        signals.append(f"📉 {round(change_3m, 1)}% ב-3 דקות")
        score += 3

    if change_5m <= -MOVE_5M:
        signals.append(f"⚡ {round(change_5m, 1)}% ב-5 דקות")
        score += 2

    # Acceleration
    if abs(change_1m) > abs(change_3m) * 0.7 and abs(change_1m) >= 2.0:
        signals.append("⚡ התנעה מואצת")
        score += 2

    # Funding boost (only if already has movement signal)
    if abs(funding) >= FUNDING_ABS_FOR_BOOST and score >= 4:
        signals.append(f"💹 Funding חריג: {round(funding, 4)}%")
        score += 2

    # Update anchors (stored) – do it outside meta snapshot
    update_anchors(symbol, ts, price)
    with lock:
        a2 = anchors.get(symbol, {}).copy()

    t15 = a2.get("t15")
    t60 = a2.get("t60")
    change_15m = pct_change(t15[1], price) if t15 else 0.0
    change_1h = pct_change(t60[1], price) if t60 else 0.0

    # OI gate
    if not pass_oi_gate(symbol, score, change_24h):
        return

    # -------- Anti-stuck dynamic turnover filter --------
    is_explosive = (abs(change_1m) >= MOVE_EXPLOSIVE_1M)
    min_turnover = MIN_TURNOVER_24H_EXPLOSIVE if is_explosive else MIN_TURNOVER_24H_NORMAL
    if turnover24h < min_turnover:
        return

    # ===== Funding timing alerts (Soon + Imminent) =====
    if next_ms and next_ms > 0:
        secs_to_funding = (next_ms / 1000.0) - ts

        # Soon (30m)
        if 0 < secs_to_funding <= FUNDING_SOON_WINDOW_SEC:
            key = symbol + "_soon"
            with lock:
                last = last_alert_funding.get(key)
            if (last is None) or (ts - last >= ALERT_COOLDOWN_FUNDING_SEC):
                minutes_left = int(secs_to_funding // 60)
                send_funding_timing_alert(symbol, funding, price, minutes_left, "soon")
                with lock:
                    last_alert_funding[key] = ts

        # Imminent (7m) only if extreme funding
        if (
            0 < secs_to_funding <= FUNDING_IMMINENT_WINDOW_SEC and
            (funding <= FUNDING_EXTREME_NEG or funding >= FUNDING_EXTREME_POS)
        ):
            key = symbol + "_imminent"
            with lock:
                last = last_alert_funding.get(key)
            if (last is None) or (ts - last >= ALERT_COOLDOWN_FUNDING_SEC):
                minutes_left = int(secs_to_funding // 60)
                send_funding_timing_alert(symbol, funding, price, minutes_left, "imminent")
                with lock:
                    last_alert_funding[key] = ts

    # ===== Funding extreme alert (kept as-is) =====
    if funding <= FUNDING_EXTREME_NEG or funding >= FUNDING_EXTREME_POS:
        key = symbol + "_extreme"
        with lock:
            last = last_alert_funding.get(key)
        if (last is None) or (ts - last >= ALERT_COOLDOWN_FUNDING_SEC):
            send_funding_extreme_alert(symbol, funding, price)
            with lock:
                last_alert_funding[key] = ts

    # ===== Advanced anti-stuck filters (only when score >= 4) =====
    if score >= 4:
        # orderbook
        obc = get_orderbook_cached(symbol)
        if obc.get("ok") and obc.get("payload"):
            spread_pct = obc.get("spread_pct", 0.0)
            payload = obc["payload"]

            max_spread = MAX_SPREAD_EXPLOSIVE_PCT if is_explosive else MAX_SPREAD_NORMAL_PCT
            band = DEPTH_BAND_EXPLOSIVE_PCT if is_explosive else DEPTH_BAND_NORMAL_PCT
            min_depth = MIN_DEPTH_EXPLOSIVE_USDT if is_explosive else MIN_DEPTH_NORMAL_USDT

            if spread_pct > max_spread:
                return

            depth_usdt = calc_depth_within_band(payload, band)
            if depth_usdt < min_depth:
                return

        # last 1m turnover
        if ENABLE_1M_VOLUME_FILTER:
            t1m = fetch_1m_turnover_usdt(symbol)
            if t1m < MIN_1M_TURNOVER_USDT:
                return

    # ===== FAST alert =====
    if score >= 4:
        is_long = (change_1m > 0 and change_3m > 0)
        is_short = (change_1m < 0 and change_3m < 0)

        if is_long or is_short:
            with lock:
                last = last_alert_fast.get(symbol)
            if (last is None) or (ts - last >= ALERT_COOLDOWN_FAST_SEC):
                send_fast_alert(symbol, price, change_1m, change_3m, change_5m, signals, score)
                with lock:
                    last_alert_fast[symbol] = ts
                print(f"🎯 FAST ALERT {symbol} score={score}", flush=True)

    # ===== SLOW alert =====
    slow_trigger = (
        abs(change_15m) >= MOVE_15M or
        abs(change_1h) >= MOVE_1H or
        abs(change_24h) >= MOVE_24H
    )
    if slow_trigger:
        with lock:
            last = last_alert_slow.get(symbol)
        if (last is None) or (ts - last >= ALERT_COOLDOWN_SLOW_SEC):
            send_slow_alert(symbol, price, change_15m, change_1h, change_24h)
            with lock:
                last_alert_slow[symbol] = ts
            print(f"📣 SLOW ALERT {symbol} 15m={change_15m:.1f}% 1h={change_1h:.1f}% 24h={change_24h:.1f}%", flush=True)

# =========================
# WebSocket subscribe/resubscribe
# =========================

def subscribe_symbols(ws, symbols):
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        topics = [f"tickers.{s}" for s in batch]
        ws.send(json.dumps({"op": "subscribe", "args": topics}))
        time.sleep(0.25)

def resubscribe_loop():
    global current_symbols, ws_app

    while True:
        try:
            new_symbols = get_top_symbols()

            with lock:
                existing = set(current_symbols)
            to_add = [s for s in new_symbols if s not in existing]

            if to_add:
                with ws_lock:
                    ws = ws_app
                if ws:
                    print(f"🔁 Resubscribe adding {len(to_add)} symbols", flush=True)
                    subscribe_symbols(ws, to_add)
                    with lock:
                        current_symbols.extend(to_add)

        except Exception as e:
            print(f"Resubscribe loop error: {e}", flush=True)

        time.sleep(RESUBSCRIBE_EVERY_SEC)

# =========================
# WebSocket handlers
# =========================

def on_message(ws, message: str):
    try:
        data = json.loads(message)
        topic = data.get("topic", "")
        if not topic.startswith("tickers."):
            return

        ticker_data = data.get("data") or {}
        symbol = ticker_data.get("symbol") or ""
        last_price = safe_float(ticker_data.get("lastPrice", 0))

        if not symbol or last_price <= 0:
            return

        ts = now_ts()
        with lock:
            price_history[symbol].append((ts, last_price))

        check_signals(symbol, last_price)

    except Exception as e:
        print(f"Message error: {e}", flush=True)

def on_error(ws, error):
    print(f"WebSocket error: {error}", flush=True)

def on_close(ws, close_status_code, close_msg):
    print(f"WebSocket closed ({close_status_code}) {close_msg}", flush=True)

def on_open(ws):
    global current_symbols
    print("WebSocket connected!", flush=True)

    symbols = get_top_symbols()
    with lock:
        current_symbols = list(symbols)

    subscribe_symbols(ws, symbols)
    print(f"✅ Subscribed initial: {len(symbols)} symbols", flush=True)

def start_websocket():
    global ws_app

    ws_url = "wss://stream.bybit.com/v5/public/linear"
    ws = websocket.WebSocketApp(
        ws_url,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )

    with ws_lock:
        ws_app = ws

    ws.run_forever(ping_interval=20, ping_timeout=10)

# =========================
# Main
# =========================

if __name__ == "__main__":
    print("🚀 Starting scanner (FAST + SLOW + FUNDING + Anti-Stuck)…", flush=True)
    print("⏳ Waiting for initial metadata...", flush=True)
    time.sleep(3)

    threading.Thread(target=resubscribe_loop, daemon=True).start()

    while True:
        try:
            start_websocket()
        except Exception as e:
            print(f"WebSocket crashed: {e}", flush=True)

        print("Reconnecting in 10s...", flush=True)
        time.sleep(10)
