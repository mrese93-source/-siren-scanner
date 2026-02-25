import requests
import os
import time
from collections import deque

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get(‘CHAT_ID’)

history = {}
alerted = {}
market_history = deque(maxlen=5)

def send_telegram(msg):
try:
url = ‘https://api.telegram.org/bot’ + TELEGRAM_TOKEN + ‘/sendMessage’
requests.post(url, data={‘chat_id’: CHAT_ID, ‘text’: msg, ‘parse_mode’: ‘HTML’}, timeout=10)
except Exception as e:
print(’Telegram error: ’ + str(e))

def get_bybit_tickers():
try:
url = ‘https://api.bybit.com/v5/market/tickers?category=linear’
r = requests.get(url, timeout=10)
return r.json()[‘result’][‘list’]
except Exception as e:
print(’Bybit error: ’ + str(e))
return []

def analyze_market(tickers):
gainers = 0
losers = 0
for t in tickers:
try:
change = float(t[‘price24hPcnt’]) * 100
if change > 2:
gainers += 1
elif change < -2:
losers += 1
except:
continue
total = gainers + losers
if total == 0:
return ‘neutral’, 50
bull_pct = (gainers / total) * 100
if bull_pct >= 70:
trend = ‘bull’
elif bull_pct <= 30:
trend = ‘bear’
else:
trend = ‘neutral’
return trend, bull_pct

def detect_market_move(current_trend, bull_pct):
market_history.append({‘trend’: current_trend, ‘bull_pct’: bull_pct})
if len(market_history) < 3:
return None
recent = list(market_history)[-3:]
bull_values = [m[‘bull_pct’] for m in recent]
change = bull_values[-1] - bull_values[0]
if change >= 20:
return ‘\u26a0\ufe0f <b>Market Alert</b>\n\U0001f30a השוק מתחיל לעלות בכוח’
elif change <= -20:
return ‘\u26a0\ufe0f <b>Market Alert</b>\n\U0001f30a השוק מתחיל לרדת בכוח’
elif bull_values[-1] >= 75 and all(v >= 65 for v in bull_values):
return ‘\u26a0\ufe0f <b>Market Alert</b>\n\U0001f4c8 מגמת עלייה יציבה בשוק’
elif bull_values[-1] <= 25 and all(v <= 35 for v in bull_values):
return ‘\u26a0\ufe0f <b>Market Alert</b>\n\U0001f4c9 מגמת ירידה יציבה בשוק’
return None

def get_history(symbol):
if symbol not in history:
history[symbol] = deque(maxlen=10)
return history[symbol]

def calc_volume_avg(hist_list):
if len(hist_list) < 3:
return None
return sum(h[‘volume’] for h in hist_list) / len(hist_list)

def calc_oi_trend(hist):
if len(hist) < 3:
return 0
changes = []
items = list(hist)
for i in range(1, len(items)):
prev = items[i-1][‘oi’]
curr = items[i][‘oi’]
if prev > 0:
changes.append(((curr - prev) / prev) * 100)
if not changes:
return 0
return sum(changes) / len(changes)

def calc_funding_trend(hist):
if len(hist) < 3:
return 0
items = list(hist)
return items[-1][‘funding’] - items[0][‘funding’]

def scan():
print(‘Scanning…’)
tickers = get_bybit_tickers()
if not tickers:
print(‘No data’)
return

