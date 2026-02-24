import requests
import os
import time
from collections import deque

TELEGRAM_TOKEN = os.environ.get(“TELEGRAM_TOKEN”)
CHAT_ID = os.environ.get(“CHAT_ID”)

# היסטוריה לכל מטבע — 10 סריקות אחרונות

history = {}  # symbol -> deque של {price, oi, volume, funding}
alerted = {}  # symbol -> timestamp
market_history = deque(maxlen=5)  # היסטוריה של מצב השוק הכללי

def send_telegram(msg):
try:
url = f”https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage”
requests.post(url, data={“chat_id”: CHAT_ID, “text”: msg, “parse_mode”: “HTML”}, timeout=10)
except Exception as e:
print(f”שגיאת טלגרם: {e}”)

def get_bybit_tickers():
try:
url = “https://api.bybit.com/v5/market/tickers?category=linear”
r = requests.get(url, timeout=10)
return r.json()[“result”][“list”]
except Exception as e:
print(f”שגיאת Bybit: {e}”)
return []

def analyze_market(tickers):
“””
מנתח את מצב השוק הכללי —
האם רוב המטבעות עולים או יורדים
“””
gainers = 0
losers = 0
big_movers = 0  # מטבעות שזזו 5%+ ב-24h

```
for t in tickers:
    try:
        change = float(t["price24hPcnt"]) * 100
        if change > 2:
            gainers += 1
        elif change < -2:
            losers += 1
        if abs(change) >= 5:
            big_movers += 1
    except:
        continue

total = gainers + losers
if total == 0:
    return "neutral", 0

bull_pct = (gainers / total) * 100

if bull_pct >= 70:
    trend = "bull"
elif bull_pct <= 30:
    trend = "bear"
else:
    trend = "neutral"

return trend, bull_pct
```

def detect_market_move(current_trend, bull_pct):
“””
מזהה אם השוק הכללי עומד לעשות מהלך גדול
על ידי השוואה לסריקות קודמות
“””
market_history.append({“trend”: current_trend, “bull_pct”: bull_pct})

```
if len(market_history) < 3:
    return None

# בדוק אם יש שינוי מגמה מהיר בשוק
recent = list(market_history)[-3:]
bull_values = [m["bull_pct"] for m in recent]

change = bull_values[-1] - bull_values[0]

if change >= 20:
    return "🌊 שוק מתחיל לעלות בכוח — רוב המטבעות פונים לירוק"
elif change <= -20:
    return "🌊 שוק מתחיל לרדת בכוח — רוב המטבעות פונים לאדום"
elif bull_values[-1] >= 75 and all(v >= 65 for v in bull_values):
    return "📈 שוק חזק בעקביות — מגמת עלייה יציבה"
elif bull_values[-1] <= 25 and all(v <= 35 for v in bull_values):
    return "📉 שוק חלש בעקביות — מגמת ירידה יציבה"

return None
```

def get_history(symbol):
if symbol not in history:
history[symbol] = deque(maxlen=10)
return history[symbol]

def calc_volume_avg(hist):
“”“ממוצע volume של הסריקות הקודמות”””
if len(hist) < 3:
return None
return sum(h[“volume”] for h in hist) / len(hist)

def calc_oi_trend(hist):
“””
בודק אם OI עולה בעקביות —
מחזיר את האחוז הממוצע של עלייה
“””
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
“”“בודק אם funding rate מטפס או יורד”””
if len(hist) < 3:
return 0
items = list(hist)
first = items[0][“funding”]
last = items[-1][“funding”]
return last - first

def scan():
print(“סורק…”)
tickers = get_bybit_tickers()
if not tickers:
print(“אין נתונים”)
return

