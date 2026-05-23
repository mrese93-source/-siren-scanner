import time
import hmac
import hashlib
import json
import requests
import logging

from .config import config

log = logging.getLogger("bybit_client")


class BybitClient:
    def __init__(self):
        self.api_key    = config["api_key"]
        self.api_secret = config["api_secret"]
        self.base_url   = config["testnet_url"] if config["demo_mode"] else config["live_url"]
        self.session    = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _sign(self, params: dict) -> dict:
        ts = str(int(time.time() * 1000))
        recv_window = "5000"
        param_str = ts + self.api_key + recv_window + "&".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY":     self.api_key,
            "X-BAPI-TIMESTAMP":   ts,
            "X-BAPI-SIGN":        signature,
            "X-BAPI-RECV-WINDOW": recv_window,
        }

    def _get(self, path: str, params: dict = None, auth: bool = False) -> dict:
        params = params or {}
        headers = self._sign(params) if auth else {}
        url = self.base_url + path
        try:
            r = self.session.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"GET {path} error: {e}")
            return {}

    def _post(self, path: str, body: dict) -> dict:
        headers = self._sign(body)
        url = self.base_url + path
        try:
            r = self.session.post(url, json=body, headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"POST {path} error: {e}")
            return {}

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_tickers(self) -> list:
        data = self._get("/v5/market/tickers", {"category": "linear"})
        return data.get("result", {}).get("list", [])

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        """Returns list of OHLCV dicts sorted oldest-first."""
        data = self._get("/v5/market/kline", {
            "category": "linear",
            "symbol":   symbol,
            "interval": interval,
            "limit":    limit,
        })
        raw = data.get("result", {}).get("list", [])
        # Bybit returns newest-first: [ts, open, high, low, close, volume, turnover]
        candles = []
        for c in reversed(raw):
            candles.append({
                "ts":     int(c[0]),
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            })
        return candles

    def get_orderbook(self, symbol: str) -> dict:
        data = self._get("/v5/market/orderbook", {"category": "linear", "symbol": symbol, "limit": 5})
        return data.get("result", {})

    # ------------------------------------------------------------------
    # Account & positions
    # ------------------------------------------------------------------

    def get_balance(self) -> float:
        data = self._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}, auth=True)
        try:
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == "USDT":
                    return float(c["walletBalance"])
        except Exception:
            pass
        return 0.0

    def get_positions(self) -> list:
        data = self._get("/v5/position/list", {"category": "linear", "settleCoin": "USDT"}, auth=True)
        return data.get("result", {}).get("list", [])

    def get_open_orders(self, symbol: str = "") -> list:
        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/v5/order/realtime", params, auth=True)
        return data.get("result", {}).get("list", [])

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        side: str,           # "Buy" or "Sell"
        qty: float,
        order_type: str = "Market",
        price: float = None,
        sl: float = None,
        tp: float = None,
        reduce_only: bool = False,
    ) -> dict:
        body = {
            "category":   "linear",
            "symbol":     symbol,
            "side":       side,
            "orderType":  order_type,
            "qty":        str(qty),
            "timeInForce": "GTC",
            "reduceOnly": reduce_only,
            "positionIdx": 0,  # one-way mode
        }
        if price:
            body["price"] = str(price)
        if sl:
            body["stopLoss"] = str(sl)
        if tp:
            body["takeProfit"] = str(tp)

        result = self._post("/v5/order/create", body)
        return result

    def close_position(self, symbol: str, side: str, qty: float) -> dict:
        close_side = "Sell" if side == "Buy" else "Buy"
        return self.place_order(symbol, close_side, qty, reduce_only=True)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._post("/v5/order/cancel", {
            "category": "linear",
            "symbol":   symbol,
            "orderId":  order_id,
        })

    # ------------------------------------------------------------------
    # Instrument info
    # ------------------------------------------------------------------

    def get_instrument_info(self, symbol: str) -> dict:
        data = self._get("/v5/market/instruments-info", {"category": "linear", "symbol": symbol})
        items = data.get("result", {}).get("list", [])
        return items[0] if items else {}
