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

TELEGRAM_TOKEN = os.environ.get(“TELEGRAM_TOKEN”)
CHAT_ID        = os.environ.get(“CHAT_ID”)

HEADERS = {
“User-Agent”: (
“Mozilla/5.0 (Windows NT 10.0; Win64; x64) “
“AppleWebKit/537.36 (KHTML, like Gecko) “
“Chrome/120.0.0.0 Safari/537.36”
)
}

# cooldowns

FAST_ANTI_SPAM_SEC               = 30 * 60
SLOW_ANTI_SPAM_SEC               = 60 * 60
FUNDING_ALERT_COOLDOWN_SEC       = 60 * 60
ACCUMULATION_ALERT_COOLDOWN_SEC  = 2 * 60 * 60
FUNDING_SPIKE_ALERT_COOLDOWN_SEC = 60 * 60
LIQ_ZONE_ALERT_COOLDOWN_SEC      = 45 * 60
WHALE_ALERT_COOLDOWN_SEC         = 60 * 60

# move thresholds

FUNDING_SPIKE_MIN_DELTA = 0.50
MOVE_EXPLOSIVE_1M = 10.0
MOVE_1M  = 2.5
MOVE_3M  = 4.0
MOVE_5M  = 5.0
MOVE_15M = 8.0
MOVE_1H  = 15.0
MOVE_24H = 30.0
FUNDING_EXTREME_POS   = 0.5
FUNDING_EXTREME_NEG   = -0.5
FUNDING_ABS_FOR_BOOST = 0.5

# symbol filters

TOP_N_SYMBOLS               = 500
RESUBSCRIBE_EVERY_SEC       = 10 * 60
MIN_OI_SOFT                 = 50_000
MAX_OI                      = 800_000_000
ALLOW_LOW_OI_IF_STRONG_MOVE = True
LOW_OI_FAST_SCORE_BYPASS    = 7
MIN_TURNOVER_24H_NORMAL     = 400_000
MIN_TURNOVER_24H_EXPLOSIVE  = 150_000

# order book

ORDERBOOK_TTL_SEC        = 25
MAX_SPREAD_NORMAL_PCT    = 0.45
MAX_SPREAD_EXPLOSIVE_PCT = 0.80
DEPTH_BAND_NORMAL_PCT    = 0.50
DEPTH_BAND_EXPLOSIVE_PCT = 0.80
MIN_DEPTH_NORMAL_USDT    = 20_000
MIN_DEPTH_EXPLOSIVE_USDT = 8_000

# volume filter

ENABLE_1M_VOLUME_FILTER = True
MIN_1M_TURNOVER_USDT    = 800

# accumulation

OI_TREND_MIN_PCT    = 3.5
VOLUME_SPIKE_MIN    = 2.2
PRICE_STABLE_MAX_PCT = 0.9
FUNDING_TREND_MIN   = 0.01
ACCUM_HISTORY_MIN   = 4

HTTP_TIMEOUT = 10

# ── Liquidation zones ──────────────────────────────

LIQ_ZONE_PROXIMITY_PCT = 1.2
LIQ_ZONE_WARN_PCT      = 2.5
LIQ_KLINE_LIMIT        = 96
LIQ_MIN_TOUCHES        = 2
LIQ_CLUSTER_BAND_PCT   = 0.3

# ── Volume Profile ─────────────────────────────────

VP_KLINE_LIMIT      = 96       # 96x15m = 24h
VP_BINS             = 40       # عدد مستويات السعر
VP_POC_ZONE_PCT     = 0.5      # % حول POC تعتبر منطقة سيولة
VP_HVN_TOP_PCT      = 20       # أعلى 20% حجم = HVN

# ── Psychological levels ───────────────────────────

PSYCH_PROXIMITY_PCT = 0.8      # % مسافة من الرقم النفسي

# ── Whale detection ────────────────────────────────

WHALE_OI_DROP_PCT        = 3.0   # % انخفاض OI مع سعر يرتفع = توزيع
WHALE_VOL_SPIKE_NO_MOVE  = 3.0   # x حجم تداول بدون حركة = امتصاص
WHALE_PRICE_STABLE_PCT   = 0.6   # % حركة سعر تعتبر “ثابت” للامتصاص
WHALE_FUNDING_JUMP       = 0.15  # % قفزة funding = حوت يبني مركز

# ── CVD ────────────────────────────────────────────

CVD_ALERT_COOLDOWN_SEC   = 45 * 60
CVD_LOOKBACK_SEC         = 15 * 60   # نافذة تحليل CVD (15 دقيقة)
CVD_DIVERGE_PRICE_MIN    = 1.0       # % حركة سعر للكشف عن divergence
CVD_DIVERGE_RATIO        = 0.4       # نسبة CVD/سعر تعتبر divergence
CVD_STRONG_DELTA_X       = 2.5       # x متوسط = CVD قوي جداً

# =========================

# State

# =========================

lock           = threading.Lock()
price_history  = defaultdict(lambda: deque(maxlen=2500))
symbol_metadata = {}
oi_history     = defaultdict(lambda: deque(maxlen=20))
funding_history= defaultdict(lambda: deque(maxlen=20))
volume_history = defaultdict(lambda: deque(maxlen=20))

last_fast_alert          = {}
last_slow_alert          = {}
last_alert_funding       = {}
last_accum_alert         = {}
last_funding_spike_alert = {}
last_liq_zone_alert      = {}
last_whale_alert         = {}
last_cvd_alert           = {}

# CVD state — يتراكم من WebSocket

cvd_history    = defaultdict(lambda: deque(maxlen=500))  # (ts, cumulative_delta)
cvd_running    = defaultdict(float)                       # القيمة التراكمية الحالية

anchors        = {}
orderbook_cache= {}
liq_zone_cache = {}
vp_cache       = {}

last_price_cache  = {}
last_funding_seen = {}
last_oi_seen      = {}
last_vol_seen     = {}

ws_app         = None
ws_lock        = threading.Lock()
current_symbols= []

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
print(f”HTTP error: {e} | {url}”, flush=True)
return None
except ValueError as e:
print(f”JSON decode error: {e} | {url}”, flush=True)
return None
except Exception as e:
print(f”GET error: {e} | {url}”, flush=True)
return None

