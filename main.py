import requests
import os
import time

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

alerted = {}
prev_prices = {}
prev_oi = {}

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"שגיאת טלגרם: {e}")

def get_bybit_tickers():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        r = requests.get(url, timeout=10)
        return r.json()["result"]["list"]
    except Exception as e:
        print(f"שגיאת Bybit: {e}")
        return []

def scan():
    print("סורק...")
    tickers = get_bybit_tickers()
    if not tickers:
        print("אין נתונים")
        return

    now = time.time()
    found = 0

    for t in tickers:
        try:
            symbol = t["symbol"]
            price = float(t["lastPrice"])
            change_24h = float(t["price24hPcnt"]) * 100
            volume = float(t["turnover24h"])
            funding = float(t.get("fundingRate", 0)) * 100
            oi = float(t.get("openInterestValue", 0))

            # פילטר שווי שוק — רק medium-small cap
            if oi < 5000000 or oi > 200000000:
                continue

            # פילטר volume
            if volume < 1000000 or volume > 80000000:
                continue

            # שינוי 15 דקות במחיר
            change_15m = 0
            if symbol in prev_prices:
                change_15m = ((price - prev_prices[symbol]) / prev_prices[symbol]) * 100
            prev_prices[symbol] = price

            # שינוי OI
            oi_change = 0
            if symbol in prev_oi and prev_oi[symbol] > 0:
                oi_change = ((oi - prev_oi[symbol]) / prev_oi[symbol]) * 100
            prev_oi[symbol] = oi

            # לא לשלוח שוב תוך 6 שעות
            if symbol in alerted and now - alerted[symbol] < 21600:
                continue

            # סיגנלי LONG — חייב לפחות 2 סיגנלים
            long_15m = change_15m >= 3 and change_24h >= 5
            long_24h = change_24h >= 10 and change_24h <= 30
            long_funding = funding >= 0.1 and change_24h >= 5
            long_oi = oi_change >= 5 and change_24h >= 3

            # סיגנלי SHORT — חייב לפחות 2 סיגנלים
            short_15m = change_15m <= -3 and change_24h <= -5
            short_24h = change_24h <= -10 and change_24h >= -30
            short_funding = funding <= -0.1 and change_24h <= -5
            short_oi = oi_change >= 5 and change_24h <= -3

            long_score = sum([long_15m, long_24h, long_funding, long_oi])
            short_score = sum([short_15m, short_24h, short_funding, short_oi])

            # רק אם יש לפחות 2 סיגנלים — פחות רעש
            if long_score < 2 and short_score < 2:
                continue

            is_long = long_score >= short_score and long_score >= 2
            is_short = short_score > long_score and short_score >= 2

            if not (is_long or is_short):
                continue

            score = long_score if is_long else short_score

            if score >= 3:
                strength = "🔥🔥🔥 חזק מאוד"
            elif score == 2:
                strength = "🔥🔥 חזק"
            else:
                strength = "🔥 בינוני"

            if is_long:
                direction = "🟢 LONG"
                if long_oi and long_15m:
                    signal_txt = "OI + מחיר עולים — כניסה מוקדמת"
                elif long_oi:
                    signal_txt = "OI עולה — כסף חדש נכנס"
                elif long_15m:
                    signal_txt = "מחיר זז עכשיו — LONG מוקדם"
                elif long_funding:
                    signal_txt = "Funding גבוה — לונגים חזקים"
                else:
                    signal_txt = "כניסה LONG"
            else:
                direction = "🔴 SHORT"
                if short_oi and short_15m:
                    signal_txt = "OI + מחיר יורדים — SHORT מוקדם"
                elif short_oi:
                    signal_txt = "OI עולה בירידה — לחץ שורטים"
                elif short_15m:
                    signal_txt = "מחיר זז עכשיו — SHORT מוקדם"
                elif short_funding:
                    signal_txt = "Funding שלילי — שורטים חזקים"
                else:
                    signal_txt = "כניסה SHORT"

            oi_txt = f"📐 OI: {oi_change:+.1f}%" if oi_change != 0 else ""
            funding_txt = f"💹 Funding: {funding:.4f}%"
            if funding >= 0.1:
                funding_txt += " 🔥"
            elif funding <= -0.1:
                funding_txt += " 🩸"

            msg = (
                f"{direction} <b>{symbol}</b>\n"
                f"📊 {signal_txt}\n"
                f"💪 {strength}\n"
                f"📈 24h: {change_24h:.1f}% | 15m: {change_15m:.1f}%\n"
                f"💵 מחיר: ${price}\n"
                f"📦 Volume: ${volume/1000000:.1f}M\n"
                f"{oi_txt}\n"
                f"{funding_txt}"
            )
            send_telegram(msg)
            alerted[symbol] = now
            found += 1
            print(f"נמצא: {symbol} {direction} | ציון: {score}")

        except Exception as e:
            continue

    print(f"סריקה הסתיימה — {found} סיגנלים")

print("מתחיל סורק...")
while True:
    try:
        scan()
    except Exception as e:
        print(f"שגיאה: {e}")
    time.sleep(180)
