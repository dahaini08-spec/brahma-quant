"""
options_pc_ratio.py — 梵天期权P/C比层 (s_options / _options_pc_v56)
设计院 2026-08-01 自主补全

数据源优先级:
  1. Deribit公开API (BTC/ETH未平仓量)
  2. Binance期权API (USDT期权)
  3. data/options_pc_BTC.json 本地缓存回退

返回格式（与brahma_engine期望对齐）:
  pc_oi_ratio       : float  Put/Call OI比
  pc_score          : int    评分调整（-8~+8）
  interpretation_oi : str    信号描述
  signal            : str    CALL_HEAVY / PUT_HEAVY / BALANCED / UNKNOWN
  put_oi            : float
  call_oi           : float
  source            : str    数据来源
  error             : str    非空时表示获取失败
"""

import json, time, urllib.request
from pathlib import Path

_CACHE: dict = {}
_CACHE_TTL = 300  # 5分钟

_DATA_DIR = Path(__file__).parent.parent / 'data'


def _get(url: str, timeout: int = 8) -> dict:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _from_deribit(currency: str) -> dict:
    """Deribit 公开 API 获取 BTC/ETH 期权 OI"""
    url = (f'https://deribit.com/api/v2/public/get_book_summary_by_currency'
           f'?currency={currency}&kind=option')
    data = _get(url, timeout=10)
    if not data or not data.get('result'):
        return {}

    put_oi  = sum(float(x.get('open_interest', 0))
                  for x in data['result'] if '-P' in x.get('instrument_name', ''))
    call_oi = sum(float(x.get('open_interest', 0))
                  for x in data['result'] if '-C' in x.get('instrument_name', ''))

    if call_oi <= 0:
        return {}

    return {'put_oi': put_oi, 'call_oi': call_oi, 'source': 'deribit'}


def _from_binance(symbol_base: str) -> dict:
    """Binance 期权 API — USDT期权 P/C OI"""
    # Binance European options: /eapi/v1/openInterest
    url = f'https://eapi.binance.com/eapi/v1/openInterest?underlyingAsset={symbol_base}USDT&expiration=ALL'
    data = _get(url, timeout=8)
    if not data or not isinstance(data, list):
        return {}

    put_oi  = sum(float(x.get('putOpenInterest', 0)) for x in data)
    call_oi = sum(float(x.get('callOpenInterest', 0)) for x in data)

    if call_oi <= 0:
        return {}

    return {'put_oi': put_oi, 'call_oi': call_oi, 'source': 'binance_eapi'}


def _from_cache_file(symbol_base: str) -> dict:
    """回退：读取本地 data/options_pc_BTC.json 缓存"""
    f = _DATA_DIR / f'options_pc_{symbol_base}.json'
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text())
        # 文件格式兼容: {put_oi, call_oi} 或 {pc_oi_ratio}
        if 'put_oi' in d and 'call_oi' in d:
            return {**d, 'source': 'local_cache'}
        if 'pc_oi_ratio' in d:
            ratio = float(d['pc_oi_ratio'])
            # 反推虚拟 OI（仅用于信号分类）
            return {'put_oi': ratio, 'call_oi': 1.0, 'source': 'local_cache'}
    except Exception:
        pass
    return {}


