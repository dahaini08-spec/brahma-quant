#!/usr/bin/env python3
"""OI异动扫描器 - 设计院 2026-07-05"""
import requests, json, time, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.system_config import FAPI_BASE, JARVIS_TARGET, JARVIS_CHANNEL

SYMS = ['BTCUSDT','ETHUSDT','SOLUSDT','HYPEUSDT','BNBUSDT','LDOUSDT']
OI_CACHE = os.path.join(os.path.dirname(__file__), '..', 'data', 'oi_prev_cache.json')

def get_oi(sym):
    r = requests.get(f'{FAPI_BASE}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=3', timeout=6)
    data = r.json()
    if isinstance(data, list) and len(data) >= 2:
        cur  = float(data[-1]['sumOpenInterest'])
        prev = float(data[-2]['sumOpenInterest'])
        px   = float(requests.get(f'{FAPI_BASE}/fapi/v1/ticker/price?symbol={sym}', timeout=4).json()['price'])
        chg_pct = (cur - prev) / prev * 100 if prev > 0 else 0
        return {'cur': cur, 'prev': prev, 'px': px, 'oi_usd': cur * px / 1e9, 'chg_pct': chg_pct}
    return None

alerts = []
for sym in SYMS:
    try:
        d = get_oi(sym)
        if d and abs(d['chg_pct']) >= 3.0:
            icon = '📈' if d['chg_pct'] > 0 else '📉'
            alerts.append(f"{icon} {sym}: OI{d['chg_pct']:+.1f}% | ${d['oi_usd']:.2f}B")
    except Exception as e:
        pass

if alerts:
    msg = '🚨 OI异动警报\n' + '\n'.join(alerts)
    print(msg)
    import subprocess
    subprocess.Popen(
        ['openclaw','message','send','--to', JARVIS_TARGET,'--channel', JARVIS_CHANNEL,'--message', msg],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
else:
    print('HEARTBEAT_OK')
