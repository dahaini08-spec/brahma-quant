#!/usr/bin/env python3
"""
price_trigger_monitor.py — 条件触发监控 [9.18苏摩111 Phase 5]
每5min检查关键条件，满足时自动触发分析+推送
"""
import json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TRIGGER_FILE = os.path.join(ROOT, 'data', 'price_triggers.json')
STATE_FILE = os.path.join(ROOT, 'data', 'price_trigger_state.json')

def get_price(sym):
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT'
        return float(json.loads(urllib.request.urlopen(url, timeout=5).read())['price'])
    except:
        return 0

def load_triggers():
    if not os.path.exists(TRIGGER_FILE):
        return {}
    return json.load(open(TRIGGER_FILE))

def main():
    triggers = load_triggers()
    if not triggers:
        print('[trigger] 无触发条件配置')
        return
    
    state = {}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE))
    
    for sym, cfg in triggers.items():
        price = get_price(sym)
        if price == 0:
            continue
        
        stop_wall = cfg.get('stop_wall', 0)
        support_pool = cfg.get('support_pool', 0)
        liquidation = cfg.get('liquidation', 0)
        
        triggered = []
        
        # 条件1: 价格到达止损墙±0.5%
        if stop_wall > 0 and abs(price - stop_wall) / stop_wall < 0.005:
            triggered.append(f'价格${price:,.0f}到达止损墙${stop_wall:,.0f}')
        
        # 条件2: 价格到达清算区±0.5%
        if liquidation > 0 and abs(price - liquidation) / liquidation < 0.005:
            triggered.append(f'价格${price:,.0f}到达清算区${liquidation:,.0f}')
        
        # 条件3: 价格到达支撑池±0.5%
        if support_pool > 0 and abs(price - support_pool) / support_pool < 0.005:
            triggered.append(f'价格${price:,.0f}到达支撑池${support_pool:,.0f}')
        
        # 去重：同条件30min内不重复触发
        _key = f'{sym}_last_trigger'
        _last_ts = state.get(_key, 0)
        if triggered and (time.time() - _last_ts) > 1800:
            state[_key] = time.time()
            print(f'[trigger] {sym} {" | ".join(triggered)}')
            # 触发分析
            os.system(f'cd {ROOT} && timeout 90 python3 scripts/brahma_manual_analysis.py --symbols {sym} --push 2>&1 >> logs/trigger_analysis.log')
        elif triggered:
            print(f'[trigger] {sym} 触发但30min内已触发过，跳过')
        else:
            print(f'[trigger] {sym} ${price:,.0f} 未触发任何条件')
    
    json.dump(state, open(STATE_FILE, 'w'))

if __name__ == '__main__':
    main()