def send_telegram(msg: str):
if not TELEGRAM_TOKEN or not CHAT_ID:
print(“Missing TELEGRAM_TOKEN or CHAT_ID”, flush=True)
return
try:
url = f”https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage”
r = session.post(
url,
data={“chat_id”: CHAT_ID, “text”: msg, “parse_mode”: “HTML”},
timeout=10,
)
if r.status_code != 200:
print(f”Telegram error: {r.status_code} | {r.text}”, flush=True)
except Exception as e:
print(f”Telegram send error: {e}”, flush=True)

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
return “🔥🔥🔥🔥 קריטי!”
if score >= 7:
return “🔥🔥🔥 חזק מאוד”
return “🔥🔥 חזק”

def update_anchors(symbol, ts, price):
with lock:
a = anchors.get(symbol, {})
t15 = a.get(“t15”)
if (t15 is None) or (ts - t15[0] > 20 * 60):
a[“t15”] = (ts, price)
t60 = a.get(“t60”)
if (t60 is None) or (ts - t60[0] > 75 * 60):
a[“t60”] = (ts, price)
anchors[symbol] = a

def fetch_orderbook_metrics(symbol):
url = f”https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=50”
data = http_get_json(url, timeout=6)
if not data:
return False, 0.0, None
try:
ob = data.get(“result”, {}) or {}
bids = ob.get(“b”, []) or []
asks = ob.get(“a”, []) or []
if not bids or not asks:
return False, 0.0, None
best_bid = safe_float(bids[0][0])
best_ask = safe_float(asks[0][0])
if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
return False, 0.0, None
mid = (best_bid + best_ask) / 2.0
spread_pct = ((best_ask - best_bid) / mid) * 100.0
return True, spread_pct, {“mid”: mid, “bids”: bids, “asks”: asks}
except Exception as e:
print(f”Orderbook parse error {symbol}: {e}”, flush=True)
return False, 0.0, None

def calc_depth_within_band(payload, band_pct):
try:
mid   = payload[“mid”]
lower = mid * (1.0 - band_pct / 100.0)
upper = mid * (1.0 + band_pct / 100.0)
bid_depth = sum(safe_float(px) * safe_float(qty) for px, qty in payload[“bids”] if safe_float(px) >= lower)
ask_depth = sum(safe_float(px) * safe_float(qty) for px, qty in payload[“asks”] if safe_float(px) <= upper)
return bid_depth + ask_depth
except Exception:
return 0.0

def get_orderbook_cached(symbol):
ts = now_ts()
c  = orderbook_cache.get(symbol)
if c and (ts - c[“ts”] <= ORDERBOOK_TTL_SEC):
return c
ok, spread_pct, payload = fetch_orderbook_metrics(symbol)
c = {“ts”: ts, “ok”: ok, “spread_pct”: spread_pct, “payload”: payload}
orderbook_cache[symbol] = c
return c

def fetch_1m_turnover_usdt(symbol):
url  = f”https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=1&limit=1”
data = http_get_json(url, timeout=6)
if not data:
return 0.0
try:
lst = data.get(“result”, {}).get(“list”, []) or []
if lst and len(lst[0]) > 6:
return safe_float(lst[0][6])
return 0.0
except Exception:
return 0.0

def fetch_klines(symbol, interval=“15”, limit=96):
url  = f”https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}”
data = http_get_json(url, timeout=8)
if not data:
return []
try:
return data.get(“result”, {}).get(“list”, []) or []
except Exception:
return []

# =========================

# Volume Profile

# =========================

def build_volume_profile(symbol, current_price):
candles = fetch_klines(symbol, interval=“15”, limit=VP_KLINE_LIMIT)
if len(candles) < 10:
return None

```
highs    = [safe_float(c[2]) for c in candles]
lows     = [safe_float(c[3]) for c in candles]
volumes  = [safe_float(c[5]) for c in candles]

price_min = min(lows)
price_max = max(highs)
if price_max <= price_min:
    return None

bin_size = (price_max - price_min) / VP_BINS
bins     = [0.0] * VP_BINS

for i, candle in enumerate(candles):
    h   = highs[i]
    l   = lows[i]
    vol = volumes[i]
    candle_range = h - l
    if candle_range <= 0:
        continue
    for b in range(VP_BINS):
        bin_low  = price_min + b * bin_size
        bin_high = bin_low + bin_size
        overlap  = max(0, min(h, bin_high) - max(l, bin_low))
        bins[b] += vol * (overlap / candle_range)

if not any(v > 0 for v in bins):
    return None

max_vol    = max(bins)
threshold  = max_vol * (VP_HVN_TOP_PCT / 100)
poc_idx    = bins.index(max_vol)
poc_price  = price_min + (poc_idx + 0.5) * bin_size

hvn_zones = []
for b in range(VP_BINS):
    if bins[b] >= threshold:
        zone_price = price_min + (b + 0.5) * bin_size
        dist_pct   = abs(zone_price - current_price) / current_price * 100
        if dist_pct <= 5.0:
            hvn_zones.append({
                "price":    round(zone_price, 6),
                "volume":   round(bins[b], 2),
                "dist_pct": round(dist_pct, 2),
                "is_poc":   (b == poc_idx)
            })

hvn_zones.sort(key=lambda x: x["dist_pct"])

return {
    "poc":      round(poc_price, 6),
    "hvn":      hvn_zones[:6],
    "ts":       now_ts()
}
```

def get_vp_cached(symbol, current_price):
cached = vp_cache.get(symbol)
if cached and (now_ts() - cached[“ts”] < 20 * 60):
return cached
vp = build_volume_profile(symbol, current_price)
if vp:
vp_cache[symbol] = vp
return vp

# =========================

# Psychological Levels

# =========================

def get_psych_levels(price):
levels = []

