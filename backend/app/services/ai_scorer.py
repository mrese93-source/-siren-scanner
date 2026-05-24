def compute_ai_score(
    volume_spike_ratio: float,
    change_1h: float,
    change_24h: float,
    funding_rate: float,
    open_interest_change: float,
    confidence: float,
) -> float:
    """
    Rule-based AI score 0-100.
    Higher = stronger / more interesting opportunity.
    """
    score = 0.0

    # Volume spike: 0-30 pts
    if volume_spike_ratio >= 5:
        score += 30
    elif volume_spike_ratio >= 3:
        score += 20
    elif volume_spike_ratio >= 2:
        score += 10

    # Hourly move: 0-20 pts
    abs_1h = abs(change_1h)
    if abs_1h >= 5:
        score += 20
    elif abs_1h >= 3:
        score += 14
    elif abs_1h >= 1.5:
        score += 8

    # 24h trend alignment: 0-15 pts
    abs_24h = abs(change_24h)
    if abs_24h >= 15:
        score += 15
    elif abs_24h >= 8:
        score += 10
    elif abs_24h >= 3:
        score += 5

    # Funding rate extremity: 0-20 pts
    abs_fr = abs(funding_rate * 100)  # convert to %
    if abs_fr >= 0.3:
        score += 20
    elif abs_fr >= 0.15:
        score += 12
    elif abs_fr >= 0.07:
        score += 6

    # OI change: 0-15 pts
    abs_oi = abs(open_interest_change)
    if abs_oi >= 10:
        score += 15
    elif abs_oi >= 5:
        score += 10
    elif abs_oi >= 2:
        score += 5

    # Confidence boost: scale remaining 0-100 → portion
    score = score * (0.5 + confidence / 200)

    return round(min(score, 100), 1)


def compute_risk_level(stop_loss_pct: float, confidence: float) -> str:
    if stop_loss_pct > 3 or confidence < 50:
        return "HIGH"
    elif stop_loss_pct > 1.5 or confidence < 70:
        return "MEDIUM"
    return "LOW"