```
now = time.time()
found = 0

# ניתוח שוק כללי
market_trend, bull_pct = analyze_market(tickers)
market_alert = detect_market_move(market_trend, bull_pct)

# שלח התראת שוק אם יש מהלך כללי
if market_alert:
    trend_emoji = "🟢" if market_trend == "bull" else "🔴" if market_trend == "bear" else "⚪"
    market_msg = (
        f"⚠️ <b>התראת שוק כללי</b>\n"
        f"{market_alert}\n"
        f"{trend_emoji} {bull_pct:.0f}% מהמטבעות בכיוון העלייה"
    )
    send_telegram(market_msg)
    print(f"התראת שוק: {market_alert}")

for t in tickers:
    try:
        symbol = t["symbol"]
        price = float(t["lastPrice"])
        change_24h = float(t["price24hPcnt"]) * 100
        volume = float(t["turnover24h"])
        funding = float(t.get("fundingRate", 0)) * 100
        oi = float(t.get("openInterestValue", 0))

        # פילטר בסיסי
        if oi < 1_000_000 or oi > 300_000_000:
            continue
        if volume < 500_000 or volume > 150_000_000:
            continue
        if price <= 0:
            continue

        # שמור היסטוריה
        hist = get_history(symbol)
        hist.append({
            "price": price,
            "oi": oi,
            "volume": volume,
            "funding": funding,
            "time": now
        })

        # לא לשלוח שוב תוך 6 שעות
        if symbol in alerted and now - alerted[symbol] < 21600:
            continue

        # חייבים לפחות 3 סריקות היסטוריה
        if len(hist) < 3:
            continue

        items = list(hist)
        prev_price = items[-2]["price"]

        # שינוי 15 דקות
        change_15m = ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0

        # Volume spike — ביחס לממוצע היסטורי
        vol_avg = calc_volume_avg(list(hist)[:-1])
        vol_spike = (volume / vol_avg) if vol_avg and vol_avg > 0 else 1

        # OI trend — עולה בעקביות?
        oi_trend = calc_oi_trend(hist)
        current_oi_change = ((oi - items[-2]["oi"]) / items[-2]["oi"]) * 100 if items[-2]["oi"] > 0 else 0

        # Funding trend
        funding_trend = calc_funding_trend(hist)

        # ══════════════════════════════════════
        # סיגנלים עם משקל — כל סיגנל = ציון
        # ══════════════════════════════════════

        long_signals = []
        short_signals = []

        # --- LONG סיגנלים ---

        # Volume spike חזק מאוד — לבד מספיק
        if vol_spike >= 5 and change_24h >= 0:
            long_signals.append(("💥 Volume פי {:.1f} מהרגיל".format(vol_spike), 3))
        elif vol_spike >= 3 and change_24h >= 0:
            long_signals.append(("📊 Volume פי {:.1f} מהרגיל".format(vol_spike), 2))

        # מחיר מתחיל לזוז עכשיו
        if change_15m >= 5:
            long_signals.append(("🚀 מחיר +{:.1f}% ב-15 דקות".format(change_15m), 3))
        elif change_15m >= 3:
            long_signals.append(("📈 מחיר +{:.1f}% ב-15 דקות".format(change_15m), 2))

        # OI עולה בעקביות + מחיר יציב = צבירה שקטה
        if oi_trend >= 3 and abs(change_15m) < 2:
            long_signals.append(("🔍 OI עולה בשקט — צבירה לפני מהלך", 3))
        elif oi_trend >= 2:
            long_signals.append(("📐 OI עולה בעקביות", 2))

        # Funding מטפס — לחץ לונגים בונה
        if funding_trend >= 0.05 and funding >= 0.05:
            long_signals.append(("💹 Funding מטפס — לחץ לונגים", 2))
        elif funding >= 0.1:
            long_signals.append(("🔥 Funding גבוה מאוד", 2))

        # 24h חזק
        if 10 <= change_24h <= 60:
            long_signals.append(("📈 24h: +{:.1f}%".format(change_24h), 1))

        # מטבע הולך נגד שוק יורד = חזק מאוד
        if market_trend == "bear" and change_24h >= 10:
            long_signals.append(("⚡ עולה נגד שוק יורד — חזק מאוד", 3))

        # --- SHORT סיגנלים ---

        if vol_spike >= 5 and change_24h <= 0:
            short_signals.append(("💥 Volume פי {:.1f} מהרגיל".format(vol_spike), 3))
        elif vol_spike >= 3 and change_24h <= 0:
            short_signals.append(("📊 Volume פי {:.1f} מהרגיל".format(vol_spike), 2))

        if change_15m <= -5:
            short_signals.append(("💥 מחיר {:.1f}% ב-15 דקות".format(change_15m), 3))
        elif change_15m <= -3:
            short_signals.append(("📉 מחיר {:.1f}% ב-15 דקות".format(change_15m), 2))

        if oi_trend >= 3 and abs(change_15m) < 2:
            short_signals.append(("🔍 OI עולה בשקט — לחץ שורטים בונה", 3))
        elif oi_trend >= 2 and change_24h < 0:
            short_signals.append(("📐 OI עולה + מחיר יורד", 2))

        if funding_trend <= -0.05 and funding <= -0.05:
            short_signals.append(("💹 Funding יורד — לחץ שורטים", 2))
        elif funding <= -0.1:
            short_signals.append(("🩸 Funding שלילי מאוד", 2))

        if -60 <= change_24h <= -10:
            short_signals.append(("📉 24h: {:.1f}%".format(change_24h), 1))

        if market_trend == "bull" and change_24h <= -10:
            short_signals.append(("⚡ יורד נגד שוק עולה — חזק מאוד", 3))

        # ══════════════════════════════════════
        # חישוב ציון כולל
        # ══════════════════════════════════════

        long_score = sum(w for _, w in long_signals)
        short_score = sum(w for _, w in short_signals)

        # סיגנל בודד חזק מאוד (ציון 3) = מספיק לבד
        # סיגנלים בינוניים = צריך ציון 4+
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

        # ══════════════════════════════════════
        # בניית הודעה
        # ══════════════════════════════════════

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

        # שורות סיגנלים
        signals_txt = "\n".join(f"• {s}" for s, _ in signals_used)

        # מצב שוק
        if market_trend == "bull":
            market_txt = f"🌍 שוק: עולה ({bull_pct:.0f}% ירוק)"
        elif market_trend == "bear":
            market_txt = f"🌍 שוק: יורד ({100-bull_pct:.0f}% אדום)"
        else:
            market_txt = f"🌍 שוק: ניטרלי"

        oi_txt = f"📐 OI trend: {oi_trend:+.1f}% ממוצע" if oi_trend != 0 else ""
        funding_txt = f"💹 Funding: {funding:.4f}%"

        msg = (
            f"{direction} <b>{symbol}</b>\n"
            f"💪 {strength}\n"
            f"\n<b>סיגנלים:</b>\n{signals_txt}\n"
            f"\n📈 24h: {change_24h:.1f}% | 15m: {change_15m:.1f}%\n"
            f"💵 מחיר: ${price}\n"
            f"📦 Volume: ${volume/1_000_000:.1f}M (פי {vol_spike:.1f} מהרגיל)\n"
            f"{oi_txt}\n"
            f"{funding_txt}\n"
            f"{market_txt}"
        )

        send_telegram(msg)
        alerted[symbol] = now
        found += 1
        print(f"נמצא: {symbol} {direction} | ציון: {score}")

    except Exception as e:
        continue

print(f"סריקה הסתיימה — {found} סיגנלים")
```

print(“מתחיל סורק…”)
while True:
try:
scan()
except Exception as e:
print(f”שגיאה: {e}”)
time.sleep(180)