```
def round_to(p, step):
    import math
    return round(math.floor(p / step) * step, 10), round(math.ceil(p / step) * step, 10)

# .00 levels
for step in [0.001, 0.01, 0.1, 1, 10, 100, 1000, 10000]:
    if price >= step * 5:
        lo, hi = round_to(price, step)
        for lvl in [lo, hi]:
            dist = abs(lvl - price) / price * 100
            if 0 < dist <= PSYCH_PROXIMITY_PCT:
                levels.append({"price": round(lvl, 8), "dist_pct": round(dist, 3), "type": f"round({step})"})

# .25 .50 .75 sublevels for price > 10
if price > 10:
    base = int(price)
    for frac in [0.25, 0.50, 0.75]:
        lvl  = base + frac
        dist = abs(lvl - price) / price * 100
        if 0 < dist <= PSYCH_PROXIMITY_PCT:
            levels.append({"price": lvl, "dist_pct": round(dist, 3), "type": ".25/.50/.75"})

levels.sort(key=lambda x: x["dist_pct"])
return levels[:3]
```

# =========================

# Liquidation Zones

# =========================

def build_liq_zones(symbol, current_price):
candles = fetch_klines(symbol, interval=“15”, limit=LIQ_KLINE_LIMIT)
if len(candles) < 10:
return {“above”: [], “below”: [], “ts”: now_ts()}

```
highs = [safe_float(c[2]) for c in candles]
lows  = [safe_float(c[3]) for c in candles]

local_highs, local_lows = [], []
for i in range(1, len(highs) - 1):
    if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
        local_highs.append(highs[i])
    if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
        local_lows.append(lows[i])

def cluster_levels(levels, band_pct):
    if not levels:
        return []
    sorted_lvls = sorted(levels)
    clusters, cur = [], [sorted_lvls[0]]
    for lvl in sorted_lvls[1:]:
        center = sum(cur) / len(cur)
        if abs(lvl - center) / center * 100 <= band_pct:
            cur.append(lvl)
        else:
            clusters.append(cur)
            cur = [lvl]
    clusters.append(cur)
    result = []
    for cl in clusters:
        if len(cl) >= LIQ_MIN_TOUCHES:
            avg = sum(cl) / len(cl)
            t   = len(cl)
            strength = "🔴🔴🔴 عالية جداً" if t >= 5 else ("🟠🟠 عالية" if t >= 3 else "🟡 متوسطة")
            result.append({"price": round(avg, 6), "touches": t, "strength": strength})
    return result

above = cluster_levels([h for h in local_highs if h > current_price], LIQ_CLUSTER_BAND_PCT)
below = cluster_levels([l for l in local_lows  if l < current_price], LIQ_CLUSTER_BAND_PCT)
above.sort(key=lambda x: x["price"])
below.sort(key=lambda x: x["price"], reverse=True)
return {"above": above[:5], "below": below[:5], "ts": now_ts()}
```

def get_liq_zones_cached(symbol, current_price):
cached = liq_zone_cache.get(symbol)
if cached and (now_ts() - cached[“ts”] < 15 * 60):
return cached
zones = build_liq_zones(symbol, current_price)
liq_zone_cache[symbol] = zones
return zones

def check_liq_zones(symbol, price):
ts       = now_ts()
last_liq = last_liq_zone_alert.get(symbol, 0)
if ts - last_liq < LIQ_ZONE_ALERT_COOLDOWN_SEC:
return

```
zones = get_liq_zones_cached(symbol, price)
if not zones:
    return

alerts = []
for zone in zones["above"]:
    dist = ((zone["price"] - price) / price) * 100
    if 0 < dist <= LIQ_ZONE_PROXIMITY_PCT:
        alerts.append({"dir": "above", "zone": zone, "dist": round(dist, 2), "level": "🚨 قريب جداً", "emoji": "⬆️🔴"})
    elif 0 < dist <= LIQ_ZONE_WARN_PCT:
        alerts.append({"dir": "above", "zone": zone, "dist": round(dist, 2), "level": "⚠️ تحذير", "emoji": "⬆️🟡"})

for zone in zones["below"]:
    dist = ((price - zone["price"]) / price) * 100
    if 0 < dist <= LIQ_ZONE_PROXIMITY_PCT:
        alerts.append({"dir": "below", "zone": zone, "dist": round(dist, 2), "level": "🚨 قريب جداً", "emoji": "⬇️🟢"})
    elif 0 < dist <= LIQ_ZONE_WARN_PCT:
        alerts.append({"dir": "below", "zone": zone, "dist": round(dist, 2), "level": "⚠️ تحذير", "emoji": "⬇️🟡"})

if not alerts:
    return

lines = [f"💧 <b>Liquidity Zone Alert</b>", f"<b>{symbol}</b> | 💵 ${price}", "━━━━━━━━━━━━━━━━"]
for a in alerts:
    z        = a["zone"]
    hunt_txt = "🎯 Shorts سيُصطادون" if a["dir"] == "above" else "🎯 Longs سيُصطادون"
    lines.append(
        f"{a['emoji']} {a['level']} | ${z['price']} ({a['dist']}%)\n"
        f"   {z['strength']} ({z['touches']} لمسات) | {hunt_txt}"
    )

with lock:
    meta    = symbol_metadata.get(symbol, {})
    oi      = meta.get("oi", 0)
    funding = meta.get("funding", 0.0)

if oi > 0:
    lines.append(f"📊 OI: ${round(oi/1_000_000, 2)}M")
if funding != 0:
    lines.append(f"📉 Funding: {round(funding, 4)}%")

send_telegram("\n".join(lines))
last_liq_zone_alert[symbol] = ts
print(f"LIQ ALERT: {symbol}", flush=True)
```

# =========================

# CVD — Cumulative Volume Delta (جديد)

# =========================

def update_cvd(symbol: str, price: float, prev_price: float, volume: float):
“””
يحسب CVD من تيكر الـ WebSocket.
المنطق: إذا السعر ارتفع عن السابق = شراء = delta موجب
إذا السعر انخفض          = بيع  = delta سالب
“””
if prev_price <= 0 or volume <= 0:
return
if price > prev_price:
delta = volume
elif price < prev_price:
delta = -volume
else:
delta = 0.0

```
with lock:
    cvd_running[symbol] += delta
    cvd_history[symbol].append((now_ts(), cvd_running[symbol]))
```

def check_cvd(symbol: str, price: float):
“””
يكشف ٣ أنماط:
1. Bullish Divergence  — سعر ينخفض + CVD يرتفع = شراء خفي
2. Bearish Divergence  — سعر يرتفع + CVD ينخفض = بيع خفي
3. CVD Spike قوي       — دلتا كبيرة جداً = ضغط اتجاهي مفاجئ
“””
ts       = now_ts()
last_cvd = last_cvd_alert.get(symbol, 0)
if ts - last_cvd < CVD_ALERT_COOLDOWN_SEC:
return

