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
CHAT_ID = os.environ.get("CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

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

OI_TREND_MIN_PCT = 3.5
VOLUME_SPIKE_MIN = 2.2
PRICE_STABLE_MAX_PCT = 0.9
FUNDING_TREND_MIN = 0.01
ACCUM_HISTORY_MIN = 4

HTTP_TIMEOUT = 10

# Whale detection
WHALE_TRADE_MIN_USDT      = 50_000
WHALE_ALERT_COOLDOWN_SEC  = 5 * 60

# Liquidation cascade
LIQ_WINDOW_SEC            = 60
LIQ_MIN_USDT              = 100_000
LIQ_ALERT_COOLDOWN_SEC    = 5 * 60

# Liquidity zones
LIQ_WALL_MIN_USDT         = 300_000
LIQ_ZONE_APPROACH_PCT     = 0.5
LIQ_ZONE_COOLDOWN_SEC     = 30 * 60

# Compression (big move incoming)
COMPRESSION_MAX_PCT       = 0.4
COMPRESSION_OI_MIN        = 2.0
COMPRESSION_COOLDOWN_SEC  = 2 * 60 * 60

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

last_whale_alert      = {}
liq_events            = defaultdict(list)
last_liq_alert        = {}
last_liq_zone_alert   = {}
last_compression_alert = {}

ws_app = None
ws_lock = threading.Lock()
current_symbols = []

session = requests.Session()
session.headers.update(HEADERS)

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


def http_get_json(url, timeout=HTTP_TIMEOUT):
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()

        if not r.text or not r.text.strip():
            return None

        return r.json()
    except requests.RequestException as e:
        print(f"HTTP error: {e} | {url}", flush=True)
        return None
    except ValueError as e:
        print(f"JSON decode error: {e} | {url}", flush=True)
        return None
    except Exception as e:
        print(f"Unexpected GET error: {e} | {url}", flush=True)
        return None


def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CHAT_ID", flush=True)
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = session.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"Telegram error: {r.status_code} | {r.text}", flush=True)
    except Exception as e:
        print(f"Telegram send error: {e}", flush=True)


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
        return "🔥🔥🔥🔥 Critical!"
    if score >= 7:
        return "🔥🔥🔥 Very Strong"
    return "🔥🔥 Strong"


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
    url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=50"
    data = http_get_json(url, timeout=6)
    if not data:
        return False, 0.0, None

    try:
        ob = data.get("result", {}) or {}
        bids = ob.get("b", []) or []
        asks = ob.get("a", []) or []

        if not bids or not asks:
            return False, 0.0, None

        best_bid = safe_float(bids[0][0])
        best_ask = safe_float(asks[0][0])

        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return False, 0.0, None

        mid = (best_bid + best_ask) / 2.0
        spread_pct = ((best_ask - best_bid) / mid) * 100.0

        return True, spread_pct, {"mid": mid, "bids": bids, "asks": asks}
    except Exception as e:
        print(f"Orderbook parse error for {symbol}: {e}", flush=True)
        return False, 0.0, None


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
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=1&limit=1"
    data = http_get_json(url, timeout=6)
    if not data:
        return 0.0

    try:
        lst = data.get("result", {}).get("list", []) or []
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
        f"<b>{symbol}</b>\n\n"
        f"Before: <b>{round(prev_f, 4)}%</b>\n"
        f"Now: <b>{round(new_f, 4)}%</b>\n"
        f"Change: <b>{arrow} {round(delta, 4)}%</b>\n"
        + (f"💵 Price: ${price}\n" if price and price > 0 else "")
    )
    send_telegram(msg)


