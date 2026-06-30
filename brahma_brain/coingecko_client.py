#!/usr/bin/env python3
"""
coingecko_client.py — CoinGecko免费API接入层 v1.0
设计院封印 · 2026-06-29

增益：
  - 代币市值排名（识别主流/山寨/妖币分类）
  - 全市场涨跌幅 → 板块热度（注入market_screener）
  - 零成本：CoinGecko免费tier 30次/min

接口：
  get_market_rank(symbol)  → rank, market_cap
  get_sector_heat()        → top_gainers, top_losers
  classify_token(symbol)   → BLUECHIP/LARGE/MID/SMALL/MICRO
"""
import urllib.request, json, time
from pathlib import Path

BASE = 'https://api.coingecko.com/api/v3'
_RANK_CACHE  = {}   # symbol→{rank,cap,ts}
_SECTOR_CACHE = {'data': None, 'ts': 0}
_RANK_TTL   = 3600   # 1h
_SECTOR_TTL = 1800   # 30min

# Binance symbol → CoinGecko id 映射（常用合约）
SYM_MAP = {
    'BTCUSDT': 'bitcoin',    'ETHUSDT': 'ethereum',
    'SOLUSDT': 'solana',     'BNBUSDT': 'binancecoin',
    'XRPUSDT': 'ripple',     'ADAUSDT': 'cardano',
    'DOGEUSDT': 'dogecoin',  'AVAXUSDT': 'avalanche-2',
    'LINKUSDT': 'chainlink', 'DOTUSDT': 'polkadot',
    'NEARUSDT': 'near',      'UNIUSDT': 'uniswap',
    'AAVEUSDT': 'aave',      'FETUSDT': 'fetch-ai',
    'RENDERUSDT': 'render-token', 'SANDUSDT': 'the-sandbox',
    'AXSUSDT': 'axie-infinity', 'GALAUSDT': 'gala',
    'GRTUSDT': 'the-graph',  'ARBUSDT': 'arbitrum',
    'OPUSDT': 'optimism',    'SUIUSDT': 'sui',
    'APTUSDT': 'aptos',      'SHIBUSDT': 'shiba-inu',
    '1000PEPEUSDT': 'pepe',  'LDOUSDT': 'lido-dao',
}

# 市值分级（USD）
TIER_MAP = [
    (100e9, 'BLUECHIP'),   # >1000亿
    (10e9,  'LARGE'),      # >100亿
    (1e9,   'MID'),        # >10亿
    (100e6, 'SMALL'),      # >1亿
    (0,     'MICRO'),      # <1亿（妖币区）
]


def _get(url: str, timeout: int = 6) -> dict:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def get_market_rank(symbol: str) -> dict:
    """获取代币市值排名和市值"""
    now = time.time()
    cached = _RANK_CACHE.get(symbol)
    if cached and now - cached['ts'] < _RANK_TTL:
        return cached

    cg_id = SYM_MAP.get(symbol)
    if not cg_id:
        return {'rank': 9999, 'market_cap': 0, 'tier': 'UNKNOWN', 'source': 'none'}

    data = _get(f'{BASE}/coins/{cg_id}?localization=false&tickers=false&community_data=false&developer_data=false')
    if not data:
        return {'rank': 9999, 'market_cap': 0, 'tier': 'UNKNOWN', 'source': 'cg_fail'}

    rank = data.get('market_cap_rank') or 9999
    cap  = data.get('market_data', {}).get('market_cap', {}).get('usd', 0) or 0

    tier = 'MICRO'
    for threshold, name in TIER_MAP:
        if cap >= threshold:
            tier = name
            break

    result = {'rank': rank, 'market_cap': cap, 'tier': tier, 'cg_id': cg_id,
              'source': 'coingecko', 'ts': now}
    _RANK_CACHE[symbol] = result
    return result


def classify_token(symbol: str) -> str:
    """快速分类代币（BLUECHIP/LARGE/MID/SMALL/MICRO）"""
    return get_market_rank(symbol).get('tier', 'UNKNOWN')


def get_sector_heat(limit: int = 50) -> dict:
    """全市场涨跌幅分布 → 板块热度"""
    global _SECTOR_CACHE
    now = time.time()
    if _SECTOR_CACHE['data'] and now - _SECTOR_CACHE['ts'] < _SECTOR_TTL:
        return _SECTOR_CACHE['data']

    data = _get(f'{BASE}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={limit}&page=1&price_change_percentage=24h')
    if not data:
        return {}

    gainers = sorted(data, key=lambda x: x.get('price_change_percentage_24h', 0) or 0, reverse=True)[:5]
    losers  = sorted(data, key=lambda x: x.get('price_change_percentage_24h', 0) or 0)[:5]
    avg_change = sum((c.get('price_change_percentage_24h') or 0) for c in data) / max(len(data), 1)

    result = {
        'avg_change_24h': round(avg_change, 2),
        'market_mood':    'BULL' if avg_change > 2 else 'BEAR' if avg_change < -2 else 'NEUTRAL',
        'top_gainers':    [{'symbol': c['symbol'].upper()+'USDT', 'pct': round(c.get('price_change_percentage_24h') or 0, 1)} for c in gainers],
        'top_losers':     [{'symbol': c['symbol'].upper()+'USDT', 'pct': round(c.get('price_change_percentage_24h') or 0, 1)} for c in losers],
        'ts': now,
        'source': 'coingecko',
    }
    _SECTOR_CACHE = {'data': result, 'ts': now}
    return result


if __name__ == '__main__':
    print('=== CoinGecko接入测试 ===')
    r = get_market_rank('RENDERUSDT')
    print(f'RENDERUSDT: rank={r["rank"]} cap=${r["market_cap"]/1e6:.0f}M tier={r["tier"]}')
    r2 = get_market_rank('BTCUSDT')
    print(f'BTCUSDT: rank={r2["rank"]} cap=${r2["market_cap"]/1e9:.0f}B tier={r2["tier"]}')
    heat = get_sector_heat()
    print(f'市场情绪: {heat.get("market_mood")} avg_24h={heat.get("avg_change_24h")}%')
    print(f'领涨: {heat.get("top_gainers", [])[:3]}')
