from dataclasses import dataclass, field
from typing import Optional
from .ai_scorer import compute_ai_score, compute_risk_level


VOLUME_SPIKE_THRESHOLD = 2.5   # current volume > 2.5x average
PUMP_THRESHOLD_1H = 3.0        # >3% in 1h = pump
DUMP_THRESHOLD_1H = -3.0       # <-3% in 1h = dump
FUNDING_EXTREME = 0.001        # |funding| > 0.1% = extreme
OI_CHANGE_THRESHOLD = 5.0      # >5% OI change


@dataclass
class SignalData:
    symbol: str
    price: float
    direction: str            # LONG / SHORT / WATCH
    signal_type: str
    confidence: float
    ai_score: float
    risk_level: str
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward: float
    change_1m: float = 0.0
    change_5m: float = 0.0
    change_15m: float = 0.0
    change_1h: float = 0.0
    change_24h: float = 0.0
    volume_24h: float = 0.0
    volume_spike_ratio: float = 1.0
    open_interest: float = 0.0
    funding_rate: float = 0.0
    explanation: str = ""


def _build_long_levels(price: float, stop_pct: float, tp_ratios: tuple):
    sl = round(price * (1 - stop_pct / 100), 4)
    tp1 = round(price * (1 + tp_ratios[0] / 100), 4)
    tp2 = round(price * (1 + tp_ratios[1] / 100), 4)
    tp3 = round(price * (1 + tp_ratios[2] / 100), 4)
    rr = round(tp_ratios[1] / stop_pct, 2) if stop_pct else 0
    return sl, tp1, tp2, tp3, rr


def _build_short_levels(price: float, stop_pct: float, tp_ratios: tuple):
    sl = round(price * (1 + stop_pct / 100), 4)
    tp1 = round(price * (1 - tp_ratios[0] / 100), 4)
    tp2 = round(price * (1 - tp_ratios[1] / 100), 4)
    tp3 = round(price * (1 - tp_ratios[2] / 100), 4)
    rr = round(tp_ratios[1] / stop_pct, 2) if stop_pct else 0
    return sl, tp1, tp2, tp3, rr


def generate_signal(
    symbol: str,
    price: float,
    change_1m: float,
    change_5m: float,
    change_15m: float,
    change_1h: float,
    change_24h: float,
    volume_24h: float,
    avg_volume: float,
    open_interest: float,
    prev_open_interest: float,
    funding_rate: float,
) -> Optional[SignalData]:

    volume_spike_ratio = (volume_24h / avg_volume) if avg_volume > 0 else 1.0
    oi_change_pct = ((open_interest - prev_open_interest) / prev_open_interest * 100) if prev_open_interest > 0 else 0.0

    reasons = []
    direction = "WATCH"
    signal_type = "WATCH"
    confidence = 40.0

    # --- Volume Spike ---
    if volume_spike_ratio >= VOLUME_SPIKE_THRESHOLD:
        reasons.append(f"📊 Volume spike {volume_spike_ratio:.1f}x above average")
        confidence += 15

    # --- Pump / Dump ---
    if change_1h >= PUMP_THRESHOLD_1H:
        signal_type = "PUMP"
        direction = "LONG"
        reasons.append(f"🚀 Pump detected: +{change_1h:.2f}% in 1h")
        confidence += 20
    elif change_1h <= DUMP_THRESHOLD_1H:
        signal_type = "DUMP"
        direction = "SHORT"
        reasons.append(f"🔻 Dump detected: {change_1h:.2f}% in 1h")
        confidence += 20

    # --- Funding Rate Extreme ---
    if abs(funding_rate) >= FUNDING_EXTREME:
        if funding_rate > 0:
            reasons.append(f"💰 Extreme positive funding: {funding_rate*100:.4f}% → longs paying, SHORT bias")
            direction = "SHORT" if direction == "WATCH" else direction
            signal_type = "FUNDING" if signal_type == "WATCH" else signal_type
            confidence += 15
        else:
            reasons.append(f"💰 Extreme negative funding: {funding_rate*100:.4f}% → shorts paying, LONG bias")
            direction = "LONG" if direction == "WATCH" else direction
            signal_type = "FUNDING" if signal_type == "WATCH" else signal_type
            confidence += 15

    # --- OI Change ---
    if abs(oi_change_pct) >= OI_CHANGE_THRESHOLD:
        if oi_change_pct > 0 and direction in ("LONG", "WATCH"):
            reasons.append(f"📈 Open Interest +{oi_change_pct:.1f}% (new longs entering)")
            signal_type = "OI_CHANGE" if signal_type == "WATCH" else signal_type
            direction = "LONG" if direction == "WATCH" else direction
            confidence += 10
        elif oi_change_pct < 0 and direction in ("SHORT", "WATCH"):
            reasons.append(f"📉 Open Interest {oi_change_pct:.1f}% (shorts closing / squeeze risk)")
            signal_type = "OI_CHANGE" if signal_type == "WATCH" else signal_type
            confidence += 10

    # --- Squeeze detection ---
    if funding_rate > FUNDING_EXTREME and change_1h > 2 and oi_change_pct > 3:
        signal_type = "SHORT_SQUEEZE"
        direction = "LONG"
        reasons.append("⚡ Short Squeeze: high positive funding + rising price + OI increase")
        confidence += 15
    elif funding_rate < -FUNDING_EXTREME and change_1h < -2 and oi_change_pct > 3:
        signal_type = "LONG_SQUEEZE"
        direction = "SHORT"
        reasons.append("⚡ Long Squeeze: negative funding + falling price + OI increase")
        confidence += 15

    # Require at least one strong reason
    if signal_type == "WATCH" and volume_spike_ratio < VOLUME_SPIKE_THRESHOLD:
        return None

    confidence = min(confidence, 95.0)

    # Build trade levels
    volatility = max(abs(change_1h), 1.0)
    stop_pct = max(1.0, volatility * 0.5)
    tp_ratios = (stop_pct * 1.5, stop_pct * 2.5, stop_pct * 4.0)

    if direction == "LONG":
        sl, tp1, tp2, tp3, rr = _build_long_levels(price, stop_pct, tp_ratios)
        entry_low = round(price * 0.998, 4)
        entry_high = round(price * 1.002, 4)
    elif direction == "SHORT":
        sl, tp1, tp2, tp3, rr = _build_short_levels(price, stop_pct, tp_ratios)
        entry_low = round(price * 0.998, 4)
        entry_high = round(price * 1.002, 4)
    else:
        sl = round(price * 0.97, 4)
        tp1, tp2, tp3 = round(price * 1.02, 4), round(price * 1.04, 4), round(price * 1.06, 4)
        entry_low, entry_high = round(price * 0.998, 4), round(price * 1.002, 4)
        rr = 2.0

    ai_score = compute_ai_score(
        volume_spike_ratio, change_1h, change_24h,
        funding_rate, oi_change_pct, confidence
    )
    risk_level = compute_risk_level(stop_pct, confidence)

    explanation = " | ".join(reasons) if reasons else "General market activity detected"

    return SignalData(
        symbol=symbol,
        price=price,
        direction=direction,
        signal_type=signal_type,
        confidence=confidence,
        ai_score=ai_score,
        risk_level=risk_level,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
        take_profit_3=tp3,
        risk_reward=rr,
        change_1m=change_1m,
        change_5m=change_5m,
        change_15m=change_15m,
        change_1h=change_1h,
        change_24h=change_24h,
        volume_24h=volume_24h,
        volume_spike_ratio=volume_spike_ratio,
        open_interest=open_interest,
        funding_rate=funding_rate,
        explanation=explanation,
    )
