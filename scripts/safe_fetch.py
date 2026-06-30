#!/usr/bin/env python3
"""
safe_fetch.py — 统一HTTP安全封装 v1.0
设计院 2026-06-06

消灭62个无超时urlopen调用，统一处理：
  - 超时保护(默认8秒)
  - 自动重试(默认2次)
  - 错误收集
用法：
  from safe_fetch import fetch_json, fetch_price
  data = fetch_json('https://...')
  price = fetch_price('BTCUSDT')
"""
import json, time, urllib.request, urllib.error
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

BINANCE_FAPI = 'https://fapi.binance.com/fapi/v1'
BINANCE_API  = 'https://api.binance.com/api/v3'
DEFAULT_TIMEOUT = 8
DEFAULT_RETRIES = 2

def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT,
               retries: int = DEFAULT_RETRIES,
               module: str = 'safe_fetch') -> dict | None:
    """安全拉取JSON，含超时+重试+错误收集"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1 * (attempt + 1))
    # 记录错误
    try:
        from error_collector import log_error
        log_error(module, last_err, context=url[:100])
    except:
        pass
    return None

def fetch_price(symbol: str, timeout: int = 6) -> float | None:
    """获取合约现价，失败返回None"""
    url = f'{BINANCE_FAPI}/ticker/price?symbol={symbol}'
    data = fetch_json(url, timeout=timeout, module='fetch_price')
    if data:
        try: return float(data['price'])
        except: pass
    # fallback: 现货
    url2 = f'{BINANCE_API}/ticker/price?symbol={symbol}'
    data2 = fetch_json(url2, timeout=timeout, module='fetch_price_spot')
    if data2:
        try: return float(data2['price'])
        except: pass
    return None

def fetch_ticker_24h(symbol: str, timeout: int = 8) -> dict | None:
    """获取24H ticker数据"""
    url = f'{BINANCE_FAPI}/ticker/24hr?symbol={symbol}'
    return fetch_json(url, timeout=timeout, module='fetch_ticker_24h')

def fetch_klines(symbol: str, interval: str = '1h',
                 limit: int = 100, timeout: int = 10) -> list | None:
    """获取K线数据"""
    url = f'{BINANCE_FAPI}/klines?symbol={symbol}&interval={interval}&limit={limit}'
    return fetch_json(url, timeout=timeout, module='fetch_klines')

def fetch_funding_rate(symbol: str, timeout: int = 6) -> float | None:
    """获取资金费率"""
    url = f'{BINANCE_FAPI}/premiumIndex?symbol={symbol}'
    data = fetch_json(url, timeout=timeout, module='fetch_funding_rate')
    if data:
        try: return float(data['lastFundingRate'])
        except: pass
    return None

def fetch_multi_prices(symbols: list, timeout: int = 8) -> dict:
    """批量获取价格"""
    url = f'{BINANCE_FAPI}/ticker/price'
    data = fetch_json(url, timeout=timeout, module='fetch_multi_prices')
    if not data: return {}
    prices = {}
    for item in (data if isinstance(data, list) else [data]):
        sym = item.get('symbol','')
        if sym in symbols:
            try: prices[sym] = float(item['price'])
            except: pass
    return prices

if __name__ == '__main__':
    btc = fetch_price('BTCUSDT')
    eth = fetch_price('ETHUSDT')
    print(f'BTC=${btc:,.0f} ETH=${eth:.2f}')
