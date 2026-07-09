#!/usr/bin/env python3
"""
brahma_order_engine.py — 统一开单引擎 v1.0
设计院 2026-07-04

替代三个独立executor，统一处理signal_bus中的信号
支持 --dry-run 模式（只推送不下单）
"""
import json, time, sys, os, hmac, hashlib, requests, argparse
from pathlib import Path

BASE     = Path(__file__).parent.parent
sys.path.insert(0, str(BASE/'scripts'))
sys.path.insert(0, str(BASE/'brahma_brain'))

from signal_bus import read_pending, mark_status
from system_config import (API_KEY,
                            API_SECRET as SECRET,
                            JARVIS_USER_ID, JARVIS_THREAD_ID)

FAPI = 'https://fapi.binance.com'
MAX_POSITIONS   = 5
MIN_SCORE       = 138
MIN_RR          = 1.0
MAX_NAV_PCT     = 0.05   # 单笔最大5% NAV
PUSH_TARGET     = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'

# 体制死穴
DEAD_ZONES = {('BEAR_TREND','LONG'), ('CHOP_MID','LONG'), ('BULL_TREND','SHORT')}

def _signed(params: dict):
    params['timestamp'] = int(time.time()*1000)
    qs = '&'.join(f'{k}={v}' for k,v in params.items())
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + f'&signature={sig}'

def get_nav() -> float:
    try:
        r = requests.get(f'{FAPI}/fapi/v2/account',
            params=json.loads('{}'),
            headers={'X-MBX-APIKEY': API_KEY}, timeout=8)
        qs = _signed({})
        r = requests.get(f'{FAPI}/fapi/v2/account?{qs}',
            headers={'X-MBX-APIKEY': API_KEY}, timeout=8)
        return float(r.json().get('totalMarginBalance', 2000))
    except: return 2000.0

def get_open_positions() -> list:
    try:
        qs = _signed({})
        r = requests.get(f'{FAPI}/fapi/v2/positionRisk?{qs}',
            headers={'X-MBX-APIKEY': API_KEY}, timeout=8)
        return [p for p in r.json() if float(p.get('positionAmt',0)) != 0]
    except: return []

def get_price(symbol: str) -> float:
    try:
        r = requests.get(f'{FAPI}/fapi/v1/ticker/price',
                         params={'symbol': symbol}, timeout=5)
        return float(r.json()['price'])
    except: return 0.0

def get_symbol_info(symbol: str) -> dict:
    try:
        r = requests.get(f'{FAPI}/fapi/v1/exchangeInfo', timeout=10)
        for s in r.json()['symbols']:
            if s['symbol'] == symbol:
                filters = {f['filterType']: f for f in s['filters']}
                step = float(filters.get('LOT_SIZE',{}).get('stepSize','0.001'))
                tick = float(filters.get('PRICE_FILTER',{}).get('tickSize','0.01'))
                return {'step': step, 'tick': tick, 'minQty': float(filters.get('LOT_SIZE',{}).get('minQty','0.001'))}
    except: pass
    return {'step': 0.001, 'tick': 0.01, 'minQty': 0.001}

def round_step(qty: float, step: float) -> float:
    import math
    precision = max(0, -int(math.floor(math.log10(step)))) if step > 0 else 3
    return round(round(qty / step) * step, precision)

def place_order(symbol, side, qty, price=None, order_type='LIMIT', dry_run=False) -> dict:
    params = {
        'symbol':     symbol,
        'side':       side,
        'type':       order_type,
        'quantity':   qty,
    }
    if order_type == 'LIMIT':
        params['price'] = price
        params['timeInForce'] = 'GTC'

    if dry_run:
        return {'orderId': 'DRY_RUN', 'status': 'NEW', 'symbol': symbol,
                'side': side, 'origQty': qty, 'price': price or 0}
    try:
        qs = _signed(params)
        r = requests.post(f'{FAPI}/fapi/v1/order',
            data=qs, headers={'X-MBX-APIKEY': API_KEY}, timeout=10)
        return r.json()
    except Exception as e:
        return {'error': str(e)}

def push(msg: str):
    try:
        os.system(f'openclaw message send --channel jarvis --to "{PUSH_TARGET}" '
                  f'--message "{msg.replace(chr(34),chr(39))}" 2>/dev/null')
    except: pass

def run(dry_run=False):
    pass  # [静默]

    # 1. 读取信号
    signals = read_pending(max_age_h=12, min_score=MIN_SCORE)
    if not signals:
        pass  # [静默]
        return

    # 2. 当前持仓
    positions = get_open_positions()
    pos_syms  = {p['symbol'] for p in positions}
    pass  # [静默]

    if len(positions) >= MAX_POSITIONS:
        pass  # [静默]
        return

    nav = get_nav()
    executed = 0

    for sig in signals:
        sym       = sig['symbol']
        direction = sig['direction']
        score     = float(sig.get('score', 0))
        regime    = sig.get('regime', '')
        rr1       = float(sig.get('rr1', 0))
        sl_pct    = float(sig.get('sl_pct', 0))
        entry_lo  = float(sig.get('entry_lo', 0))
        entry_hi  = float(sig.get('entry_hi', 0))
        source    = sig.get('source', '?')

        pass  # [静默]

        # 死穴过滤
        if (regime, direction) in DEAD_ZONES:
            pass  # [静默]
            continue

        # RR过滤
        if rr1 < MIN_RR:
            pass  # [静默]
            continue

        # 已持仓过滤
        if sym in pos_syms:
            pass  # [静默]
            continue

        # 持仓上限
        if len(positions) + executed >= MAX_POSITIONS:
            pass  # [静默]
            break

        # 价格检查
        price = get_price(sym)
        if not price:
            pass  # [静默]
            continue

        # 入场区判断
        in_zone = entry_lo <= price <= entry_hi
        above_zone = price > entry_hi * 1.02
        if above_zone:
            pass  # [静默]
            mark_status(sig['signal_id'], 'expired')
            continue

        order_type = 'MARKET' if in_zone else 'LIMIT'
        order_price = round(entry_lo * 1.001, 6) if not in_zone else None

        # 仓位计算
        pos_pct = min(MAX_NAV_PCT, sl_pct / 100 * 10 if sl_pct > 0 else 0.03)
        if sl_pct > 10: pos_pct *= 0.5   # 宽止损减半仓
        notional = nav * pos_pct
        qty = notional / price

        info  = get_symbol_info(sym)
        qty   = round_step(qty, info['step'])
        if qty < info['minQty']:
            pass  # [静默]
            continue

        pass  # [静默]
        pass  # [静默]

        # 下单
        result = place_order(sym, 'BUY' if direction=='LONG' else 'SELL',
                             qty, order_price, order_type, dry_run)

        if result.get('error'):
            pass  # [静默]
            continue

        order_id = str(result.get('orderId',''))
        mark_status(sig['signal_id'], 'entered', order_id)
        executed += 1

        prefix = '📋[DryRun]' if dry_run else '✅'
        msg = (f'{prefix} 开单: {sym} {direction} @{price:.4f} '
               f'qty={qty} score={score:.0f} source={source}')
        pass  # [静默]
        push(msg)

    pass  # [静默]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