```
with lock:
    cvd_hist    = list(cvd_history[symbol])
    prices_list = list(price_history[symbol])

if len(cvd_hist) < 10 or len(prices_list) < 10:
    return

# ── بيانات النافذة الزمنية ──────────────────
cutoff      = ts - CVD_LOOKBACK_SEC
recent_cvd  = [(t, v) for t, v in cvd_hist    if t >= cutoff]
recent_px   = [(t, p) for t, p in prices_list if t >= cutoff]

if len(recent_cvd) < 6 or len(recent_px) < 6:
    return

cvd_start  = recent_cvd[0][1]
cvd_end    = recent_cvd[-1][1]
px_start   = recent_px[0][1]
px_end     = recent_px[-1][1]

cvd_change = cvd_end - cvd_start          # موجب = شراء صافي، سالب = بيع صافي
px_change  = ((px_end - px_start) / px_start) * 100 if px_start > 0 else 0

pattern    = None
confidence = ""
implication= ""

# ── 1. Bullish Divergence ────────────────────
# سعر نزل + CVD ارتفع = مشترون خفيون يمتصون البيع
if px_change <= -CVD_DIVERGE_PRICE_MIN and cvd_change > 0:
    ratio = abs(cvd_change / (cvd_start if cvd_start != 0 else 1))
    if ratio >= CVD_DIVERGE_RATIO or cvd_change > abs(cvd_start) * 0.3:
        pattern     = "BULLISH_DIV"
        confidence  = "🟢🟢🟢 عالية" if ratio >= CVD_DIVERGE_RATIO * 2 else "🟢🟢 متوسطة"
        implication = "مشترون يمتصون البيع بصمت — ارتداد محتمل ⬆️"

# ── 2. Bearish Divergence ────────────────────
# سعر ارتفع + CVD نزل = بائعون خفيون يبيعون على الارتفاع
elif px_change >= CVD_DIVERGE_PRICE_MIN and cvd_change < 0:
    ratio = abs(cvd_change / (cvd_start if cvd_start != 0 else 1))
    if ratio >= CVD_DIVERGE_RATIO or abs(cvd_change) > abs(cvd_start) * 0.3:
        pattern     = "BEARISH_DIV"
        confidence  = "🔴🔴🔴 عالية" if ratio >= CVD_DIVERGE_RATIO * 2 else "🔴🔴 متوسطة"
        implication = "بائعون يوزعون على الارتفاع — هبوط محتمل ⬇️"

# ── 3. CVD Spike — ضغط اتجاهي مفاجئ ────────
if len(recent_cvd) >= 20:
    deltas     = [abs(recent_cvd[i][1] - recent_cvd[i-1][1]) for i in range(1, len(recent_cvd))]
    avg_delta  = sum(deltas[:-3]) / max(len(deltas) - 3, 1) if len(deltas) > 3 else 0
    last_delta = sum(deltas[-3:]) / 3 if len(deltas) >= 3 else 0

    if avg_delta > 0 and last_delta >= avg_delta * CVD_STRONG_DELTA_X:
        spike_dir = "شراء" if cvd_change > 0 else "بيع"
        if not pattern:
            pattern     = "CVD_SPIKE"
            implication = f"ضغط {spike_dir} مفاجئ x{round(last_delta/avg_delta,1)} — حركة قادمة"
            confidence  = "🔵🔵 قوي"

if not pattern:
    return

# ── بناء الرسالة ─────────────────────────────
pattern_label = {
    "BULLISH_DIV": "📈 Bullish CVD Divergence",
    "BEARISH_DIV": "📉 Bearish CVD Divergence",
    "CVD_SPIKE":   "⚡ CVD Spike",
}[pattern]

with lock:
    meta    = symbol_metadata.get(symbol, {})
    oi      = meta.get("oi", 0)
    funding = meta.get("funding", 0.0)

lines = [
    f"📊 <b>CVD Alert — {pattern_label}</b>",
    f"<b>{symbol}</b> | 💵 ${price}",
    "━━━━━━━━━━━━━━━━",
    f"🎯 {implication}",
    f"📶 ثقة: {confidence}",
    "━━━━━━━━━━━━━━━━",
    f"• السعر تغيّر: {round(px_change, 2)}% خلال {CVD_LOOKBACK_SEC//60}m",
    f"• CVD تغيّر: {'+' if cvd_change > 0 else ''}{round(cvd_change, 2)}",
    f"• اتجاه CVD: {'🟢 شراء صافي' if cvd_change > 0 else '🔴 بيع صافي'}",
]

if oi > 0:
    lines.append(f"📊 OI: ${round(oi/1_000_000, 2)}M")
if funding != 0:
    lines.append(f"📉 Funding: {round(funding, 4)}%")

# أقرب سيولة
lz = liq_zone_cache.get(symbol)
if lz:
    if pattern == "BULLISH_DIV" and lz.get("above"):
        n = lz["above"][0]
        d = round(((n["price"] - price) / price) * 100, 2)
        lines.append(f"💧 هدف محتمل: ${n['price']} (+{d}%)")
    elif pattern == "BEARISH_DIV" and lz.get("below"):
        n = lz["below"][0]
        d = round(((price - n["price"]) / price) * 100, 2)
        lines.append(f"💧 هدف محتمل: ${n['price']} (-{d}%)")

send_telegram("\n".join(lines))
last_cvd_alert[symbol] = ts
print(f"CVD ALERT: {symbol} | {pattern} | px={round(px_change,2)}% cvd={round(cvd_change,2)}", flush=True)
```

# =========================

# Whale Detection

# =========================

def check_whale_activity(symbol, price):
ts        = now_ts()
last_wh   = last_whale_alert.get(symbol, 0)
if ts - last_wh < WHALE_ALERT_COOLDOWN_SEC:
return