def send_fast_alert(symbol, price, change_1m, change_3m, change_5m, signals, score):
    with lock:
        meta = symbol_metadata.get(symbol, {})
        oi = meta.get("oi", 0)
        funding = meta.get("funding", 0)

    is_long = change_1m > 0 and change_3m > 0
    direction = "🟢 LONG" if is_long else "🔴 SHORT"
    funding_state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        f"{direction} <b>{symbol}</b>\n"
        f"{strength_from_score(score)}\n\n"
        "<b>Signals (Fast):</b>\n"
        + "\n".join("- " + s for s in signals)
        + "\n\n"
        + f"⏱️ 1m: {round(change_1m, 1)}% | 3m: {round(change_3m, 1)}% | 5m: {round(change_5m, 1)}%\n"
        + f"💵 Price: ${price}\n"
        + f"📉 Funding: {round(funding, 4)}% ({funding_state})\n"
        + f"📊 OI: ${round(oi / 1_000_000, 3)}M"
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
        strength = "🔥🔥🔥🔥 Extreme Move"
    elif max_move >= 30:
        strength = "🔥🔥🔥 Very Strong"
    else:
        strength = "🔥🔥 Strong"

    signals = []
    if abs(change_15m) >= MOVE_15M:
        signals.append(f"⏳ 15m: {round(change_15m, 1)}%")
    if abs(change_1h) >= MOVE_1H:
        signals.append(f"🕐 1h: {round(change_1h, 1)}%")
    if abs(change_24h) >= MOVE_24H:
        signals.append(f"📅 24h: {round(change_24h, 1)}%")

    funding_state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        f"{direction} <b>{symbol}</b>\n"
        f"{strength}\n\n"
        "<b>Signals (Slow):</b>\n"
        + ("\n".join("- " + s for s in signals) if signals else "- Significant move")
        + "\n\n"
        + f"💵 Price: ${price}\n"
        + f"📉 Funding: {round(funding, 4)}% ({funding_state})\n"
        + f"📊 OI: ${round(oi / 1_000_000, 3)}M"
    )
    send_telegram(msg)


def send_accumulation_alert(symbol, price, direction, oi_trend, funding_trend, vol_spike, funding):
    funding_state = "Short pays Long" if funding < 0 else "Long pays Short"
    dir_txt = "🟢 Early LONG" if direction == "long" else "🔴 Early SHORT"

    signals = [
        f"🔍 OI rising consistently: {round(oi_trend, 2)}% avg",
        "🧘 Price stable - accumulation before move",
    ]

    if vol_spike >= VOLUME_SPIKE_MIN:
        signals.insert(1, f"📦 Volume spike: x{round(vol_spike, 1)} vs normal")

    if abs(funding_trend) >= FUNDING_TREND_MIN:
        signals.insert(1, f"📉 Funding starting to move: {round(funding_trend, 4)}%")

    msg = (
        f"{dir_txt} <b>{symbol}</b>\n"
        "💡 Early detection - before price moves\n\n"
        "<b>Signals:</b>\n"
        + "\n".join("- " + s for s in signals)
        + "\n\n"
        + f"💵 Price: ${price}\n"
        + f"📉 Funding: {round(funding, 4)}% ({funding_state})"
    )
    send_telegram(msg)


def send_funding_extreme_alert(symbol, funding, price):
    direction = "🟢 Bias LONG" if funding < 0 else "🔴 Bias SHORT"
    state = "Short pays Long" if funding < 0 else "Long pays Short"

    msg = (
        f"{direction} <b>{symbol}</b>\n"
        "⚠️ <b>Extreme Funding</b>\n\n"
        f"📉 Funding: <b>{round(funding, 4)}%</b>\n"
        f"🧾 State: {state}\n"
        f"💵 Price: ${price}\n"
    )
    send_telegram(msg)


def send_whale_alert(symbol, side, size_usdt, price):
    direction = "🟢 BUY" if side == "Buy" else "🔴 SELL"
    msg = (
        f"🐋 <b>Whale Trade</b> — {direction}\n"
        f"<b>{symbol}</b>\n\n"
        f"💰 Size: <b>${round(size_usdt / 1_000, 1)}K</b>\n"
        f"💵 Price: ${price}\n"
    )
    send_telegram(msg)


def send_liquidation_alert(symbol, side, total_usdt, price):
    direction = "🔴 Longs liquidated" if side == "Sell" else "🟢 Shorts liquidated"
    msg = (
        f"💥 <b>Liquidation Cascade</b>\n"
        f"<b>{symbol}</b>\n\n"
        f"{direction}\n"
        f"💰 Total: <b>${round(total_usdt / 1_000, 1)}K</b> in 1 min\n"
        f"💵 Price: ${price}\n"
    )
    send_telegram(msg)


