import requests
import time

TELEGRAM_TOKEN = "8766891112:AAEiJZ_e--Y9tTdyj-5w4PGMv42BDHNmOoU"
CHAT_ID = "711459112"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

def get_bybit_tickers():
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    r = requests.get(url)
    return r.json()["result"]["list"]

def scan():
    print("סורק...")
    tickers = get_bybit_tickers()
    for t in tickers:
        try:
            symbol = t["symbol"]
            change = float(t["price24hPcnt"]) * 100
            volume = float(t["turnover24h"])
            price = float(t["lastPrice"])
            if change >= 15 and volume >= 1000000 and volume <= 50000000:
                msg = f"🚨 PUMP ALERT!\n💰 {symbol}\n📈 עלייה: {change:.1f}%\n💵 מחיר: ${price}\n📊 Volume: ${volume:,.0f}"
                send_telegram(msg)
                print(msg)
        except:
            pass

while True:
    scan()
    time.sleep(60)
