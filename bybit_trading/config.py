import os

config = {
    # Bybit Demo API credentials (testnet)
    "api_key":    os.environ.get("BYBIT_API_KEY", ""),
    "api_secret": os.environ.get("BYBIT_API_SECRET", ""),
    "demo_mode":  True,   # True = demo/testnet, False = live
    "testnet_url": "https://api-demo.bybit.com",
    "live_url":    "https://api.bybit.com",

    # Telegram notifications
    "telegram_token":   os.environ.get("BYBIT_TG_TOKEN", ""),
    "telegram_chat_id": os.environ.get("BYBIT_TG_CHAT_ID", ""),

    # Scanner settings
    "top_n_symbols":       50,     # how many symbols to scan
    "min_volume_usdt_24h": 5_000_000,
    "min_oi_usdt":         1_000_000,

    # Strategy timeframes
    "timeframes": ["60", "240", "D", "W"],   # 1H, 4H, Daily, Weekly (Bybit kline intervals)
    "primary_tf": "240",                     # 4H for entries
    "trend_tf":   "D",                       # Daily for trend

    # Technical analysis parameters
    "ema_fast":   9,
    "ema_mid":    21,
    "ema_slow":   55,
    "rsi_period": 14,
    "rsi_ob":     70,
    "rsi_os":     30,
    "macd_fast":  12,
    "macd_slow":  26,
    "macd_signal": 9,
    "vol_ma_period": 20,
    "vol_spike_mult": 1.8,

    # Risk management
    "risk_pct_per_trade":   1.0,   # % of balance per trade
    "max_open_trades":      5,
    "sl_atr_mult":          1.5,
    "tp_rr_ratio":          2.0,   # reward:risk ratio
    "atr_period":           14,

    # Bot timing
    "scan_interval_sec":    60,    # scan every 60 seconds
    "candle_limit":         200,   # candles per timeframe for analysis
}