def send_liquidity_zone_alert(symbol, side, wall_price, wall_usdt, price):
    zone_type = "🟢 Support" if side == "bid" else "🔴 Resistance"
    msg = (
        f"🧲 <b>Liquidity Zone</b> — {zone_type}\n"
        f"<b>{symbol}</b>\n\n"
        f"📍 Zone: <b>${wall_price}</b>\n"
        f"💰 Liquidity: <b>${round(wall_usdt / 1_000_000, 2)}M</b>\n"
        f"💵 Current Price: ${price}\n"
    )
    send_telegram(msg)


def send_compression_alert(symbol, price, range_pct, oi_trend):
    msg = (
        f"🌀 <b>Compression — Big Move Coming</b>\n"
        f"<b>{symbol}</b>\n\n"
        f"📉 Price range (15m): <b>{round(range_pct, 2)}%</b> (very tight)\n"
        f"📊 OI growing: <b>+{round(oi_trend, 2)}%</b>\n"
        f"💵 Price: ${price}\n"
        f"⚡ Explosive move expected soon"
    )
    send_telegram(msg)


# =========================
# Whale / Liquidation / Zones / Compression
# =========================

def check_whale_trades(symbol, price):
    ts = now_ts()
    if ts - last_whale_alert.get(symbol, 0) < WHALE_ALERT_COOLDOWN_SEC:
        return
    try:
        url = f"https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={symbol}&limit=50"
        data = http_get_json(url, timeout=6)
        if not data:
            return
        trades = data.get("result", {}).get("list", []) or []
        for trade in trades:
            size      = safe_float(trade.get("size", 0))
            t_price   = safe_float(trade.get("price", 0))
            size_usdt = size * t_price
            if size_usdt >= WHALE_TRADE_MIN_USDT:
                side = trade.get("side", "")
                send_whale_alert(symbol, side, size_usdt, price)
                last_whale_alert[symbol] = ts
                print(f"WHALE: {symbol} {side} ${round(size_usdt/1000,1)}K", flush=True)
                return
    except Exception as e:
        print(f"Whale check error {symbol}: {e}", flush=True)


def process_liquidation(symbol, side, size_usdt, price):
    ts = now_ts()
    liq_events[symbol].append((ts, size_usdt, side))
    liq_events[symbol] = [(t, v, s) for t, v, s in liq_events[symbol] if ts - t <= LIQ_WINDOW_SEC]

    if ts - last_liq_alert.get(symbol, 0) < LIQ_ALERT_COOLDOWN_SEC:
        return

    long_liq  = sum(v for t, v, s in liq_events[symbol] if s == "Sell")
    short_liq = sum(v for t, v, s in liq_events[symbol] if s == "Buy")

    if long_liq >= LIQ_MIN_USDT:
        send_liquidation_alert(symbol, "Sell", long_liq, price)
        last_liq_alert[symbol] = ts
        liq_events[symbol].clear()
    elif short_liq >= LIQ_MIN_USDT:
        send_liquidation_alert(symbol, "Buy", short_liq, price)
        last_liq_alert[symbol] = ts
        liq_events[symbol].clear()


