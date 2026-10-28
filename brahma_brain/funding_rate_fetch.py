"""
funding_rate_fetch.py — 资金费率按需拉取
[修复 2026-10-28 苏摩111] 补全缺失模块
"""
import json, urllib.request, time
from pathlib import Path

FAPI = 'https://fapi.binance.com'
DATA = Path(__file__).parent.parent / 'data'

def fetch_funding_rate(symbol: str) -> dict:
    """拉取最新资金费率，写入data/funding_rate_{symbol.lower()}.json"""
    sym = symbol.upper().replace('USDT','') + 'USDT'
    url = f'{FAPI}/fapi/v1/premiumIndex?symbol={sym}'
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            d = json.loads(r.read())
        result = {
            'symbol': sym,
            'fr': float(d.get('lastFundingRate', 0)),
            'mark_price': float(d.get('markPrice', 0)),
            'index_price': float(d.get('indexPrice', 0)),
            'ts': time.time(),
        }
        out = DATA / f'funding_rate_{sym.lower()}.json'
        out.write_text(json.dumps(result, ensure_ascii=False))
        return result
    except Exception as e:
        return {'error': str(e), 'ts': time.time()}

if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
    print(fetch_funding_rate(sym))
