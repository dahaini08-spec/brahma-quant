#!/usr/bin/env python3
# price_gate.py - 梵天实时价格门控
# 苏摩111封印 2026-07-18 · 任何分析/发帖/决策必须调用此模块

import subprocess, json, time

_CACHE = {}
_CACHE_TTL = 30  # 秒，超过30秒强制重新拉取

def get_realtime_price(symbol: str) -> float:
    now = time.time()
    if symbol in _CACHE:
        price, ts = _CACHE[symbol]
        if now - ts < _CACHE_TTL:
            return price
    r = subprocess.run(
        ["binance-cli","futures-usds","symbol-price-ticker","--symbol",symbol],
        capture_output=True, text=True, timeout=8
    )
    d = json.loads(r.stdout)
    price = float(d["price"])
    _CACHE[symbol] = (price, now)
    return price

def get_btc_eth() -> dict:
    return {
        "BTC": get_realtime_price("BTCUSDT"),
        "ETH": get_realtime_price("ETHUSDT"),
    }

if __name__ == "__main__":
    prices = get_btc_eth()
    print(f"BTC={prices['BTC']} ETH={prices['ETH']}")
