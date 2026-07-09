"""
brahma_v6/adapters/binance_user_stream.py — UserStreamManager for order update events
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import threading
import time
import json
from typing import Callable, Optional, Dict

try:
    import requests as _requests_lib
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class UserStreamManager:
    """
    Manages the Binance USDS-M Futures user data stream (listen key lifecycle).
    Handles keepalive every 30 minutes and registration of order update callbacks.
    """

    KEEPALIVE_INTERVAL = 1800  # 30 min in seconds
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._listen_key: Optional[str] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._order_update_callbacks: list = []
        self._lock = threading.Lock()

    def _headers(self) -> Dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    def start_listen_key(self) -> str:
        """POST /fapi/v1/listenKey — create a new listen key."""
        if not HAS_REQUESTS:
            raise RuntimeError("requests library not available")
        resp = _requests_lib.post(
            f"{self.BASE_URL}/fapi/v1/listenKey",
            headers=self._headers(),
            timeout=self.timeout,
        )
        data = resp.json()
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to start listen key: {data}")
        self._listen_key = data["listenKey"]
        self._start_keepalive_thread()
        return self._listen_key

    def keepalive_listen_key(self) -> bool:
        """PUT /fapi/v1/listenKey — extend listen key validity."""
        if not self._listen_key:
            return False
        if not HAS_REQUESTS:
            return False
        resp = _requests_lib.put(
            f"{self.BASE_URL}/fapi/v1/listenKey",
            headers=self._headers(),
            timeout=self.timeout,
        )
        return resp.status_code < 400

    def close_listen_key(self) -> bool:
        """DELETE /fapi/v1/listenKey — close the listen key."""
        self._stop_event.set()
        if not self._listen_key:
            return True
        if not HAS_REQUESTS:
            return True
        resp = _requests_lib.delete(
            f"{self.BASE_URL}/fapi/v1/listenKey",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._listen_key = None
        return resp.status_code < 400

    def register_order_update_callback(self, callback: Callable[[Dict], None]) -> None:
        """Register a callback that will be called with each ORDER_TRADE_UPDATE event."""
        with self._lock:
            self._order_update_callbacks.append(callback)

    def on_order_update(self, raw_event: Dict) -> None:
        """Dispatch an order update event to all registered callbacks."""
        with self._lock:
            callbacks = list(self._order_update_callbacks)
        for cb in callbacks:
            try:
                cb(raw_event)
            except Exception:
                pass

    def _start_keepalive_thread(self) -> None:
        """Start background thread to keep listen key alive."""
        self._stop_event.clear()

        def _keepalive_loop():
            while not self._stop_event.wait(timeout=self.KEEPALIVE_INTERVAL):
                self.keepalive_listen_key()

        self._keepalive_thread = threading.Thread(
            target=_keepalive_loop, daemon=True, name="user-stream-keepalive"
        )
        self._keepalive_thread.start()

    @property
    def listen_key(self) -> Optional[str]:
        return self._listen_key
