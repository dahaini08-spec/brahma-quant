"""
brahma_v6/adapters/binance_client.py — BinanceClient with HMAC signing for USDS-M Futures
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import hashlib
import hmac
import time
import urllib.parse
from typing import Dict, Any, Optional

try:
    import requests as _requests_lib
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class BinanceAPIError(Exception):
    """Raised on Binance API errors."""
    def __init__(self, code: int, message: str, raw: Dict = None):
        self.code = code
        self.message = message
        self.raw = raw or {}
        super().__init__(f"Binance API Error {code}: {message}")


class BinanceClient:
    """
    Binance USDS-M Futures REST client with HMAC signing.
    Base URL: https://fapi.binance.com
    """

    BASE_URL = "https://fapi.binance.com"
    TEST_BASE_URL = "https://testnet.binancefuture.com"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        timeout: float = 10.0,
        recv_window: int = 5000,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.timeout = timeout
        self.recv_window = recv_window
        self._base_url = self.TEST_BASE_URL if testnet else self.BASE_URL

    def _sign(self, params: Dict) -> Dict:
        """Add HMAC signature to params dict."""
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.recv_window
        query = urllib.parse.urlencode(params)
        sig = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = sig
        return params

    def _headers(self) -> Dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key, "Content-Type": "application/x-www-form-urlencoded"}

    def _get(self, path: str, params: Dict = None, signed: bool = True) -> Dict:
        """Perform signed GET request."""
        p = dict(params or {})
        if signed:
            p = self._sign(p)
        url = f"{self._base_url}{path}"
        if not HAS_REQUESTS:
            raise RuntimeError("requests library not available")
        resp = _requests_lib.get(url, params=p, headers=self._headers(), timeout=self.timeout)
        return self._handle_response(resp)

    def _post(self, path: str, params: Dict = None, signed: bool = True) -> Dict:
        """Perform signed POST request."""
        p = dict(params or {})
        if signed:
            p = self._sign(p)
        url = f"{self._base_url}{path}"
        if not HAS_REQUESTS:
            raise RuntimeError("requests library not available")
        resp = _requests_lib.post(url, data=p, headers=self._headers(), timeout=self.timeout)
        return self._handle_response(resp)

    def _delete(self, path: str, params: Dict = None, signed: bool = True) -> Dict:
        """Perform signed DELETE request."""
        p = dict(params or {})
        if signed:
            p = self._sign(p)
        url = f"{self._base_url}{path}"
        if not HAS_REQUESTS:
            raise RuntimeError("requests library not available")
        resp = _requests_lib.delete(url, params=p, headers=self._headers(), timeout=self.timeout)
        return self._handle_response(resp)

    def _handle_response(self, resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {"msg": resp.text}
        if resp.status_code >= 400:
            code = data.get("code", resp.status_code)
            msg = data.get("msg", "Unknown error")
            raise BinanceAPIError(code=code, message=msg, raw=data)
        return data

    # ── Order endpoints ───────────────────────────────────────────────────

    def new_order_test(self, params: Dict) -> Dict:
        """POST /fapi/v1/order/test — test order (no real execution)."""
        return self._post("/fapi/v1/order/test", params)

    def new_order(self, params: Dict) -> Dict:
        """POST /fapi/v1/order — place real order."""
        return self._post("/fapi/v1/order", params)

    def cancel_order(self, symbol: str, order_id: Optional[int] = None,
                     client_order_id: Optional[str] = None) -> Dict:
        """DELETE /fapi/v1/order — cancel order."""
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return self._delete("/fapi/v1/order", params)

    def get_order(self, symbol: str, order_id: Optional[int] = None,
                  client_order_id: Optional[str] = None) -> Dict:
        """GET /fapi/v1/order — query order status."""
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return self._get("/fapi/v1/order", params)

    def exchange_info(self) -> Dict:
        """GET /fapi/v1/exchangeInfo — symbol filters and rules."""
        return self._get("/fapi/v1/exchangeInfo", {}, signed=False)

    def account(self) -> Dict:
        """GET /fapi/v2/account — account balance and positions."""
        return self._get("/fapi/v2/account", {})

    def position_risk(self, symbol: Optional[str] = None) -> Dict:
        """GET /fapi/v2/positionRisk — position info."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._get("/fapi/v2/positionRisk", params)
