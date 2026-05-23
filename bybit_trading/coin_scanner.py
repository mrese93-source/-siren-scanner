"""
Scans Bybit linear perpetuals and returns the top candidates by volume/OI.
"""

import logging
from typing import List

from .config import config
from .bybit_client import BybitClient

log = logging.getLogger("coin_scanner")


class CoinScanner:
    def __init__(self):
        self.client = BybitClient()

    def get_candidates(self) -> List[str]:
        tickers = self.client.get_tickers()
        if not tickers:
            log.warning("No tickers returned from Bybit")
            return []

        candidates = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue

            try:
                volume24h  = float(t.get("turnover24h", 0))
                oi         = float(t.get("openInterestValue", 0))
            except (ValueError, TypeError):
                continue

            if volume24h < config["min_volume_usdt_24h"]:
                continue
            if oi < config["min_oi_usdt"]:
                continue

            candidates.append((symbol, volume24h))

        # Sort by 24h volume descending and take top N
        candidates.sort(key=lambda x: x[1], reverse=True)
        top = [sym for sym, _ in candidates[: config["top_n_symbols"]]]
        log.info(f"Scanner found {len(top)} candidates (from {len(tickers)} tickers)")
        return top
