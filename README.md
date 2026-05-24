# 🚀 TradeFlow AI — Crypto Signal Scanner MVP

Real-time crypto market scanner with AI-powered trading signals, Telegram alerts,
and a modern dashboard built for traders.

---

## 📁 Project Structure

```
tradeflow-ai/
├── backend/          ← Python FastAPI server
│   ├── app/
│   │   ├── main.py            ← FastAPI entry point
│   │   ├── config.py          ← Environment settings
│   │   ├── database.py        ← SQLite setup
│   │   ├── models.py          ← DB models (Signal, AlertLog, User)
│   │   ├── schemas.py         ← Pydantic response schemas
│   │   ├── routers/
│   │   │   └── signals.py     ← API endpoints
│   │   └── services/
│   │       ├── bybit_client.py    ← Bybit REST API calls
│   │       ├── scanner.py         ← Background scan loop
│   │       ├── signal_generator.py← Signal logic
│   │       ├── ai_scorer.py       ← AI score calculation
│   │       └── telegram_alerts.py ← Telegram notifications
│   ├── requirements.txt
│   ├── .env.example
│   └── Procfile               ← For Railway deployment
└── frontend/         ← Next.js 14 dashboard
    └── src/
        ├── app/
        │   ├── page.tsx           ← Main dashboard
        │   └── coin/[symbol]/     ← Per-coin detail page
        ├── components/
        │   ├── Header.tsx
        │   ├── SignalTable.tsx
        │   └── TradeSetup.tsx
        ├── lib/api.ts             ← API client
        └── types/index.ts         ← TypeScript types
```

---

## 🛠 Local Setup (Mac/iPhone via SSH or computer)

### Prerequisites
- Python 3.11+
- Node.js 18+

### 1. Backend

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy env file and fill in values
cp .env.example .env
# Edit .env with your Telegram token etc.

# Run the server
uvicorn app.main:app --reload --port 8000
```

Server runs at: http://localhost:8000
API docs at: http://localhost:8000/docs

### 2. Frontend

```bash
cd frontend

# Install dependencies
npm install

# Copy env file
cp .env.example .env.local
# NEXT_PUBLIC_API_URL=http://localhost:8000

# Run dev server
npm run dev
```

Frontend runs at: http://localhost:3000

---

## 🔑 Environment Variables

### Backend `.env`
```
BYBIT_API_KEY=           # Optional — public data doesn't need auth
BYBIT_API_SECRET=        # Optional
TELEGRAM_TOKEN=          # Your bot token from @BotFather
TELEGRAM_CHAT_ID=        # Your channel/group ID (e.g. -100xxxx)
SCAN_INTERVAL_SECONDS=60 # How often to scan (default: 60s)
ALERT_COOLDOWN_SECONDS=300 # Min time between alerts per coin
CORS_ORIGINS=http://localhost:3000,https://your-frontend.vercel.app
```

### Frontend `.env.local`
```
NEXT_PUBLIC_API_URL=https://your-backend.railway.app
```

---

## 📲 Telegram Setup (step by step)

1. Open Telegram → search **@BotFather**
2. Send `/newbot` → give it a name like `TradeFlow AI`
3. Copy the **token** (looks like `7123456789:AAHxxxxx`)
4. Create a Telegram **channel** or **group**
5. Add your bot as **Admin** with "Post Messages" permission
6. Get your Chat ID:
   - For a channel: forward any message to @userinfobot, or use the format `-100xxxxxxxxxx`
   - For a group: use @RawDataBot inside the group
7. Add to your `.env`:
   ```
   TELEGRAM_TOKEN=7123456789:AAHxxxxx
   TELEGRAM_CHAT_ID=-100xxxxxxxxxx
   ```

---

## 🚢 Deploy to Railway (Backend)

1. Go to [railway.app](https://railway.app) → New Project
2. Connect your GitHub repo
3. Select the `backend/` folder as root
4. Set environment variables in Railway dashboard:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `CORS_ORIGINS=https://your-frontend.vercel.app`
5. Railway will detect `Procfile` and start the server automatically
6. Copy your Railway URL (e.g. `https://tradeflow-xxx.railway.app`)

---

## 🌐 Deploy to Vercel (Frontend)

1. Go to [vercel.com](https://vercel.com) → New Project
2. Connect your GitHub repo
3. Set **Root Directory** to `frontend`
4. Add environment variable:
   ```
   NEXT_PUBLIC_API_URL=https://your-backend.railway.app
   ```
5. Click Deploy!

---

## 🧪 Testing Signals

### Check if backend is working:
```bash
curl http://localhost:8000/health
# → {"status":"ok","service":"TradeFlow AI"}

curl http://localhost:8000/api/status
# → {"last_scan":"...","active_signals":0,"symbols_scanned":0,...}

curl http://localhost:8000/api/signals
# → [] initially, fills up after first scan (60s)
```

### Force a quick test scan:
The scanner starts automatically when the server starts.
Wait ~60 seconds for the first scan to complete.

### Test Telegram manually (Python):
```python
import httpx, asyncio
from app.services.telegram_alerts import send_telegram_alert

# Run from backend/ folder with venv active:
# python -c "import asyncio; ..."
```

---

## 📊 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/signals` | All active signals |
| GET | `/api/signals/{symbol}` | Signals for one coin |
| GET | `/api/top` | Top 10 by AI score |
| GET | `/api/status` | Scanner status |

### Query params for `/api/signals`:
- `active_only=true/false`
- `direction=LONG/SHORT/WATCH`
- `limit=50` (max 200)

---

## 📈 How Signals Work

The scanner runs every 60 seconds and checks the top 50 crypto pairs by volume on Bybit.

For each coin it calculates:
1. **Volume Spike** — current volume vs. 20-period rolling average
2. **Pump/Dump** — price change >3% in 1h
3. **Funding Rate** — |funding| > 0.1% triggers a signal
4. **Open Interest Change** — OI shift >5% adds to signal strength
5. **Squeeze Detection** — combines funding + price + OI for squeeze signals

Each signal gets an **AI Score (0–100)** based on all factors combined.

Trade levels are auto-calculated using ATR-based volatility:
- Entry zone: ±0.2% from current price
- Stop loss: based on recent volatility
- TP1/TP2/TP3: 1.5x / 2.5x / 4x the stop distance

---

## 💳 Freemium (Future)

The database already has a `users` table with `is_premium` and `subscription_expires` fields.

To add a paywall:
1. Add JWT auth middleware to FastAPI
2. In `/api/signals`, limit free users to top 5 signals
3. Add Stripe or crypto payment endpoint
4. Update frontend to show upgrade prompts

---

## 🔧 Troubleshooting

**Backend won't start:**
- Check Python version: `python3 --version` (need 3.11+)
- Activate venv: `source venv/bin/activate`

**No signals appearing:**
- Wait 60s for first scan
- Check logs: the scanner prints to stdout
- Try `curl http://localhost:8000/api/status` to see `last_scan`

**Telegram not sending:**
- Verify bot is admin in the channel
- Double-check `TELEGRAM_CHAT_ID` format (channels need `-100...`)
- Test with: `curl "https://api.telegram.org/bot{TOKEN}/getMe"`

**CORS errors in browser:**
- Add frontend URL to `CORS_ORIGINS` in backend `.env`