def _score_from_ratio(pc_ratio: float, direction: str = 'SHORT') -> tuple:
    """
    P/C比 → 评分 + 信号文字
    解读基准（BEAR_TREND SHORT视角，其他体制同向调整）:
      P/C < 0.5  Call极重 → 做市商负Gamma放大下跌 → SHORT +8
      P/C 0.5~0.7 Call重  → 负Gamma环境             → SHORT +4
      P/C 0.7~0.9 偏Call  → 轻度看涨               → SHORT +2 / LONG -2
      P/C 0.9~1.1 均衡    → 中性                   → 0
      P/C 1.1~1.3 偏Put   → 轻度看跌               → SHORT -2 / LONG +2
      P/C 1.3~1.6 Put重   → 悲观已定价，反弹风险    → SHORT -4
      P/C > 1.6  Put极重  → 极度恐慌，反转警告      → SHORT -8
    """
    if pc_ratio < 0.5:
        signal = 'CALL_HEAVY_EXTREME'
        base   = 8
        interp = f'P/C OI={pc_ratio:.3f} 🔴Call极重 做市商负Gamma放大下跌'
    elif pc_ratio < 0.7:
        signal = 'CALL_HEAVY'
        base   = 4
        interp = f'P/C OI={pc_ratio:.3f} 🟠Call重(0.5~0.7，做市商负Gamma)'
    elif pc_ratio < 0.9:
        signal = 'CALL_LEAN'
        base   = 2
        interp = f'P/C OI={pc_ratio:.3f} 🟡偏Call(0.7~0.9，轻度看涨)'
    elif pc_ratio < 1.1:
        signal = 'BALANCED'
        base   = 0
        interp = f'P/C OI={pc_ratio:.3f} ⚪均衡(0.9~1.1)'
    elif pc_ratio < 1.3:
        signal = 'PUT_LEAN'
        base   = -2
        interp = f'P/C OI={pc_ratio:.3f} 🟡偏Put(1.1~1.3，轻度看跌)'
    elif pc_ratio < 1.6:
        signal = 'PUT_HEAVY'
        base   = -4
        interp = f'P/C OI={pc_ratio:.3f} 🟠Put重(1.3~1.6，悲观已定价)'
    else:
        signal = 'PUT_HEAVY_EXTREME'
        base   = -8
        interp = f'P/C OI={pc_ratio:.3f} 🔴Put极重 极度恐慌 反转警告'

    # 方向修正：LONG视角与SHORT相反
    score = base if direction != 'LONG' else -base
    return signal, score, interp


def get_options_pc(symbol: str = 'BTC', direction: str = 'SHORT') -> dict:
    """
    主入口 — brahma_engine 调用
    symbol: 'BTC' / 'ETH'（不含USDT）
    direction: 'SHORT' / 'LONG'
    """
    cache_key = f'opc_{symbol}_{direction}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']

    default = {
        'pc_oi_ratio': 0.0,
        'pc_score': 0,
        'interpretation_oi': f'P/C 数据不可用',
        'signal': 'UNKNOWN',
        'put_oi': 0.0,
        'call_oi': 0.0,
        'source': 'unavailable',
        'error': 'no data',
    }

    # 尝试数据源
    raw = {}
    for fetcher in [
        lambda: _from_deribit(symbol),
        lambda: _from_binance(symbol),
        lambda: _from_cache_file(symbol),
    ]:
        try:
            raw = fetcher()
            if raw:
                break
        except Exception:
            continue

    if not raw:
        _CACHE[cache_key] = {'data': default, 'ts': now}
        return default

    put_oi  = float(raw.get('put_oi', 0))
    call_oi = float(raw.get('call_oi', 1))
    pc_ratio = round(put_oi / max(call_oi, 1e-9), 3)

    signal, score, interp = _score_from_ratio(pc_ratio, direction)

    # 写本地缓存（下次API失败时回退）
    try:
        cache_file = _DATA_DIR / f'options_pc_{symbol}.json'
        cache_file.write_text(json.dumps({
            'pc_oi_ratio': pc_ratio,
            'put_oi': put_oi,
            'call_oi': call_oi,
            'ts': now,
            'source': raw.get('source', 'unknown'),
        }))
    except Exception:
        pass

    result = {
        'pc_oi_ratio': pc_ratio,
        'pc_score': score,
        'interpretation_oi': interp,
        'signal': signal,
        'put_oi': put_oi,
        'call_oi': call_oi,
        'source': raw.get('source', 'unknown'),
        'error': '',
    }
    _CACHE[cache_key] = {'data': result, 'ts': now}
    return result


if __name__ == '__main__':
    for sym in ['BTC', 'ETH']:
        r = get_options_pc(sym, 'SHORT')
        print(f'{sym}: P/C={r["pc_oi_ratio"]:.3f}  signal={r["signal"]}  '
              f'score={r["pc_score"]:+d}  src={r["source"]}')
        print(f'  {r["interpretation_oi"]}')
