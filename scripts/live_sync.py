#!/usr/bin/env python3
"""
live_sync.py — 梵天神经感知核心
苏摩111封印 2026-07-18

每次调用立即从交易所拉取真实状态，重建所有缓存。
这是系统的「神经末梢」，任何依赖缓存的模块都应先调用此脚本。

设计原则：
  - 不信任任何本地缓存
  - 不依赖任何cron任务
  - 直接调用交易所API
  - 写入后返回真实状态
"""

import subprocess, json, time, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

def sync_positions() -> list:
    """拉取真实持仓，重建wuqu_positions.json"""
    sys.path.insert(0, str(BASE))
    from scripts.binance_fapi import get_positions
    positions, err = get_positions()
    if err:
        raise RuntimeError(f'binance_fapi error: {err}')
    real = [p for p in (positions or []) if abs(float(p.get('positionAmt',0))) > 0]
    cache = []
    for p in real:
        amt = float(p.get('positionAmt',0))
        entry = float(p.get('entryPrice',0))
        mark = float(p.get('markPrice', entry))
        pnl = float(p.get('unRealizedProfit',0))
        cache.append({
            'symbol': p.get('symbol',''),
            'side': 'LONG' if amt > 0 else 'SHORT',
            'entry_price': entry,
            'mark_price': mark,
            'quantity': abs(amt),
            'unrealized_pnl': round(pnl,4),
            'leverage': int(p.get('leverage',1)),
            'notional_usdt': round(abs(amt)*mark, 4),
            'stop_loss': None,
            'take_profit': None,
            'updated_at': int(time.time()),
            'source': 'LIVE_SYNC'
        })
    (DATA / 'wuqu_positions.json').write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    return cache

def sync_prices(symbols=None) -> dict:
    """拉取实时价格，刷新live_prices.json"""
    if symbols is None:
        symbols = ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT']
    prices = {}
    for sym in symbols:
        r = subprocess.run(
            ['binance-cli','futures-usds','symbol-price-ticker','--symbol',sym],
            capture_output=True, text=True, timeout=8
        )
        try:
            d = json.loads(r.stdout)
            prices[sym] = {'price': float(d['price']), 'ts': int(time.time()*1000)}
        except: pass
    if prices:
        (DATA / 'live_prices.json').write_text(json.dumps(prices, indent=2))
    return prices

def full_sync() -> dict:
    """完整神经感知同步：持仓+价格"""
    ts = time.time()
    positions = sync_positions()
    # 加入持仓标的的实时价格
    pos_syms = [p['symbol'] for p in positions]
    base_syms = ['BTCUSDT','ETHUSDT']
    all_syms = list(set(base_syms + pos_syms))
    prices = sync_prices(all_syms)
    
    # 更新持仓的mark_price为最新价格
    for p in positions:
        sym = p['symbol']
        if sym in prices:
            latest = prices[sym]['price']
            old_mark = p['mark_price']
            ep = p['entry_price']
            qty = p['quantity']
            side = p['side']
            p['mark_price'] = latest
            p['unrealized_pnl'] = round((latest-ep)*qty if side=='LONG' else (ep-latest)*qty, 4)
    
    (DATA / 'wuqu_positions.json').write_text(json.dumps(positions, indent=2, ensure_ascii=False))
    
    elapsed = time.time() - ts
    total_pnl = sum(p['unrealized_pnl'] for p in positions)
    
    return {
        'positions': positions,
        'prices': prices,
        'total_pnl': round(total_pnl, 4),
        'elapsed_ms': round(elapsed*1000),
        'synced_at': int(ts)
    }

if __name__ == '__main__':
    result = full_sync()
    print(f"✅ 神经感知同步完成 ({result['elapsed_ms']}ms)")
    print(f"   持仓: {len(result['positions'])}个 · 总PnL: {result['total_pnl']:+.4f}U")
    print(f"   BTC={result['prices'].get('BTCUSDT',{}).get('price','?')} ETH={result['prices'].get('ETHUSDT',{}).get('price','?')}")
    for p in result['positions']:
        print(f"   {p['symbol']} {p['side']} mark={p['mark_price']} PnL={p['unrealized_pnl']:+.4f}U")
