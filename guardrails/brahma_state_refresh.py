#!/usr/bin/env python3
"""
brahma_state_refresh.py — 系统状态定时刷新（零AI，每15分钟）
功能：刷新brahma_state.json中的price/nav/regime/last_update
依赖：Binance FAPI公开接口，不需要API Key
2026-06-05 重建
"""
import json, urllib.request, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
STATE_FILE = BASE / 'data' / 'brahma_state.json'
LOG = BASE / 'logs' / 'system_cron.log'

def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [state-refresh] {msg}'
    print(line)
    try:
        with open(LOG, 'a') as f:
            f.write(line + '\n')
    except: pass

def fetch_price(symbol):
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = urllib.request.urlopen(url, timeout=5)
        return float(json.loads(r.read())['price'])
    except:
        return None

def run():
    try:
        state = json.load(open(STATE_FILE))
    except Exception as e:
        log(f'读取state失败: {e}')
        return

    # 刷新主要品种价格
    symbols = ['BTCUSDT', 'ETHUSDT', 'DOGEUSDT', 'BNBUSDT', 'SOLUSDT']
    prices = {}
    for sym in symbols:
        p = fetch_price(sym)
        if p:
            prices[sym] = p

    if not prices:
        log('价格获取失败，跳过刷新')
        return

    # 更新state
    state['prices'] = prices
    state['price'] = prices.get('BTCUSDT', state.get('price', 0))
    state['last_update'] = datetime.now(timezone.utc).isoformat()
    state['refresh_source'] = 'brahma_state_refresh'


    # [FIX-SSOT 2026-06-14] 同步体制（唯一真理源 = market_state）
    try:
        import sys as _s2
        _bb = str(Path(__file__).parent.parent / 'brahma_brain')
        if _bb not in _s2.path: _s2.path.insert(0, _bb)
        from market_state import analyze as _ms_a
        _ms = _ms_a('BTCUSDT')
        state['regime'] = _ms.get('regime', state.get('regime', 'CHOP_MID'))
        _mom = _ms.get('momentum', {})
        _trend = _ms.get('trend', {})
        state['regime_snapshot'] = {
            'symbol': 'BTCUSDT', 'regime': state['regime'],
            'rsi_1h': _mom.get('rsi_1h',0), 'rsi_4h': _mom.get('rsi_4h',0),
            'rsi_1d': _mom.get('rsi_1d',0),
            'trend_1h': _trend.get('1h',{}).get('direction','?'),
            'trend_4h': _trend.get('4h',{}).get('direction','?'),
            'trend_1d': _trend.get('1d',{}).get('direction','?'),
            'source': 'market_state.detect_regime',
        }
        # 修复 market_prices 实时化
        state['market_prices'] = {'BTCUSDT': prices.get('BTCUSDT', 0), 'ETHUSDT': prices.get('ETHUSDT', 0)}
    except Exception as _re:
        pass  # regime失败不影响价格刷新
    # 写回
    tmp = str(STATE_FILE) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(STATE_FILE))

    btc = prices.get('BTCUSDT', 0)
    eth = prices.get('ETHUSDT', 0)
    nav = state.get('nav', 0)
    log(f'✅ BTC={btc:.0f} ETH={eth:.2f} NAV={nav:.2f}')

if __name__ == '__main__':
    run()
