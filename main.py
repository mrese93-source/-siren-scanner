import requests
import os
import time

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

alerted = {}
prev_prices = {}

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

            change_15m = 0
            if symbol in prev_prices:
                change_15m = ((price - prev_prices[symbol]) / prev_prices[symbol]) * 100
            prev_prices[symbol] = price

            if symbol in alerted and now - alerted[symbol] < 14400:
                continue

            if volume < 1000000 or volume > 80000000:
                continue

            long_15m = change_15m >= 3 and change_24h >= 5
            long_24h = change_24h >= 8 and change_24h <= 25
            long_funding = funding >= 0.1 and change_24h >= 5

            short_15m = change_15m <= -3 and change_24h <= -5
            short_24h = change_24h <= -8 and change_24h >= -25
            short_funding = funding <= -0.1 and change_24h <= -5

            is_long = long_15m or long_24h or long_funding
            is_short = short_15m or short_24h or short_funding

            if not (is_long or is_short):
                continue

            if is_long:
                if long_15m:
                    emoji = "⚡"
                    signal_txt = "זז עכשיו — LONG מוקדם"
                elif long_funding:
                    emoji = "💹"
                    signal_txt = "Funding גבוה — LONG חזק"
                else:
                    emoji = "🚀"
                    signal_txt = "כניסה LONG"
                if funding >= 0.1:
                    funding_txt = f"💹 Funding: {funding:.4f}% 🔥"
                elif funding < 0:
                    funding_txt = f"💹 Funding: {funding:.4f}% ❄️"
                else:
                    funding_txt = f"💹 Funding: {funding:.4f}%"
                direction = "🟢 LONG"
            else:
                if short_15m:
                    emoji = "⚡"
                    signal_txt = "זז עכשיו — SHORT מוקדם"
                elif short_funding:
                    emoji = "🩸"
                    signal_txt = "Funding שלילי — SHORT חזק"
                else:
                    emoji = "📉"
                    signal_txt = "כניסה SHORT"
                if funding <= -0.1:
                    funding_txt = f"💹 Funding: {funding:.4f}% 🩸"
                elif funding > 0:
                    funding_txt = f"💹 Funding: {funding:.4f}% 🔥"
                else:
                    funding_txt = f"💹 Funding: {funding:.4f}%"
                direction = "🔴 SHORT"

            msg = (
                f"{emoji} <b>{symbol}</b> — {direction}\n"
                f"📊 {signal_txt}\n"
                f"📈 24h: {change_24h:.1f}% | 15m: {change_15m:.1f}%\n"
                f"💵 מחיר: ${price}\n"
                f"📦 Volume: ${volume/1000000:.1f}M\n"
                f"{funding_txt}\n"
                f"⚠️ בדוק גרף לפני כניסה!"
            )
            send_telegram(msg)
            alerted[symbol] = now
            found += 1
            print(f"נמצא: {symbol} {direction}")

        except Exception as e:
            print(f"שגיאה ב-{t.get('symbol','?')}: {e}")
            continue

    print(f"סריקה הסתיימה — נמצאו {found} סיגנלים")

print("מתחיל סורק...")
while True:
    try:
        scan()
    except Exception as e:
        print(f"שגיאה כללית: {e}")
    time.sleep(900)
