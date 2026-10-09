#!/usr/bin/env python3
"""
breakout_watch.py — 过渡版果蝇检测器
P1-⑧ asset_config 표적별 파라미터 차별화
"""
import json, pathlib, sys

def _get_cfg(sym):
    try:
        ac = json.loads((pathlib.Path(__file__).parent / 'data' / 'asset_config.json').read_text())
        s = sym.replace('USDT','').upper()
        return ac.get(s, {}).get('breakout', {})
    except: return {}

def run_breakout_watch(symbols=None):
    if symbols is None:
        symbols = ['BTCUSDT','ETHUSDT']
    results = []
    for sym in symbols:
        cfg = _get_cfg(sym)
        print(f'[breakout] {sym}: vol_mult={cfg.get("vol_mult",1.5)} '
              f'confirm={cfg.get("confirm_bars",1)} fake_rate={cfg.get("fake_rate",0.4):.0%}')
        results.append({'symbol': sym, 'config': cfg, 'signal': 'WATCH'})
    return results

if __name__ == '__main__':
    run_breakout_watch()
