"""
Vectorised technical indicators — operates on plain Python lists (oldest-first).
No external TA library needed; uses only standard math.
"""

import math
from typing import List, Dict, Optional


def _ema(values: List[float], period: int) -> List[Optional[float]]:
    result = [None] * len(values)
    if len(values) < period:
        return result
    k = 2 / (period + 1)
    # seed with SMA
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        cur = values[i] * k + prev * (1 - k)
        result[i] = cur
        prev = cur
    return result


def _sma(values: List[float], period: int) -> List[Optional[float]]:
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _atr(candles: List[Dict], period: int = 14) -> List[Optional[float]]:
    trs = [None]
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    result = [None] * len(candles)
    if len(trs) < period + 1:
        return result

    # seed ATR with SMA of first `period` true ranges
    seed_vals = [t for t in trs[1 : period + 1] if t is not None]
    if len(seed_vals) < period:
        return result
    seed = sum(seed_vals) / period
    result[period] = seed
    prev = seed
    for i in range(period + 1, len(candles)):
        if trs[i] is None:
            result[i] = prev
            continue
        cur = (prev * (period - 1) + trs[i]) / period
        result[i] = cur
        prev = cur
    return result


def _rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    result = [None] * len(closes)
    if len(closes) < period + 1:
        return result

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(closes)):
        if i > period:
            g = gains[i - 1]
            l = losses[i - 1]
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period

        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100 - (100 / (1 + rs))

    return result


def _macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast   = _ema(closes, fast)
    ema_slow   = _ema(closes, slow)
    macd_line  = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    # signal line — EMA of macd_line (skip Nones for seeding)
    valid_indices = [i for i, v in enumerate(macd_line) if v is not None]
    sig_line = [None] * len(closes)
    if len(valid_indices) >= signal:
        start = valid_indices[0]
        sub = [macd_line[i] for i in range(start, len(macd_line)) if macd_line[i] is not None]
        sub_sig = _ema(sub, signal)
        # map back
        idx = 0
        for i in range(start, len(macd_line)):
            if macd_line[i] is not None:
                sig_line[i] = sub_sig[idx]
                idx += 1

    histogram = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, sig_line)
    ]
    return macd_line, sig_line, histogram


class TechnicalAnalysis:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def analyse(self, candles: List[Dict]) -> Optional[Dict]:
        """Return indicator snapshot for the latest candle, or None if not enough data."""
        if len(candles) < self.cfg["ema_slow"] + 10:
            return None

        closes  = [c["close"]  for c in candles]
        volumes = [c["volume"] for c in candles]

        ema_fast = _ema(closes, self.cfg["ema_fast"])
        ema_mid  = _ema(closes, self.cfg["ema_mid"])
        ema_slow = _ema(closes, self.cfg["ema_slow"])
        rsi      = _rsi(closes, self.cfg["rsi_period"])
        atr_vals = _atr(candles, self.cfg["atr_period"])
        vol_ma   = _sma(volumes, self.cfg["vol_ma_period"])
        macd_line, sig_line, histogram = _macd(
            closes,
            self.cfg["macd_fast"],
            self.cfg["macd_slow"],
            self.cfg["macd_signal"],
        )

        i = len(candles) - 1
        if any(v is None for v in [ema_fast[i], ema_mid[i], ema_slow[i], rsi[i], atr_vals[i]]):
            return None

        vol_spike = False
        if vol_ma[i] and vol_ma[i] > 0:
            vol_spike = volumes[i] >= vol_ma[i] * self.cfg["vol_spike_mult"]

        prev = i - 1
        macd_cross_up   = (macd_line[prev] is not None and sig_line[prev] is not None
                           and macd_line[i]  is not None and sig_line[i]  is not None
                           and macd_line[prev] < sig_line[prev]
                           and macd_line[i]   >= sig_line[i])
        macd_cross_down = (macd_line[prev] is not None and sig_line[prev] is not None
                           and macd_line[i]  is not None and sig_line[i]  is not None
                           and macd_line[prev] > sig_line[prev]
                           and macd_line[i]   <= sig_line[i])

        return {
            "close":          closes[i],
            "ema_fast":       ema_fast[i],
            "ema_mid":        ema_mid[i],
            "ema_slow":       ema_slow[i],
            "rsi":            rsi[i],
            "atr":            atr_vals[i],
            "macd":           macd_line[i],
            "macd_signal":    sig_line[i],
            "macd_hist":      histogram[i],
            "vol_spike":      vol_spike,
            "macd_cross_up":  macd_cross_up,
            "macd_cross_down": macd_cross_down,
            # trend flags
            "ema_bullish":    ema_fast[i] > ema_mid[i] > ema_slow[i],
            "ema_bearish":    ema_fast[i] < ema_mid[i] < ema_slow[i],
            "price_above_ema_slow": closes[i] > ema_slow[i],
            "price_below_ema_slow": closes[i] < ema_slow[i],
        }
