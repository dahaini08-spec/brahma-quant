#!/usr/bin/env python3
"""Autopilot L0工作记忆刷新 — 数据健康检查+状态更新"""
import json, time
from pathlib import Path

BASE = Path(__file__).parent.parent / 'data'
STATE_FILE = BASE / 'autopilot_state.json'

# 数据健康检查
now = time.time()
fresh = 0
dead = 0
check_files = [
    'liq_heatmap_btcusdt.json',
    'liq_heatmap_ethusdt.json',
    'cvd_realtime_btcusdt.json',
    'cvd_realtime_ethusdt.json',
    'oi_candidates.json',
    'regime_state.json',
    'gex_state.json',
    'vol_beta_state.json',
    'macro_real.json',
    'har_rv_cache.json',
]
for f in check_files:
    p = BASE / f
    if p.exists():
        age = (now - p.stat().st_mtime) / 3600
        if age < 12:
            fresh += 1
        else:
            dead += 1
    else:
        dead += 1

# 读现有状态
state = {}
if STATE_FILE.exists():
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        state = {}

state['ts'] = now
state['data_health'] = {'fresh': fresh, 'dead': dead}

STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
print(f"[L0] fresh={fresh} dead={dead}")
