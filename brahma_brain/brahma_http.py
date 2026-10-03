#!/usr/bin/env python3
"""
brahma_http.py — 梵天统一 HTTP 请求入口
[2026-10-03 苏摩111封印]

问题：urllib.request.urlopen 散落 115 处，无统一错误处理/超时/重试
方案：所有网络请求通过此模块，保持向后兼容

用法：
    from brahma_http import fetch, fetch_json
    data = fetch('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT')
    price = data['price']
"""
import json
import ssl
import time
import urllib.request
from typing import Any, Optional

_CTX = ssl.create_default_context()
_DEFAULT_TIMEOUT = 8
_RETRY_DELAYS = [0, 1, 2]   # 失败后重试间隔（秒）

def fetch(url: str, timeout: int = _DEFAULT_TIMEOUT, retries: int = 2) -> Any:
    """
    GET 请求并解析 JSON，带重试。
    失败时返回 None，不抛异常（保持现有调用方兼容性）。
    """
    last_exc = None
    for attempt, delay in enumerate(_RETRY_DELAYS[:retries + 1]):
        if delay > 0:
            time.sleep(delay)
        try:
            resp = urllib.request.urlopen(url, timeout=timeout, context=_CTX)
            return json.loads(resp.read())
        except Exception as e:
            last_exc = e
    return None  # 全部失败静默返回 None（兼容现有 .get() 调用）

def fetch_json(url: str, timeout: int = _DEFAULT_TIMEOUT) -> Any:
    """fetch 的别名，更明确语义"""
    return fetch(url, timeout=timeout)

def fetch_or_raise(url: str, timeout: int = _DEFAULT_TIMEOUT) -> Any:
    """严格版：失败时抛出异常"""
    resp = urllib.request.urlopen(url, timeout=timeout, context=_CTX)
    return json.loads(resp.read())

# ── 向后兼容：暴露底层 urlopen 和 ctx ──
urlopen = urllib.request.urlopen
ssl_ctx = _CTX
