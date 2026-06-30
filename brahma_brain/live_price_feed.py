#!/usr/bin/env python3
"""
live_price_feed.py — 梵天实时价格中枢 v1.0
设计院 2026-05-29

【单一价格真相源架构】
问题：
  REST K线(TTL=120s) 和 ticker(TTL=15s) 是两套数据源，
  分析层 price = k1h['c'][-1]，永远滞后 10~120s

解法：
  1. 写入层：ws_guardian markPrice 更新时 → 写 /tmp/live_prices.json
  2. 读取层：brahma_brain 分析前 → 先读 live_prices.json
  3. 降级链：WS实时价 → ticker.lastPrice → k1h[-1]

TTL：
  WS价格 30s内有效（ws_guardian心跳每10s）
  ticker 15s内有效
  k1h[-1] 兜底（指标用，不做当前价）
"""

import json, time, os
from pathlib import Path

LIVE_PRICE_FILE = Path('/tmp/live_prices.json')
WS_PRICE_TTL    = 30   # WS价格有效期（秒）
TICKER_TTL      = 15   # ticker有效期（秒）

# ─── 写入（ws_guardian 调用）────────────────────────────────────

def update_price(symbol: str, price: float, source: str = 'ws'):
    """ws_guardian 每次收到 markPrice 时调用"""
    try:
        data = {}
        if LIVE_PRICE_FILE.exists():
            try:
                data = json.loads(LIVE_PRICE_FILE.read_text())
            except Exception:
                data = {}
        data[symbol.upper()] = {
            'price':  price,
            'ts':     time.time(),
            'source': source,
        }
        LIVE_PRICE_FILE.write_text(json.dumps(data))
    except Exception:
        pass  # 写失败不影响主流程


# ─── 读取（brahma_brain 分析时调用）────────────────────────────

def get_live_price(symbol: str) -> float:
    """
    读取最新实时价格。
    降级链：WS文件（30s内）→ 0（让调用方降级到ticker）
    """
    try:
        if not LIVE_PRICE_FILE.exists():
            return 0.0
        data = json.loads(LIVE_PRICE_FILE.read_text())
        entry = data.get(symbol.upper(), {})
        if not entry:
            return 0.0
        age = time.time() - entry.get('ts', 0)
        if age > WS_PRICE_TTL:
            return 0.0   # 超时，不用旧WS价格
        return float(entry['price'])
    except Exception:
        return 0.0


def get_best_price(symbol: str, ticker: dict = None, kline_close: float = 0) -> tuple:
    """
    返回最优价格 + 来源标注
    优先级: WS实时(30s) > ticker.lastPrice(15s) > k1h[-1]
    """
    # 1. WS实时
    ws_price = get_live_price(symbol)
    if ws_price > 0:
        return ws_price, 'ws_realtime'

    # 2. ticker.lastPrice
    if ticker:
        tp = float(ticker.get('lastPrice', 0) or ticker.get('price', 0) or 0)
        if tp > 0:
            return tp, 'ticker_rest'

    # 3. k1h 收盘价（兜底）
    if kline_close > 0:
        return kline_close, 'kline_close'

    return 0.0, 'unknown'


# ─── 批量写入（初始化时拉取多标的价格）────────────────────────

def bulk_update_from_api(symbols: list):
    """
    批量从 REST API 更新价格（ws_guardian 启动时或空仓时调用）
    """
    import urllib.request
    try:
        url = 'https://fapi.binance.com/fapi/v1/ticker/price'
        raw = json.loads(urllib.request.urlopen(url, timeout=8).read())
        price_map = {item['symbol']: float(item['price']) for item in raw}
        for sym in symbols:
            p = price_map.get(sym.upper(), 0)
            if p > 0:
                update_price(sym, p, source='bulk_rest')
    except Exception:
        pass


if __name__ == '__main__':
    # 测试
    import urllib.request
    print("测试实时价格中枢...")
    bulk_update_from_api(['ETHUSDT', 'BTCUSDT', 'SOLUSDT', 'DOGEUSDT'])
    for sym in ['ETHUSDT', 'BTCUSDT', 'SOLUSDT']:
        p, src = get_best_price(sym)
        print(f"  {sym}: ${p:,.4f}  来源={src}")
