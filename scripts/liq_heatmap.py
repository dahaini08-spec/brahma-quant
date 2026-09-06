#!/usr/bin/env python3
"""
liq_heatmap.py — liquidation heatmap proxy from public depth.
Keys: environment only. No defaults.
"""
import sys
import os
import json
import time
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))
try:
    from brahma_bus import _SESS as _HTTP
except ImportError:
    import requests as _HTTP

KEY = os.environ.get('BINANCE_API_KEY', '')
SEC = os.environ.get('BINANCE_SECRET', '')
HDR = {'X-MBX-APIKEY': KEY}


def get_liq_heatmap(sym: str = 'BTCUSDT') -> dict:
    try:
        px = float(_HTTP.get(
            'https://fapi.binance.com/fapi/v1/ticker/price',
            params={'symbol': sym}, timeout=5
        ).json()['price'])
        depth = _HTTP.get(
            'https://fapi.binance.com/fapi/v1/depth',
            params={'symbol': sym, 'limit': 100}, timeout=8
        ).json()
        bids = [(float(p), float(q)) for p, q in depth.get('bids', [])]
        asks = [(float(p), float(q)) for p, q in depth.get('asks', [])]

        levers = [2, 3, 5, 10, 20, 50]
        short_liq = {lev: round(px * (1 + 1.0 / lev), 1) for lev in levers}
        long_liq = {lev: round(px * (1 - 1.0 / lev), 1) for lev in levers}

        bucket_size = px * 0.005
        ask_clusters = defaultdict(float)
        bid_clusters = defaultdict(float)
        for p, q in asks:
            b = round(p / bucket_size) * bucket_size
            ask_clusters[b] += q * p
        for p, q in bids:
            b = round(p / bucket_size) * bucket_size
            bid_clusters[b] += q * p

        top_ask_zones = sorted(ask_clusters.items(), key=lambda x: -x[1])[:3]
        top_bid_zones = sorted(bid_clusters.items(), key=lambda x: -x[1])[:3]

        nearest_short_liq = short_liq[50]
        nearest_long_liq = long_liq[50]
        dist_short = round((nearest_short_liq - px) / px * 100, 2)
        dist_long = round((px - nearest_long_liq) / px * 100, 2)

        liq_bull_score = liq_bear_score = 0
        if dist_short < 2.0:
            liq_bull_score += 8
        elif dist_short < 4.0:
            liq_bull_score += 5
        elif dist_short < 8.0:
            liq_bull_score += 3
        if dist_long < 2.0:
            liq_bear_score += 8
        elif dist_long < 4.0:
            liq_bear_score += 5
        elif dist_long < 8.0:
            liq_bear_score += 3

        result = {
            'symbol': sym,
            'price': px,
            'short_liq_map': short_liq,
            'long_liq_map': long_liq,
            'nearest_short_liq': nearest_short_liq,
            'nearest_long_liq': nearest_long_liq,
            'dist_to_short_liq': dist_short,
            'dist_to_long_liq': dist_long,
            'top_ask_clusters': [(round(p, 1), round(v, 0)) for p, v in top_ask_zones],
            'top_bid_clusters': [(round(p, 1), round(v, 0)) for p, v in top_bid_zones],
            'liq_bull_score': liq_bull_score,
            'liq_bear_score': liq_bear_score,
            'ts': time.time(),
        }
        cache = BASE / 'data' / f'liq_heatmap_{sym}.json'
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, indent=2))
        return result
    except Exception as e:
        return {'error': str(e), 'symbol': sym}


def format_report(r: dict) -> str:
    if 'error' in r:
        return f"liq_heatmap error: {r['error']}"
    return (
        f"liq {r['symbol']} px={r['price']} "
        f"short50={r['nearest_short_liq']} long50={r['nearest_long_liq']} "
        f"bull={r['liq_bull_score']} bear={r['liq_bear_score']}"
    )


if __name__ == '__main__':
    syms = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    for sym in syms:
        print(format_report(get_liq_heatmap(sym)))
