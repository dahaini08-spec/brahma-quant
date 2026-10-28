"""
orderbook_fetch.py — 盘口深度按需拉取
[修复 2026-10-28 苏摩111] 补全缺失模块
"""
import json, urllib.request, time
from pathlib import Path

FAPI = 'https://fapi.binance.com'
DATA = Path(__file__).parent.parent / 'data'

def fetch_orderbook_snapshot(symbol: str, limit: int = 20) -> dict:
    """拉取盘口快照，写入data/orderbook_{symbol.lower()}.json"""
    sym = symbol.upper().replace('USDT','') + 'USDT'
    url = f'{FAPI}/fapi/v1/depth?symbol={sym}&limit={limit}'
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            d = json.loads(r.read())
        bids = [[float(p), float(q)] for p,q in d.get('bids', [])[:5]]
        asks = [[float(p), float(q)] for p,q in d.get('asks', [])[:5]]
        result = {
            'symbol': sym,
            'bids': bids,  # [[price, qty], ...]
            'asks': asks,
            'bid1': bids[0][0] if bids else 0,
            'ask1': asks[0][0] if asks else 0,
            'spread': asks[0][0] - bids[0][0] if bids and asks else 0,
            'ts': time.time(),
        }
        out = DATA / f'orderbook_{sym.lower()}.json'
        out.write_text(json.dumps(result, ensure_ascii=False))
        return result
    except Exception as e:
        return {'error': str(e), 'ts': time.time()}

if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
    print(fetch_orderbook_snapshot(sym))
