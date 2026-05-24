import httpx
from datetime import datetime
from ..config import settings


async def send_telegram_alert(signal) -> bool:
    """Send a formatted signal alert to Telegram."""
    if not settings.telegram_token or not settings.telegram_chat_id:
        return False

    direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "WATCH": "🟡"}.get(signal.direction, "⚪")
    risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(signal.risk_level, "⚪")
    type_emoji = {
        "PUMP": "🚀", "DUMP": "🔻", "VOLUME_SPIKE": "📊",
        "FUNDING": "💰", "OI_CHANGE": "📈",
        "SHORT_SQUEEZE": "⚡", "LONG_SQUEEZE": "⚡", "WATCH": "👀",
    }.get(signal.signal_type, "📊")

    message = (
        f"{type_emoji} <b>TradeFlow AI Signal</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{signal.symbol}</b>  {direction_emoji} <b>{signal.direction}</b>\n"
        f"💵 Price: <code>${signal.price:,.4f}</code>\n"
        f"🧠 AI Score: <b>{signal.ai_score:.0f}/100</b>  |  Confidence: <b>{signal.confidence:.0f}%</b>\n"
        f"⚠️ Risk: {risk_emoji} {signal.risk_level}\n\n"
        f"📐 <b>Trade Setup</b>\n"
        f"  Entry:  <code>${signal.entry_low:,.4f} – ${signal.entry_high:,.4f}</code>\n"
        f"  SL:     <code>${signal.stop_loss:,.4f}</code>\n"
        f"  TP1:    <code>${signal.take_profit_1:,.4f}</code>\n"
        f"  TP2:    <code>${signal.take_profit_2:,.4f}</code>\n"
        f"  TP3:    <code>${signal.take_profit_3:,.4f}</code>\n"
        f"  R/R:    <b>{signal.risk_reward:.1f}x</b>\n\n"
        f"📊 <b>Market Data</b>\n"
        f"  1m: {_fmt_pct(signal.change_1m)}  5m: {_fmt_pct(signal.change_5m)}  15m: {_fmt_pct(signal.change_15m)}\n"
        f"  1h: {_fmt_pct(signal.change_1h)}  24h: {_fmt_pct(signal.change_24h)}\n"
        f"  Vol spike: <b>{signal.volume_spike_ratio:.1f}x</b>\n"
        f"  Funding: <code>{signal.funding_rate*100:.4f}%</code>\n\n"
        f"💬 {signal.explanation}\n\n"
        f"🕐 {datetime.utcnow().strftime('%H:%M UTC')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>TradeFlow AI — Not financial advice</i>"
    )

    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception:
        return False


def _fmt_pct(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"
