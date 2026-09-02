#!/usr/bin/env python3
"""brahma_state_refresh.py — 梵天体制状态刷新（supercronic每30分钟调用）"""
import sys, json
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))

def clean(d, depth=0):
    if depth > 10: return str(d)
    if isinstance(d, dict):   return {k: clean(v, depth+1) for k,v in d.items()}
    if isinstance(d, (list, tuple)): return [clean(i, depth+1) for i in d]
    if isinstance(d, (int, float, str, bool, type(None))): return d
    return str(d)

try:
    from brahma_bus import BrahmaEventBus
    BrahmaEventBus()
    from brahma_core import analyze
    result = analyze('BTCUSDT')
    cleaned = clean(result)
    (BASE / 'data' / 'brahma_state.json').write_text(
        json.dumps(cleaned, ensure_ascii=False)
    )
    print(f'[state_refresh] ✅ score={cleaned.get("score_final")} regime={cleaned.get("regime")}')
except Exception as e:
    print(f'[state_refresh] ❌ {e}')
    sys.exit(1)