def check_liquidity_zones(symbol, price):
    ts = now_ts()
    if ts - last_liq_zone_alert.get(symbol, 0) < LIQ_ZONE_COOLDOWN_SEC:
        return

    obc = get_orderbook_cached(symbol)
    if not obc.get("ok") or not obc.get("payload"):
        return

    payload = obc["payload"]

    best_bid_wall, best_bid_price = 0.0, 0.0
    for px_str, qty_str in payload["bids"]:
        px, qty = safe_float(px_str), safe_float(qty_str)
        wall_usdt = px * qty
        if wall_usdt > best_bid_wall and px >= price * 0.98:
            best_bid_wall, best_bid_price = wall_usdt, px

    best_ask_wall, best_ask_price = 0.0, 0.0
    for px_str, qty_str in payload["asks"]:
        px, qty = safe_float(px_str), safe_float(qty_str)
        wall_usdt = px * qty
        if wall_usdt > best_ask_wall and px <= price * 1.02:
            best_ask_wall, best_ask_price = wall_usdt, px

    if best_bid_wall >= LIQ_WALL_MIN_USDT:
        dist = abs(price - best_bid_price) / price * 100.0
        if dist <= LIQ_ZONE_APPROACH_PCT:
            send_liquidity_zone_alert(symbol, "bid", round(best_bid_price, 8), best_bid_wall, price)
            last_liq_zone_alert[symbol] = ts
            return

    if best_ask_wall >= LIQ_WALL_MIN_USDT:
        dist = abs(best_ask_price - price) / price * 100.0
        if dist <= LIQ_ZONE_APPROACH_PCT:
            send_liquidity_zone_alert(symbol, "ask", round(best_ask_price, 8), best_ask_wall, price)
            last_liq_zone_alert[symbol] = ts


def check_compression(symbol, price):
    ts = now_ts()
    if ts - last_compression_alert.get(symbol, 0) < COMPRESSION_COOLDOWN_SEC:
        return

    with lock:
        prices_list = list(price_history[symbol])
        oi_hist     = list(oi_history[symbol])

    if len(prices_list) < 20 or len(oi_hist) < ACCUM_HISTORY_MIN:
        return

    cutoff = ts - 15 * 60
    recent = [p for t, p in prices_list if t >= cutoff]
    if len(recent) < 5:
        return

    high, low = max(recent), min(recent)
    if low <= 0:
        return
    range_pct = (high - low) / low * 100.0

    if range_pct > COMPRESSION_MAX_PCT:
        return

    oi_trend = calc_trend(oi_hist)
    if oi_trend < COMPRESSION_OI_MIN:
        return

    send_compression_alert(symbol, price, range_pct, oi_trend)
    last_compression_alert[symbol] = ts
    print(f"COMPRESSION: {symbol} range={round(range_pct,2)}% OI={round(oi_trend,2)}%", flush=True)


# =========================
# Metadata updater
# =========================

def update_metadata():
    while True:
        try:
            data = http_get_json(
                "https://api.bybit.com/v5/market/tickers?category=linear",
                timeout=10,
            )

            if not data:
                print("Metadata fetch returned empty/invalid response", flush=True)
                time.sleep(30)
                continue

            tickers = data.get("result", {}).get("list", [])
            ts = now_ts()
            local = {}

            for t in tickers:
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

                    if abs(delta) >= FUNDING_SPIKE_MIN_DELTA and (ts - last_spike >= FUNDING_SPIKE_ALERT_COOLDOWN_SEC):
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

            print(f"Updated metadata for {len(local)} symbols", flush=True)

        except Exception as e:
            print(f"Metadata loop error: {e}", flush=True)

        time.sleep(30)


# =========================
# Symbols
# =========================

def get_top_symbols():
    data = http_get_json(
        "https://api.bybit.com/v5/market/tickers?category=linear",
        timeout=10,
    )
    if not data:
        print("get_top_symbols fallback to defaults", flush=True)
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    try:
        tickers = data.get("result", {}).get("list", []) or []
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
        print(f"Tracking {len(symbols)} symbols", flush=True)
        return symbols

    except Exception as e:
        print(f"Error getting symbols: {e}", flush=True)
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

    if funding <= -0.05 or funding_trend < 0:
        direction = "long"
    elif funding >= 0.05 or funding_trend > 0:
        direction = "short"
    else:
        return

    send_accumulation_alert(symbol, price, direction, oi_trend, funding_trend, vol_spike, funding)

    with lock:
        last_accum_alert[symbol] = ts

    print(f"ACCUM ALERT: {symbol} {direction}", flush=True)


# =========================
# Core signal checker
# =========================

