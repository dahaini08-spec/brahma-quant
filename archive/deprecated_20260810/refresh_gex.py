#!/usr/bin/env python3
"""
refresh_gex.py — GEX数据定时刷新
每30分钟由cron调用，刷新 BTC/ETH GEX并写入缓存
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'brahma_brain'))

from brahma_brain.gex_scanner import scan_gex

results = {}
for currency in ['BTC', 'ETH']:
    try:
        r = scan_gex(currency)
        results[currency] = {
            'spot': r.get('spot'),
            'max_strike': r.get('max_gex_strike'),
            'min_strike': r.get('min_gex_strike'),
            'zero_flip': r.get('zero_flip'),
            'pos_pct': r.get('spot_pos_pct'),
        }
        print(f'[GEX] {currency}: spot={r.get("spot")} max={r.get("max_gex_strike")} zero_flip={r.get("zero_flip")}')
    except Exception as e:
        print(f'[GEX] {currency} error: {e}')

print('GEX刷新完成')