```
now = time.time()
found = 0

market_trend, bull_pct = analyze_market(tickers)
market_alert = detect_market_move(market_trend, bull_pct)

if market_alert:
    trend_emoji = '\U0001f7e2' if market_trend == 'bull' else '\U0001f534' if market_trend == 'bear' else '\u26aa'
    market_msg = market_alert + '\n' + trend_emoji + ' ' + str(round(bull_pct)) + '% מהמטבעות בעלייה'
    send_telegram(market_msg)
    print('Market alert sent')

for t in tickers:
    try:
        symbol = t['symbol']
        price = float(t['lastPrice'])
        change_24h = float(t['price24hPcnt']) * 100
        volume = float(t['turnover24h'])
        funding = float(t.get('fundingRate', 0)) * 100
        oi = float(t.get('openInterestValue', 0))

        if oi < 1000000 or oi > 300000000:
            continue
        if volume < 500000 or volume > 150000000:
            continue
        if price <= 0:
            continue

        hist = get_history(symbol)
        hist.append({'price': price, 'oi': oi, 'volume': volume, 'funding': funding, 'time': now})

        if symbol in alerted and now - alerted[symbol] < 21600:
            continue

        if len(hist) < 3:
            continue

        items = list(hist)
        prev_price = items[-2]['price']
        change_15m = ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0

        vol_avg = calc_volume_avg(list(hist)[:-1])
        vol_spike = (volume / vol_avg) if vol_avg and vol_avg > 0 else 1

        oi_trend = calc_oi_trend(hist)
        funding_trend = calc_funding_trend(hist)

        long_signals = []
        short_signals = []

        if vol_spike >= 5 and change_24h >= 0:
            long_signals.append(('Volume x' + str(round(vol_spike, 1)) + ' מהרגיל \U0001f4a5', 3))
        elif vol_spike >= 3 and change_24h >= 0:
            long_signals.append(('Volume x' + str(round(vol_spike, 1)) + ' מהרגיל', 2))

        if change_15m >= 5:
            long_signals.append(('+' + str(round(change_15m, 1)) + '% ב-15 דקות \U0001f680', 3))
        elif change_15m >= 3:
            long_signals.append(('+' + str(round(change_15m, 1)) + '% ב-15 דקות', 2))

        if oi_trend >= 3 and abs(change_15m) < 2:
            long_signals.append(('OI עולה בשקט - צבירה לפני מהלך \U0001f50d', 3))
        elif oi_trend >= 2:
            long_signals.append(('OI עולה בעקביות', 2))

        if funding_trend >= 0.05 and funding >= 0.05:
            long_signals.append(('Funding מטפס - לחץ לונגים \U0001f4b9', 2))
        elif funding >= 0.1:
            long_signals.append(('Funding גבוה \U0001f525', 2))

        if 10 <= change_24h <= 60:
            long_signals.append(('24h: +' + str(round(change_24h, 1)) + '%', 1))

        if market_trend == 'bear' and change_24h >= 10:
            long_signals.append(('עולה נגד שוק יורד \u26a1', 3))

        if vol_spike >= 5 and change_24h <= 0:
            short_signals.append(('Volume x' + str(round(vol_spike, 1)) + ' מהרגיל \U0001f4a5', 3))
        elif vol_spike >= 3 and change_24h <= 0:
            short_signals.append(('Volume x' + str(round(vol_spike, 1)) + ' מהרגיל', 2))

        if change_15m <= -5:
            short_signals.append((str(round(change_15m, 1)) + '% ב-15 דקות \U0001f4a5', 3))
        elif change_15m <= -3:
            short_signals.append((str(round(change_15m, 1)) + '% ב-15 דקות', 2))

        if oi_trend >= 3 and abs(change_15m) < 2:
            short_signals.append(('OI עולה בשקט - לחץ שורטים \U0001f50d', 3))
        elif oi_trend >= 2 and change_24h < 0:
            short_signals.append(('OI עולה + מחיר יורד', 2))

        if funding_trend <= -0.05 and funding <= -0.05:
            short_signals.append(('Funding יורד - לחץ שורטים', 2))
        elif funding <= -0.1:
            short_signals.append(('Funding שלילי \U0001fa78', 2))

        if -60 <= change_24h <= -10:
            short_signals.append(('24h: ' + str(round(change_24h, 1)) + '%', 1))

        if market_trend == 'bull' and change_24h <= -10:
            short_signals.append(('יורד נגד שוק עולה \u26a1', 3))

        long_score = sum(w for _, w in long_signals)
        short_score = sum(w for _, w in short_signals)

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

        if is_long:
            direction = '\U0001f7e2 LONG'
            signals_used = long_signals
            score = long_score
        else:
            direction = '\U0001f534 SHORT'
            signals_used = short_signals
            score = short_score

        if score >= 8:
            strength = '\U0001f525\U0001f525\U0001f525\U0001f525 חזק מאוד'
        elif score >= 6:
            strength = '\U0001f525\U0001f525\U0001f525 חזק'
        elif score >= 4:
            strength = '\U0001f525\U0001f525 בינוני-חזק'
        else:
            strength = '\U0001f525 בינוני'

        signals_txt = '\n'.join('- ' + s for s, _ in signals_used)

        if market_trend == 'bull':
            market_txt = '\U0001f30d שוק: עולה (' + str(round(bull_pct)) + '% ירוק)'
        elif market_trend == 'bear':
            market_txt = '\U0001f30d שוק: יורד (' + str(round(100 - bull_pct)) + '% אדום)'
        else:
            market_txt = '\U0001f30d שוק: ניטרלי'

        msg = (
            direction + ' <b>' + symbol + '</b>\n'
            + strength + '\n\n'
            + '<b>סיגנלים:</b>\n' + signals_txt + '\n\n'
            + '24h: ' + str(round(change_24h, 1)) + '% | 15m: ' + str(round(change_15m, 1)) + '%\n'
            + '\U0001f4b5 מחיר: $' + str(price) + '\n'
            + '\U0001f4e6 Volume: $' + str(round(volume / 1000000, 1)) + 'M (x' + str(round(vol_spike, 1)) + ')\n'
            + '\U0001f4b9 Funding: ' + str(round(funding, 4)) + '%\n'
            + market_txt
        )

        send_telegram(msg)
        alerted[symbol] = now
        found += 1
        print('Found: ' + symbol + ' ' + direction + ' | Score: ' + str(score))

    except Exception as e:
        continue

print('Scan done - ' + str(found) + ' signals')
```

print(‘Starting scanner…’)
while True:
try:
scan()
except Exception as e:
print(’Error: ’ + str(e))
time.sleep(180)