def check_signals(symbol, price):
    ts = now_ts()

    with lock:
        last_fast = last_fast_alert.get(symbol)
        last_slow = last_slow_alert.get(symbol)

    fast_blocked = (last_fast is not None) and (ts - last_fast < FAST_ANTI_SPAM_SEC)
    slow_blocked = (last_slow is not None) and (ts - last_slow < SLOW_ANTI_SPAM_SEC)

    with lock:
        prices_list = list(price_history[symbol])
        meta = symbol_metadata.get(symbol, {}).copy()

    if len(prices_list) < 5:
        return

    funding = meta.get("funding", 0.0)
    change_24h = meta.get("price24hPcnt", 0.0)
    turnover24h = meta.get("turnover24h", 0.0)

    change_1m = calc_change(prices_list, 60)
    change_3m = calc_change(prices_list, 180)
    change_5m = calc_change(prices_list, 300)

    signals = []
    score = 0

    if change_1m >= MOVE_EXPLOSIVE_1M:
        signals.append(f"💣💣💣 +{round(change_1m, 1)}% in 1m (PUMP)")
        score += 8
    elif change_1m >= MOVE_1M:
        signals.append(f"🚀 +{round(change_1m, 1)}% in 1m")
        score += 4

    if change_3m >= MOVE_3M:
        signals.append(f"📈 +{round(change_3m, 1)}% in 3m")
        score += 3
    if change_5m >= MOVE_5M:
        signals.append(f"⚡ +{round(change_5m, 1)}% in 5m")
        score += 2

    if change_1m <= -MOVE_EXPLOSIVE_1M:
        signals.append(f"💣💣💣 {round(change_1m, 1)}% in 1m (DUMP)")
        score += 8
    elif change_1m <= -MOVE_1M:
        signals.append(f"💥 {round(change_1m, 1)}% in 1m")
        score += 4

    if change_3m <= -MOVE_3M:
        signals.append(f"📉 {round(change_3m, 1)}% in 3m")
        score += 3
    if change_5m <= -MOVE_5M:
        signals.append(f"⚡ {round(change_5m, 1)}% in 5m")
        score += 2

    if abs(change_1m) > abs(change_3m) * 0.7 and abs(change_1m) >= 2.0:
        signals.append("⚡ Accelerating momentum")
        score += 2

    if abs(funding) >= FUNDING_ABS_FOR_BOOST and score >= 4:
        signals.append(f"📉 Extreme Funding: {round(funding, 4)}%")
        score += 2

    update_anchors(symbol, ts, price)

    with lock:
        a2 = anchors.get(symbol, {}).copy()

    t15 = a2.get("t15")
    t60 = a2.get("t60")
    change_15m = pct_change(t15[1], price) if t15 else 0.0
    change_1h = pct_change(t60[1], price) if t60 else 0.0

    if not pass_oi_gate(symbol, score, change_24h):
        return

    is_explosive = abs(change_1m) >= MOVE_EXPLOSIVE_1M
    min_turnover = MIN_TURNOVER_24H_EXPLOSIVE if is_explosive else MIN_TURNOVER_24H_NORMAL
    if turnover24h < min_turnover:
        return

    if score >= 4:
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

        if ENABLE_1M_VOLUME_FILTER:
            t1m = fetch_1m_turnover_usdt(symbol)
            if t1m < MIN_1M_TURNOVER_USDT:
                return

    if score >= 4 and not fast_blocked:
        is_long = change_1m > 0 and change_3m > 0
        is_short = change_1m < 0 and change_3m < 0

        if is_long or is_short:
            check_whale_trades(symbol, price)
            send_fast_alert(symbol, price, change_1m, change_3m, change_5m, signals, score)
            with lock:
                last_fast_alert[symbol] = ts
            return

    slow_trigger = (
        abs(change_15m) >= MOVE_15M
        or abs(change_1h) >= MOVE_1H
        or abs(change_24h) >= MOVE_24H
    )

    if slow_trigger and not slow_blocked:
        check_whale_trades(symbol, price)
        send_slow_alert(symbol, price, change_15m, change_1h, change_24h)
        with lock:
            last_slow_alert[symbol] = ts

    check_liquidity_zones(symbol, price)


# =========================
# Funding extreme
# =========================