```
with lock:
    oi_hist   = list(oi_history[symbol])
    fund_hist = list(funding_history[symbol])
    vol_hist  = list(volume_history[symbol])
    prices_lx = list(price_history[symbol])
    funding   = symbol_metadata.get(symbol, {}).get("funding", 0.0)
    oi_now    = symbol_metadata.get(symbol, {}).get("oi", 0.0)

if len(oi_hist) < 4 or len(vol_hist) < 4:
    return

signals   = []
whale_type = None

# ── 1. تجميع خفي: OI يرتفع + سعر ثابت ────────
oi_trend    = calc_trend(oi_hist)
price_move  = abs(calc_change(prices_lx, 180))
if oi_trend >= OI_TREND_MIN_PCT and price_move <= PRICE_STABLE_MAX_PCT:
    signals.append(f"🔍 OI يرتفع {round(oi_trend,2)}% والسعر ثابت ({round(price_move,2)}%)")
    whale_type = "ACCUMULATION"

# ── 2. توزيع: OI ينخفض + سعر يرتفع ───────────
if len(oi_hist) >= 2:
    oi_change = ((oi_hist[-1] - oi_hist[-2]) / oi_hist[-2]) * 100 if oi_hist[-2] > 0 else 0
    price_chg = calc_change(prices_lx, 300)
    if oi_change <= -WHALE_OI_DROP_PCT and price_chg >= 1.5:
        signals.append(f"📤 OI انخفض {round(oi_change,2)}% والسعر ارتفع {round(price_chg,2)}% = توزيع")
        whale_type = "DISTRIBUTION"

# ── 3. امتصاص: حجم كبير + سعر ثابت ───────────
if len(vol_hist) >= 4:
    avg_vol  = sum(vol_hist[:-1]) / max(len(vol_hist) - 1, 1)
    vol_spike = vol_hist[-1] / avg_vol if avg_vol > 0 else 1.0
    price_flat= abs(calc_change(prices_lx, 120))
    if vol_spike >= WHALE_VOL_SPIKE_NO_MOVE and price_flat <= WHALE_PRICE_STABLE_PCT:
        signals.append(
            f"🧲 حجم تداول x{round(vol_spike,1)} بدون حركة سعر ({round(price_flat,2)}%) = امتصاص"
        )
        whale_type = whale_type or "ABSORPTION"

# ── 4. بناء مركز: قفزة Funding مفاجئة ────────
if len(fund_hist) >= 2:
    fund_jump = abs(fund_hist[-1] - fund_hist[-2])
    if fund_jump >= WHALE_FUNDING_JUMP:
        direction = "LONG" if fund_hist[-1] > fund_hist[-2] else "SHORT"
        signals.append(
            f"💥 Funding قفز {round(fund_jump,4)}% → حوت يبني مركز {direction}"
        )
        whale_type = whale_type or "POSITION_BUILD"

if not signals or not whale_type:
    return

type_map = {
    "ACCUMULATION":  ("🟢", "تجميع خفي",     "السعر على وشك يتحرك لأعلى"),
    "DISTRIBUTION":  ("🔴", "توزيع (تصريف)", "الحوت يبيع على الارتفاع"),
    "ABSORPTION":    ("🟡", "امتصاص",         "الحوت يمتص العرض أو الطلب"),
    "POSITION_BUILD":("🔵", "بناء مركز",      "أموال كبيرة تدخل الآن"),
}
emoji, type_label, implication = type_map[whale_type]

lines = [
    f"🐋 <b>Whale Activity Detected</b>",
    f"<b>{symbol}</b> | 💵 ${price}",
    f"━━━━━━━━━━━━━━━━",
    f"{emoji} النوع: <b>{type_label}</b>",
    f"💡 {implication}",
    f"━━━━━━━━━━━━━━━━",
]
for s in signals:
    lines.append(f"• {s}")

lines.append(f"━━━━━━━━━━━━━━━━")
lines.append(f"📊 OI: ${round(oi_now/1_000_000, 2)}M | Funding: {round(funding,4)}%")

# أضف أقرب منطقة سيولة وVP
lz = liq_zone_cache.get(symbol)
if lz and lz.get("above"):
    n = lz["above"][0]
    d = round(((n["price"] - price) / price) * 100, 2)
    lines.append(f"💧 سيولة فوق: ${n['price']} (+{d}%)")
if lz and lz.get("below"):
    n = lz["below"][0]
    d = round(((price - n["price"]) / price) * 100, 2)
    lines.append(f"💧 سيولة تحت: ${n['price']} (-{d}%)")

vp = vp_cache.get(symbol)
if vp:
    poc_dist = round(abs(vp["poc"] - price) / price * 100, 2)
    lines.append(f"📈 POC (أعلى حجم): ${vp['poc']} ({poc_dist}% بعيد)")

send_telegram("\n".join(lines))
last_whale_alert[symbol] = ts
print(f"WHALE ALERT: {symbol} | {whale_type}", flush=True)
```

# =========================

# VP + Psych level check

# =========================

def check_vp_and_psych(symbol, price):
ts       = now_ts()
last_liq = last_liq_zone_alert.get(symbol + “_vp”, 0)
if ts - last_liq < LIQ_ZONE_ALERT_COOLDOWN_SEC:
return

```
lines  = []
hit_vp = False

# Volume Profile
vp = get_vp_cached(symbol, price)
if vp:
    for zone in vp["hvn"]:
        if zone["dist_pct"] <= 0.8:
            tag = " 🎯 POC" if zone["is_poc"] else ""
            lines.append(f"📊 HVN{tag} عند ${zone['price']} (dist {zone['dist_pct']}%)")
            hit_vp = True

# Psychological levels
psych = get_psych_levels(price)
for lvl in psych:
    lines.append(f"🔢 رقم نفسي {lvl['type']}: ${lvl['price']} ({lvl['dist_pct']}%)")

if not lines:
    return

header = [
    f"📍 <b>Key Level Alert</b>",
    f"<b>{symbol}</b> | 💵 ${price}",
    "━━━━━━━━━━━━━━━━",
]
send_telegram("\n".join(header + lines))
last_liq_zone_alert[symbol + "_vp"] = ts
print(f"VP/PSYCH ALERT: {symbol}", flush=True)
```

# =========================

# Original Alerts

# =========================

def send_funding_spike_alert(symbol, prev_f, new_f, price):
delta = new_f - prev_f
arrow = “⬆️” if delta > 0 else “⬇️”
msg = (
“📉 <b>Funding Spike</b>\n”
f”<b>{symbol}</b>\n\n”
f”קודם: <b>{round(prev_f,4)}%</b>\n”
f”עכשיו: <b>{round(new_f,4)}%</b>\n”
f”שינוי: <b>{arrow} {round(delta,4)}%</b>\n”
+ (f”💵 מחיר: ${price}\n” if price and price > 0 else “”)
)
send_telegram(msg)

