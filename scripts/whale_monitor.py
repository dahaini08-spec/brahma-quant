#!/usr/bin/env python3
"""
P1b: whale_monitor.py
Binance aggTrades / top trader ratio / OI proxy.
Keys: environment only. No defaults.
"""
import sys, os, requests, json, time, hmac, hashlib
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
try:
    from brahma_brain.brahma_bus import get_price as _bus_get_price
except ImportError:
    _bus_get_price = None
try:
    from brahma_brain.data_cache import get_open_interest as _dc_get_oi
except ImportError:
    _dc_get_oi = None

KEY = os.environ.get('BINANCE_API_KEY', '')
SEC = os.environ.get('BINANCE_SECRET', '')
HDR = {'X-MBX-APIKEY': KEY}


def get_whale_signal(sym: str = 'BTCUSDT') -> dict:
    px = _bus_get_price(sym) if _bus_get_price else float(requests.get(
        'https://fapi.binance.com/fapi/v1/ticker/price',
        params={'symbol': sym}, timeout=5).json()['price'])

    trades = requests.get('https://fapi.binance.com/fapi/v1/aggTrades',
                          params={'symbol': sym, 'limit': 500}, timeout=8).json()

    threshold_usd = 3_000_000
    whale_buys = whale_sells = 0.0
    whale_events = []

    if isinstance(trades, list):
        for t in trades:
            qty = float(t['q'])
            price = float(t['p'])
            usd = qty * price
            if usd >= threshold_usd:
                side = 'SELL' if t['m'] else 'BUY'
                if side == 'BUY':
                    whale_buys += usd
                else:
                    whale_sells += usd
                whale_events.append({
                    'side': side, 'usd': round(usd, 0),
                    'price': price, 'ts': t['T']
                })

    whale_net = whale_buys - whale_sells
    whale_dir = 'BUY' if whale_net > 0 else ('SELL' if whale_net < 0 else 'FLAT')

    ls_whale = requests.get(
        'https://fapi.binance.com/futures/data/topLongShortPositionRatio',
        params={'symbol': sym, 'period': '1h', 'limit': 4}, timeout=8
    ).json()

    whale_ls_trend = 'STABLE'
    whale_ls_latest = 1.0
    if isinstance(ls_whale, list) and len(ls_whale) >= 3:
        ratios = [float(x['longShortRatio']) for x in ls_whale[-3:]]
        whale_ls_latest = ratios[-1]
        if ratios[-1] > ratios[-3] * 1.05:
            whale_ls_trend = 'LONG_BUILDING'
        elif ratios[-1] < ratios[-3] * 0.95:
            whale_ls_trend = 'SHORT_BUILDING'
        else:
            whale_ls_trend = 'STABLE'

    oi_signal = 'NORMAL'
    oi_1h_chg = 0.0
    try:
        if _dc_get_oi:
            _oi_data = _dc_get_oi(sym)
            _oi_now = float(_oi_data.get('openInterest', 0))
        else:
            _oi_r = requests.get('https://fapi.binance.com/fapi/v1/openInterest',
                                  params={'symbol': sym}, timeout=5)
            _oi_data = _oi_r.json() if _oi_r.status_code == 200 else {}
            _oi_now = float(_oi_data.get('openInterest', 0))
        _oi_cache_file = BASE / 'data' / f'oi_prev_{sym}.json'
        if _oi_cache_file.exists() and _oi_now > 0:
            _oi_prev = json.loads(_oi_cache_file.read_text()).get('oi', _oi_now)
            if _oi_prev > 0:
                oi_1h_chg = round((_oi_now - _oi_prev) / _oi_prev * 100, 3)
        if _oi_now > 0:
            _oi_cache_file.parent.mkdir(parents=True, exist_ok=True)
            _oi_cache_file.write_text(json.dumps({'oi': _oi_now, 'ts': time.time()}))
            if oi_1h_chg > 2.0:
                oi_signal = 'OI_UP_STRONG'
            elif oi_1h_chg > 1.0:
                oi_signal = 'OI_UP'
            elif oi_1h_chg < -2.0:
                oi_signal = 'OI_DOWN_STRONG'
            elif oi_1h_chg < -1.0:
                oi_signal = 'OI_DOWN'
    except Exception:
        pass

    whale_score = 0
    if whale_net > 5_000_000:
        whale_score += 10
    elif whale_net > 0:
        whale_score += 5
    if whale_ls_trend == 'LONG_BUILDING':
        whale_score += 8
    if whale_net < -5_000_000:
        whale_score -= 10
    if whale_ls_trend == 'SHORT_BUILDING':
        whale_score -= 6

    result = {
        'symbol': sym,
        'price': px,
        'whale_buys_usd': round(whale_buys, 0),
        'whale_sells_usd': round(whale_sells, 0),
        'whale_net_usd': round(whale_net, 0),
        'whale_direction': whale_dir,
        'whale_event_count': len(whale_events),
        'whale_ls_ratio': round(whale_ls_latest, 3),
        'whale_ls_trend': whale_ls_trend,
        'oi_1h_chg': oi_1h_chg,
        'oi_signal': oi_signal,
        'whale_score': whale_score,
        'ts': time.time(),
    }
    cache = BASE / 'data' / f'whale_{sym}.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2))
    return result


def format_report(r: dict) -> str:
    return (
        f"whale {r['symbol']} px={r['price']} net={r['whale_net_usd']} "
        f"dir={r['whale_direction']} ls={r['whale_ls_trend']} "
        f"oi={r['oi_signal']} score={r['whale_score']}"
    )


if __name__ == '__main__':
    syms = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    for sym in syms:
        print(format_report(get_whale_signal(sym)))