def maybe_send_funding_alert(symbol, funding, price):
    if not (funding <= FUNDING_EXTREME_NEG or funding >= FUNDING_EXTREME_POS):
        return

    ts = now_ts()
    key = symbol + "_extreme"

    with lock:
        last = last_alert_funding.get(key)

    if (last is None) or (ts - last >= FUNDING_ALERT_COOLDOWN_SEC):
        send_funding_extreme_alert(symbol, funding, price)
        with lock:
            last_alert_funding[key] = ts


# =========================
# WebSocket
# =========================

def subscribe_symbols(ws, symbols):
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        try:
            ws.send(json.dumps({"op": "subscribe", "args": ["tickers." + s for s in batch]}))
        except Exception as e:
            print(f"Subscribe tickers error: {e}", flush=True)
        time.sleep(0.15)
        try:
            ws.send(json.dumps({"op": "subscribe", "args": ["liquidation." + s for s in batch]}))
        except Exception as e:
            print(f"Subscribe liquidation error: {e}", flush=True)
        time.sleep(0.15)


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
                    subscribe_symbols(ws, to_add)
                    with lock:
                        current_symbols.extend(to_add)
                    print(f"Resubscribed {len(to_add)} new symbols", flush=True)

        except Exception as e:
            print(f"Resubscribe error: {e}", flush=True)

        time.sleep(RESUBSCRIBE_EVERY_SEC)


def on_message(ws, message):
    try:
        data = json.loads(message)
        topic = data.get("topic", "")

        # --- Liquidation stream ---
        if topic.startswith("liquidation."):
            liq_data  = data.get("data") or {}
            symbol    = liq_data.get("symbol") or ""
            side      = liq_data.get("side", "")
            size      = safe_float(liq_data.get("size", 0))
            liq_price = safe_float(liq_data.get("price", 0))
            if symbol and size > 0 and liq_price > 0:
                with lock:
                    price = last_price_cache.get(symbol, liq_price)
                process_liquidation(symbol, side, size * liq_price, price)
            return

        if not topic.startswith("tickers."):
            return

        # --- Ticker stream ---
        ticker_data = data.get("data") or {}
        symbol      = ticker_data.get("symbol") or ""
        last_price  = safe_float(ticker_data.get("lastPrice", 0))
        print(symbol, last_price, flush=True)

        if not symbol or last_price <= 0:
            return

        ts = now_ts()
        with lock:
            price_history[symbol].append((ts, last_price))
            last_price_cache[symbol] = last_price
            funding = (symbol_metadata.get(symbol, {}) or {}).get("funding", 0.0)

        maybe_send_funding_alert(symbol, funding, last_price)
        check_signals(symbol, last_price)
        check_accumulation(symbol, last_price)
        check_compression(symbol, last_price)

    except Exception as e:
        print(f"Message error: {e}", flush=True)


def on_error(ws, error):
    print(f"WebSocket error: {error}", flush=True)


def on_close(ws, close_status_code, close_msg):
    print(f"WebSocket closed | code={close_status_code} | msg={close_msg}", flush=True)


def on_open(ws):
    global current_symbols
    print("WebSocket connected!", flush=True)

    symbols = get_top_symbols()
    with lock:
        current_symbols = list(symbols)

    subscribe_symbols(ws, symbols)
    print(f"Subscribed: {len(symbols)} symbols", flush=True)


def start_websocket():
    global ws_app

    ws_url = "wss://stream.bybit.com/v5/public/linear"
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    with ws_lock:
        ws_app = ws

    ws.run_forever(
        ping_interval=20,
        ping_timeout=10,
        reconnect=5,
    )


# =========================
# Main
# =========================

if __name__ == "__main__":
    print("MOHAMED NEW VERSION LIVE", flush=True)

    threading.Thread(target=update_metadata, daemon=True).start()
    threading.Thread(target=resubscribe_loop, daemon=True).start()

    while True:
        try:
            start_websocket()
        except Exception as e:
            print(f"WebSocket crashed: {e}", flush=True)
            print("Reconnecting in 10s...", flush=True)
            time.sleep(10)

