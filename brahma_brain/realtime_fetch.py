"""
realtime_fetch.py — 梵天实时数据层
设计院 2026-05-28

原则：分析时强制拉取币安FAPI最新数据，不依赖缓存
并发拉取6个接口，总耗时 < 300ms

调用方式：
    from realtime_fetch import fetch_realtime
    data = fetch_realtime('LTCUSDT')
    # data['1h'], data['4h'], data['ticker'], data['fr'] ...
"""

import urllib.request, json, time, os
from concurrent.futures import ThreadPoolExecutor, as_completed

FAPI = 'https://fapi.binance.com'
TIMEOUT = 8

def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 BrahmaBrain/1.0'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())

def fetch_realtime(symbol: str, intervals: list = None) -> dict:
    """
    强制从币安FAPI拉取最新数据，并发执行，不使用任何缓存。
    
    返回 dict:
        '15m' / '1h' / '4h' / '1d' : K线列表 (list of raw kline)
        'ticker'  : {'price': float, 'priceChangePercent': str, ...}
        'fr'      : {'fundingRate': str, 'nextFundingTime': int}
        'oi'      : {'openInterest': str}
        'lsr'     : float  (多头占比%, 如 65.3)
        '_fetched_at': float  时间戳
        '_symbol': str
    """
    if intervals is None:
        intervals = ['15m', '1h', '4h', '1d']

    tasks = {}
    for iv in intervals:
        tasks[iv] = f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={iv}&limit=250'
    tasks['ticker'] = f'{FAPI}/fapi/v1/ticker/24hr?symbol={symbol}'
    tasks['fr']     = f'{FAPI}/fapi/v1/fundingRate?symbol={symbol}&limit=1'
    tasks['oi']     = f'{FAPI}/fapi/v1/openInterest?symbol={symbol}'
    tasks['lsr']    = f'{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=1'

    result = {'_symbol': symbol, '_fetched_at': time.time(), '_errors': {}}

    def _fetch_task(key, url):
        try:
            return key, _get(url), None
        except Exception as e:
            return key, None, str(e)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_task, k, v): k for k, v in tasks.items()}
        for f in as_completed(futs):
            key, data, err = f.result()
            if err:
                result['_errors'][key] = err
            else:
                result[key] = data

    # 标准化 ticker
    if 'ticker' in result and isinstance(result['ticker'], dict):
        t = result['ticker']
        result['price']      = float(t.get('lastPrice', 0))
        result['price_chg']  = float(t.get('priceChangePercent', 0))
        result['volume_24h'] = float(t.get('quoteVolume', 0))

    # 标准化 fr
    if 'fr' in result and isinstance(result['fr'], list) and result['fr']:
        result['funding_rate'] = float(result['fr'][0].get('fundingRate', 0))

    # 标准化 oi
    if 'oi' in result and isinstance(result['oi'], dict):
        result['open_interest'] = float(result['oi'].get('openInterest', 0))

    # 标准化 lsr
    if 'lsr' in result and isinstance(result['lsr'], list) and result['lsr']:
        result['ls_ratio'] = float(result['lsr'][0].get('longAccount', 0.5)) * 100

    elapsed = time.time() - result['_fetched_at']
    result['_elapsed_ms'] = round(elapsed * 1000)

    return result


def fetch_realtime_multi(symbols: list, intervals: list = None) -> dict:
    """多币种并发拉取，返回 {symbol: data}"""
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(symbols), 10)) as ex:
        futs = {ex.submit(fetch_realtime, sym, intervals): sym for sym in symbols}
        for f in as_completed(futs):
            sym = futs[f]
            try:
                results[sym] = f.result()
            except Exception as e:
                results[sym] = {'_error': str(e)}
    return results


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    print(f'测试实时拉取: {sym}')
    t0 = time.time()
    d = fetch_realtime(sym)
    ms = (time.time()-t0)*1000

    print(f'耗时: {ms:.0f}ms  错误: {d.get("_errors",{})}')
    print(f'现价: ${d.get("price",0):.4f}  24H变化: {d.get("price_chg",0):.2f}%')
    print(f'资金费率: {d.get("funding_rate",0)*100:.4f}%')
    print(f'OI: {d.get("open_interest",0):.0f}')
    print(f'多空比: {d.get("ls_ratio",50):.1f}%多头')
    for iv in ['15m','1h','4h','1d']:
        k = d.get(iv, [])
        if k:
            price = float(k[-1][4])
            age = (time.time() - k[-1][0]/1000)/60
            print(f'  [{iv}] {len(k)}根  最新收盘={price:.4f}  {age:.1f}min前')