def _liq_summary(symbol, price, is_long):
lz = liq_zone_cache.get(symbol)
if not lz:
return “”
if is_long and lz.get(“above”):
n = lz[“above”][0]
d = round(((n[“price”] - price) / price) * 100, 2)
return f”\n💧 سيولة فوق: ${n[‘price’]} (+{d}%)”
if not is_long and lz.get(“below”):
n = lz[“below”][0]
d = round(((price - n[“price”]) / price) * 100, 2)
return f”\n💧 سيولة تحت: ${n[‘price’]} (-{d}%)”
return “”

def send_fast_alert(symbol, price, change_1m, change_3m, change_5m, signals, score):
with lock:
meta    = symbol_metadata.get(symbol, {})
oi      = meta.get(“oi”, 0)
funding = meta.get(“funding”, 0)

```
is_long       = change_1m > 0 and change_3m > 0
direction     = "🟢 LONG" if is_long else "🔴 SHORT"
funding_state = "Short pays Long" if funding < 0 else "Long pays Short"
liq_line      = _liq_summary(symbol, price, is_long)

vp_line = ""
vp = vp_cache.get(symbol)
if vp:
    poc_dist = round(abs(vp["poc"] - price) / price * 100, 2)
    vp_line  = f"\n📈 POC: ${vp['poc']} ({poc_dist}% away)"

msg = (
    f"{direction} <b>{symbol}</b>\n"
    f"{strength_from_score(score)}\n\n"
    "<b>סיגנלים (מהיר):</b>\n"
    + "\n".join("- " + s for s in signals)
    + f"\n\n⏱️ 1m: {round(change_1m,1)}% | 3m: {round(change_3m,1)}% | 5m: {round(change_5m,1)}%\n"
    + f"💵 מחיר: ${price}\n"
    + f"📉 Funding: {round(funding,4)}% ({funding_state})\n"
    + f"📊 OI: ${round(oi/1_000_000,3)}M"
    + liq_line + vp_line
)
send_telegram(msg)
```

def send_slow_alert(symbol, price, change_15m, change_1h, change_24h):
with lock:
meta    = symbol_metadata.get(symbol, {})
oi      = meta.get(“oi”, 0)
funding = meta.get(“funding”, 0)

```
direction  = "🟢 LONG" if (change_1h > 0 or change_24h > 0) else "🔴 SHORT"
max_move   = max(abs(change_15m), abs(change_1h), abs(change_24h))
strength   = "🔥🔥🔥🔥 תנועה חריגה מאוד" if max_move >= 50 else ("🔥🔥🔥 חזק מאוד" if max_move >= 30 else "🔥🔥 חזק")
signals    = []
if abs(change_15m) >= MOVE_15M: signals.append(f"⏳ 15m: {round(change_15m,1)}%")
if abs(change_1h)  >= MOVE_1H:  signals.append(f"🕐 1h: {round(change_1h,1)}%")
if abs(change_24h) >= MOVE_24H: signals.append(f"📅 24h: {round(change_24h,1)}%")

funding_state = "Short pays Long" if funding < 0 else "Long pays Short"
is_long       = change_1h > 0 or change_24h > 0
liq_line      = _liq_summary(symbol, price, is_long)

msg = (
    f"{direction} <b>{symbol}</b>\n{strength}\n\n"
    "<b>סיגנלים (איטי):</b>\n"
    + ("\n".join("- " + s for s in signals) if signals else "- תנועה משמעותית")
    + f"\n\n💵 מחיר: ${price}\n"
    + f"📉 Funding: {round(funding,4)}% ({funding_state})\n"
    + f"📊 OI: ${round(oi/1_000_000,3)}M"
    + liq_line
)
send_telegram(msg)
```

def send_accumulation_alert(symbol, price, direction, oi_trend, funding_trend, vol_spike, funding):
funding_state = “Short pays Long” if funding < 0 else “Long pays Short”
dir_txt       = “🟢 LONG מוקדם” if direction == “long” else “🔴 SHORT מוקדם”
signals = [
f”🔍 OI עולה בעקביות: {round(oi_trend,2)}% ממוצע”,
“🧘 מחיר יציב - צבירה לפני מהלך”,
]
if vol_spike >= VOLUME_SPIKE_MIN:
signals.insert(1, f”📦 Volume קפץ: x{round(vol_spike,1)} מהרגיל”)
if abs(funding_trend) >= FUNDING_TREND_MIN:
signals.insert(1, f”📉 Funding מתחיל לזוז: {round(funding_trend,4)}%”)

```
lz_lines = ""
lz = liq_zone_cache.get(symbol)
if lz:
    parts = []
    if lz.get("above"):
        n = lz["above"][0]; d = round(((n["price"]-price)/price)*100,2)
        parts.append(f"⬆️ ${n['price']} (+{d}%) {n['strength']}")
    if lz.get("below"):
        n = lz["below"][0]; d = round(((price-n["price"])/price)*100,2)
        parts.append(f"⬇️ ${n['price']} (-{d}%) {n['strength']}")
    if parts:
        lz_lines = "\n\n💧 <b>مناطق السيولة:</b>\n" + "\n".join(parts)

msg = (
    f"{dir_txt} <b>{symbol}</b>\n"
    "💡 זיהוי מוקדם - לפני שהמחיר זז\n\n"
    "<b>סיגנלים:</b>\n"
    + "\n".join("- " + s for s in signals)
    + f"\n\n💵 מחיר: ${price}\n"
    + f"📉 Funding: {round(funding,4)}% ({funding_state})"
    + lz_lines
)
send_telegram(msg)
```

def send_funding_extreme_alert(symbol, funding, price):
direction = “🟢 Bias LONG” if funding < 0 else “🔴 Bias SHORT”
state     = “Short pays Long” if funding < 0 else “Long pays Short”
msg = (
f”{direction} <b>{symbol}</b>\n”
“⚠️ <b>Funding חריג</b>\n\n”
f”📉 Funding: <b>{round(funding,4)}%</b>\n”
f”🧾 מצב: {state}\n”
f”💵 מחיר: ${price}\n”
)
send_telegram(msg)

