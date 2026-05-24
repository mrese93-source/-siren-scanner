"""
Background scanner: runs every N seconds, fetches market data from Bybit,
generates signals, saves to DB, sends Telegram alerts.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Signal, AlertLog
from ..config import settings
from .bybit_client import get_tickers, get_klines, get_open_interest, get_funding_rate
from .signal_generator import generate_signal
from .telegram_alerts import send_telegram_alert

log = logging.getLogger("scanner")

# In-memory state
_avg_volume_cache: dict[str, float] = {}
_oi_cache: dict[str, float] = {}
_alert_cooldown: dict[str, datetime] = {}
_last_scan: datetime | None = None
_symbols_scanned: int = 0

# Top N symbols by volume to scan
TOP_N = 50
MIN_VOLUME_USD = 10_000_000  # skip tiny symbols


async def run_scan():
    global _last_scan, _symbols_scanned

    log.info("Starting market scan…")
    db: Session = SessionLocal()
    try:
        tickers = await get_tickers()
        # Filter: perpetuals with decent volume
        perps = [
            t for t in tickers
            if t.get("symbol", "").endswith("USDT")
            and float(t.get("turnover24h", 0)) >= MIN_VOLUME_USD
        ]
        # Sort by 24h turnover descending, take top N
        perps.sort(key=lambda t: float(t.get("turnover24h", 0)), reverse=True)
        top = perps[:TOP_N]
        _symbols_scanned = len(top)

        tasks = [_process_symbol(t, db) for t in top]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Deactivate old signals (older than 4h)
        cutoff = datetime.utcnow() - timedelta(hours=4)
        db.query(Signal).filter(Signal.created_at < cutoff, Signal.is_active == True).update(
            {"is_active": False}
        )
        db.commit()

    except Exception as e:
        log.error(f"Scan error: {e}")
    finally:
        db.close()

    _last_scan = datetime.utcnow()
    log.info(f"Scan complete. Scanned {_symbols_scanned} symbols.")


async def _process_symbol(ticker: dict, db: Session):
    symbol = ticker.get("symbol", "")
    if not symbol:
        return

    try:
        price = float(ticker.get("lastPrice", 0))
        if price <= 0:
            return

        change_24h = float(ticker.get("price24hPcnt", 0)) * 100
        volume_24h = float(ticker.get("turnover24h", 0))

        # Rolling average volume (simple EMA update)
        prev_avg = _avg_volume_cache.get(symbol, volume_24h)
        avg_volume = prev_avg * 0.8 + volume_24h * 0.2
        _avg_volume_cache[symbol] = avg_volume

        # Get multi-timeframe klines concurrently
        klines_1m, klines_5m, klines_15m, klines_1h, funding, oi_now = await asyncio.gather(
            get_klines(symbol, "1", 2),
            get_klines(symbol, "5", 2),
            get_klines(symbol, "15", 2),
            get_klines(symbol, "60", 2),
            get_funding_rate(symbol),
            get_open_interest(symbol),
            return_exceptions=True,
        )

        change_1m  = _pct_change(klines_1m)
        change_5m  = _pct_change(klines_5m)
        change_15m = _pct_change(klines_15m)
        change_1h  = _pct_change(klines_1h)
        funding_rate = funding if isinstance(funding, float) else 0.0
        open_interest = oi_now if isinstance(oi_now, float) else 0.0
        prev_oi = _oi_cache.get(symbol, open_interest)
        _oi_cache[symbol] = open_interest

        signal_data = generate_signal(
            symbol=symbol,
            price=price,
            change_1m=change_1m,
            change_5m=change_5m,
            change_15m=change_15m,
            change_1h=change_1h,
            change_24h=change_24h,
            volume_24h=volume_24h,
            avg_volume=avg_volume,
            open_interest=open_interest,
            prev_open_interest=prev_oi,
            funding_rate=funding_rate,
        )

        if signal_data is None:
            return

        # Save to DB
        db_signal = Signal(
            symbol=signal_data.symbol,
            direction=signal_data.direction,
            signal_type=signal_data.signal_type,
            confidence=signal_data.confidence,
            risk_level=signal_data.risk_level,
            ai_score=signal_data.ai_score,
            price=signal_data.price,
            entry_low=signal_data.entry_low,
            entry_high=signal_data.entry_high,
            stop_loss=signal_data.stop_loss,
            take_profit_1=signal_data.take_profit_1,
            take_profit_2=signal_data.take_profit_2,
            take_profit_3=signal_data.take_profit_3,
            risk_reward=signal_data.risk_reward,
            change_1m=signal_data.change_1m,
            change_5m=signal_data.change_5m,
            change_15m=signal_data.change_15m,
            change_1h=signal_data.change_1h,
            change_24h=signal_data.change_24h,
            volume_24h=signal_data.volume_24h,
            volume_spike_ratio=signal_data.volume_spike_ratio,
            open_interest=signal_data.open_interest,
            funding_rate=signal_data.funding_rate,
            explanation=signal_data.explanation,
            is_active=True,
            alert_sent=False,
        )
        db.add(db_signal)
        db.flush()

        # Send Telegram alert with cooldown
        now = datetime.utcnow()
        last_alert = _alert_cooldown.get(symbol)
        cooldown_secs = settings.alert_cooldown_seconds
        if last_alert is None or (now - last_alert).total_seconds() >= cooldown_secs:
            sent = await send_telegram_alert(signal_data)
            db_signal.alert_sent = sent
            if sent:
                _alert_cooldown[symbol] = now
                log.info(f"Alert sent: {symbol} {signal_data.direction}")
                alert_log = AlertLog(
                    symbol=symbol,
                    signal_type=signal_data.signal_type,
                    message=signal_data.explanation,
                    success=True,
                )
                db.add(alert_log)

        db.commit()

    except Exception as e:
        log.warning(f"Error processing {symbol}: {e}")
        db.rollback()


def _pct_change(klines) -> float:
    if isinstance(klines, Exception) or not isinstance(klines, list) or len(klines) < 2:
        return 0.0
    prev = klines[-1]["close"]
    curr = klines[0]["close"]
    return round((curr - prev) / prev * 100, 4) if prev else 0.0


async def scanner_loop():
    """Infinite loop that runs the scanner every N seconds."""
    while True:
        try:
            await run_scan()
        except Exception as e:
            log.error(f"Scanner loop error: {e}")
        await asyncio.sleep(settings.scan_interval_seconds)


def get_scanner_status() -> dict:
    from ..database import SessionLocal
    from ..models import Signal
    db = SessionLocal()
    try:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        total_today = db.query(Signal).filter(Signal.created_at >= today).count()
        active = db.query(Signal).filter(Signal.is_active == True).count()
    finally:
        db.close()
    return {
        "last_scan": _last_scan,
        "total_signals_today": total_today,
        "active_signals": active,
        "symbols_scanned": _symbols_scanned,
    }
