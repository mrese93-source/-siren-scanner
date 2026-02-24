import requests
import os
import time

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

alerted = {}

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})

def get_bybit_tickers():
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    r = requests.get(url)
    return r.json()["result"]["list"]

def scan():
    tickers = get_bybit_tickers()
    now = time.time()
    for t in tickers:
        try:
            symbol = t["symbol"]
            change = float(t["price24hPcnt"]) * 100
            volume = float(t["turnover24h"])
            price = float(t["lastPrice"])

            if symbol in alerted and now - alerted[symbol] < 14400:
                continue

            if change >= 8 and change <= 25 and volume >= 1000000 and volume <= 80000000:
                if change < 12:
                    emoji = "👀"
                    signal = "כניסה מוקדמת"
                else:
                    emoji = "🚀"
                    signal = "כניסה חזקה"

                msg = (
                    f"{emoji} <b>{symbol}</b> — {signal}\n"
                    f"📈 עלייה 24h: {change:.1f}%\n"
                    f"💵 מחיר: ${price}\n"
                    f"📊 Volume: ${volume/1000000:.1f}M\n"
                    f"⚠️ בדוק גרף לפני כניסה!"
                )
                send_telegram(msg)
                alerted[symbol] = now

        except:
            pass

while True:
    scan()
    time.sleep(120)