# =========================

# Metadata updater

# =========================

def update_metadata():
while True:
try:
data = http_get_json(“https://api.bybit.com/v5/market/tickers?category=linear”, timeout=10)
if not data:
time.sleep(30)
continue

```
        tickers = data.get("result", {}).get("list", [])
        ts      = now_ts()
        local   = {}

        for t in tickers:
            symbol   = t.get("symbol")
            if not symbol:
                continue
            oi       = safe_float(t.get("openInterestValue", 0))
            funding  = safe_float(t.get("fundingRate", 0)) * 100.0
            turnover = safe_float(t.get("turnover24h", 0))

            prev_f = last_funding_seen.get(symbol)
            last_funding_seen[symbol] = funding

            if prev_f is not None:
                delta      = funding - prev_f
                last_spike = last_funding_spike_alert.get(symbol, 0)
                if abs(delta) >= FUNDING_SPIKE_MIN_DELTA and (ts - last_spike >= FUNDING_SPIKE_ALERT_COOLDOWN_SEC):
                    with lock:
                        price = last_price_cache.get(symbol, 0.0)
                    send_funding_spike_alert(symbol, prev_f, funding, price)
                    last_funding_spike_alert[symbol] = ts

            local[symbol] = {
                "oi":           oi,
                "turnover24h":  turnover,
                "funding":      funding,
                "volume24h":    safe_float(t.get("volume24h", 0)),
                "price24hPcnt": safe_float(t.get("price24hPcnt", 0)) * 100.0,
                "last_update":  ts,
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
```

# =========================

# Symbols

# =========================

def get_top_symbols():
data = http_get_json(“https://api.bybit.com/v5/market/tickers?category=linear”, timeout=10)
if not data:
return [“BTCUSDT”, “ETHUSDT”, “SOLUSDT”]
try:
tickers = data.get(“result”, {}).get(“list”, []) or []
valid   = []
for t in tickers:
symbol   = t.get(“symbol”) or “”
if not symbol:
continue
turnover = safe_float(t.get(“turnover24h”, 0))
oi       = safe_float(t.get(“openInterestValue”, 0))
if turnover <= 0 or oi > MAX_OI:
continue
valid.append((symbol, turnover + oi * 0.01))
valid.sort(key=lambda x: x[1], reverse=True)
symbols = [s for s, _ in valid[:TOP_N_SYMBOLS]]
print(f”Tracking {len(symbols)} symbols”, flush=True)
return symbols
except Exception as e:
print(f”Error getting symbols: {e}”, flush=True)
return [“BTCUSDT”, “ETHUSDT”, “SOLUSDT”]

def pass_oi_gate(symbol, fast_score, change_24h):
with lock:
oi = symbol_metadata.get(symbol, {}).get(“oi”, 0)
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
ts   = now_ts()
last = last_accum_alert.get(symbol)
if last and (ts - last) < ACCUMULATION_ALERT_COOLDOWN_SEC:
return

```
with lock:
    oi_hist    = list(oi_history[symbol])
    fund_hist  = list(funding_history[symbol])
    vol_hist   = list(volume_history[symbol])
    prices_lx  = list(price_history[symbol])
    funding    = symbol_metadata.get(symbol, {}).get("funding", 0.0)

if len(oi_hist) < ACCUM_HISTORY_MIN:
    return

price_change  = abs(calc_change(prices_lx, 180))
if price_change > PRICE_STABLE_MAX_PCT:
    return

oi_trend      = calc_trend(oi_hist)
funding_trend = calc_trend(fund_hist)

vol_spike = 1.0
if len(vol_hist) >= 4:
    avg = sum(vol_hist[:-1]) / max(len(vol_hist) - 1, 1)
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
```

# =========================

# Core signal checker

# =========================

def check_signals(symbol, price):
ts = now_ts()
with lock:
last_fast = last_fast_alert.get(symbol)
last_slow = last_slow_alert.get(symbol)

```
fast_blocked = (last_fast is not None) and (ts - last_fast < FAST_ANTI_SPAM_SEC)
slow_blocked = (last_slow is not None) and (ts - last_slow < SLOW_ANTI_SPAM_SEC)

with lock:
    prices_list = list(price_history[symbol])
    meta        = symbol_metadata.get(symbol, {}).copy()

if len(prices_list) < 5:
    return

funding     = meta.get("funding", 0.0)
change_24h  = meta.get("price24hPcnt", 0.0)
turnover24h = meta.get("turnover24h", 0.0)

change_1m = calc_change(prices_list, 60)
change_3m = calc_change(prices_list, 180)
change_5m = calc_change(prices_list, 300)

signals, score = [], 0

if   change_1m >= MOVE_EXPLOSIVE_1M: signals.append(f"💣💣💣 +{round(change_1m,1)}% (PUMP)"); score += 8
elif change_1m >= MOVE_1M:           signals.append(f"🚀 +{round(change_1m,1)}% בדקה");      score += 4
if   change_3m >= MOVE_3M:           signals.append(f"📈 +{round(change_3m,1)}% ב-3m");       score += 3
if   change_5m >= MOVE_5M:           signals.append(f"⚡ +{round(change_5m,1)}% ב-5m");       score += 2

if   change_1m <= -MOVE_EXPLOSIVE_1M: signals.append(f"💣💣💣 {round(change_1m,1)}% (DUMP)"); score += 8
elif change_1m <= -MOVE_1M:           signals.append(f"💥 {round(change_1m,1)}% בדקה");       score += 4
if   change_3m <= -MOVE_3M:           signals.append(f"📉 {round(change_3m,1)}% ב-3m");       score += 3
if   change_5m <= -MOVE_5M:           signals.append(f"⚡ {round(change_5m,1)}% ב-5m");       score += 2

if abs(change_1m) > abs(change_3m) * 0.7 and abs(change_1m) >= 2.0:
    signals.append("⚡ התנעה מואצת"); score += 2

if abs(funding) >= FUNDING_ABS_FOR_BOOST and score >= 4:
    signals.append(f"📉 Funding חריג: {round(funding,4)}%"); score += 2

update_anchors(symbol, ts, price)
with lock:
    a2 = anchors.get(symbol, {}).copy()

t15 = a2.get("t15")
t60 = a2.get("t60")
change_15m = pct_change(t15[1], price) if t15 else 0.0
change_1h  = pct_change(t60[1], price) if t60 else 0.0

if not pass_oi_gate(symbol, score, change_24h):
    return

is_explosive = abs(change_1m) >= MOVE_EXPLOSIVE_1M
if turnover24h < (MIN_TURNOVER_24H_EXPLOSIVE if is_explosive else MIN_TURNOVER_24H_NORMAL):
    return

if score >= 4:
    obc = get_orderbook_cached(symbol)
    if obc.get("ok") and obc.get("payload"):
        max_spread = MAX_SPREAD_EXPLOSIVE_PCT if is_explosive else MAX_SPREAD_NORMAL_PCT
        band       = DEPTH_BAND_EXPLOSIVE_PCT if is_explosive else DEPTH_BAND_NORMAL_PCT
        min_depth  = MIN_DEPTH_EXPLOSIVE_USDT if is_explosive else MIN_DEPTH_NORMAL_USDT
        if obc.get("spread_pct", 0) > max_spread:
            return
        if calc_depth_within_band(obc["payload"], band) < min_depth:
            return
    if ENABLE_1M_VOLUME_FILTER and fetch_1m_turnover_usdt(symbol) < MIN_1M_TURNOVER_USDT:
        return

if score >= 4 and not fast_blocked:
    is_long  = change_1m > 0 and change_3m > 0
    is_short = change_1m < 0 and change_3m < 0
    if is_long or is_short:
        send_fast_alert(symbol, price, change_1m, change_3m, change_5m, signals, score)
        with lock:
            last_fast_alert[symbol] = ts
        return

if (abs(change_15m) >= MOVE_15M or abs(change_1h) >= MOVE_1H or abs(change_24h) >= MOVE_24H) and not slow_blocked:
    send_slow_alert(symbol, price, change_15m, change_1h, change_24h)
    with lock:
        last_slow_alert[symbol] = ts
```

# =========================

# Funding extreme

# =========================

def maybe_send_funding_alert(symbol, funding, price):
if not (funding <= FUNDING_EXTREME_NEG or funding >= FUNDING_EXTREME_POS):
return
ts  = now_ts()
key = symbol + “_extreme”
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
for i in range(0, len(symbols), 50):
batch  = symbols[i:i+50]
topics = [“tickers.” + s for s in batch]
try:
ws.send(json.dumps({“op”: “subscribe”, “args”: topics}))
except Exception as e:
print(f”Subscribe error: {e}”, flush=True)
time.sleep(0.25)

def resubscribe_loop():
global current_symbols, ws_app
while True:
try:
new_symbols = get_top_symbols()
with lock:
existing = set(current_symbols)
to_add   = [s for s in new_symbols if s not in existing]
if to_add:
with ws_lock:
ws = ws_app
if ws:
subscribe_symbols(ws, to_add)
with lock:
current_symbols.extend(to_add)
print(f”Resubscribed {len(to_add)} new symbols”, flush=True)
except Exception as e:
print(f”Resubscribe error: {e}”, flush=True)
time.sleep(RESUBSCRIBE_EVERY_SEC)

def on_message(ws, message):
try:
data  = json.loads(message)
topic = data.get(“topic”, “”)
if not topic.startswith(“tickers.”):
return

```
    td         = data.get("data") or {}
    symbol     = td.get("symbol") or ""
    last_price = safe_float(td.get("lastPrice", 0))
    print(symbol, last_price, flush=True)

    if not symbol or last_price <= 0:
        return

    ts = now_ts()
    with lock:
        prev_price = last_price_cache.get(symbol, 0.0)
        price_history[symbol].append((ts, last_price))
        last_price_cache[symbol] = last_price
        funding = (symbol_metadata.get(symbol, {}) or {}).get("funding", 0.0)

    # ── الفحوصات الأصلية — لا تتأثر بأي شيء ──
    maybe_send_funding_alert(symbol, funding, last_price)
    check_signals(symbol, last_price)
    check_accumulation(symbol, last_price)

    # ── الإضافات الجديدة — كل واحدة معزولة ───
    try:
        check_liq_zones(symbol, last_price)
    except Exception as e:
        print(f"liq_zones error {symbol}: {e}", flush=True)

    try:
        check_whale_activity(symbol, last_price)
    except Exception as e:
        print(f"whale error {symbol}: {e}", flush=True)

    try:
        check_vp_and_psych(symbol, last_price)
    except Exception as e:
        print(f"vp_psych error {symbol}: {e}", flush=True)

    try:
        # CVD: نستخدم فرق السعر فقط — لا volume24h
        price_delta = last_price - prev_price
        update_cvd(symbol, last_price, prev_price, abs(price_delta) * 1000)
        check_cvd(symbol, last_price)
    except Exception as e:
        print(f"cvd error {symbol}: {e}", flush=True)

except Exception as e:
    print(f"Message error: {e}", flush=True)
```

def on_error(ws, error):
print(f”WS error: {error}”, flush=True)

def on_close(ws, code, msg):
print(f”WS closed | {code} | {msg}”, flush=True)

def on_open(ws):
global current_symbols
print(“WS connected!”, flush=True)
symbols = get_top_symbols()
with lock:
current_symbols = list(symbols)
subscribe_symbols(ws, symbols)
print(f”Subscribed: {len(symbols)} symbols”, flush=True)

def start_websocket():
global ws_app
ws = websocket.WebSocketApp(
“wss://stream.bybit.com/v5/public/linear”,
on_open=on_open, on_message=on_message,
on_error=on_error, on_close=on_close,
)
with ws_lock:
ws_app = ws
ws.run_forever(ping_interval=20, ping_timeout=10, reconnect=5)

# =========================

# Main

# =========================

if **name** == “**main**”:
print(“MOHAMED BOT V3 — CVD + LIQ + WHALE + VP + PSYCH LIVE”, flush=True)

```
threading.Thread(target=update_metadata, daemon=True).start()
threading.Thread(target=resubscribe_loop, daemon=True).start()

while True:
    try:
        start_websocket()
    except Exception as e:
        print(f"WS crashed: {e}", flush=True)
        time.sleep(10)
```
