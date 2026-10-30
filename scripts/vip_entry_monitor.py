#!/usr/bin/env python3
"""
vip_entry_monitor.py — VIP策略入场区价格触达监控
[封印 2026-10-30 苏摩111]

功能：梵天分析给出入场区后，实时监控价格是否触达，立即推送
数据源：data/vip_entry_triggers.json（由brahma_manual_analysis写入）
触发：价格进入入场区 → P1推送
冷却：同入场区30min内只推一次
"""
import json, time, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))

STATE_FILE   = BASE / 'data' / 'vip_entry_monitor_state.json'
TRIGGER_FILE = BASE / 'data' / 'vip_entry_triggers.json'
COOLDOWN     = 1800   # 30min冷却
ENTRY_VALID  = 120    # 入场区有效时间：分析后120min内有效

import urllib.request

def get_price(sym):
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT'
        return float(json.loads(urllib.request.urlopen(url, timeout=4).read())['price'])
    except: return 0.0

def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except: return {}

def save_state(s):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)

def run():
    if not TRIGGER_FILE.exists():
        print('HEARTBEAT_OK (no triggers)'); return
    try:
        triggers = json.loads(TRIGGER_FILE.read_text())
    except: print('HEARTBEAT_OK (trigger read err)'); return

    if not triggers:
        print('HEARTBEAT_OK (empty triggers)'); return

    state = load_state()
    now   = time.time()
    alerts = []

    for t in triggers:
        sym       = t.get('sym', '').upper()
        direction = t.get('direction', '').lower()
        entry_lo  = float(t.get('entry_lo', 0) or 0)
        entry_hi  = float(t.get('entry_hi', 0) or 0)
        tp1       = float(t.get('tp1', 0) or 0)
        sl        = float(t.get('sl', 0) or 0)
        ts        = float(t.get('ts', 0) or 0)

        if not sym or entry_lo <= 0 or entry_hi <= 0: continue
        # 有效期检查
        age_min = (now - ts) / 60
        if age_min > ENTRY_VALID:
            continue

        px = get_price(sym)
        if px <= 0: continue

        in_zone   = entry_lo <= px <= entry_hi
        near_lo   = abs(px - entry_lo) / px < 0.003
        near_hi   = abs(px - entry_hi) / px < 0.003

        if not (in_zone or near_lo or near_hi): continue

        key = f'entry_{sym}_{direction}_{int(entry_lo)}'
        if now - state.get(key, 0) < COOLDOWN:
            print(f'[vip_entry_monitor] {sym} {direction} 冷却中'); continue

        dir_emoji = '🟢' if direction == 'long' else '🔴'
        dir_txt   = '多单' if direction == 'long' else '空单'
        status    = '已进入✅' if in_zone else '即将触达⚡'

        sl_str  = f'止损 ${sl:,.0f}' if sl > 0 else ''
        tp_str  = f'目标 ${tp1:,.0f}' if tp1 > 0 else ''

        msg = (
            f'⚡ VIP入场区触达 | {sym}\n\n'
            f'{sym} ${px:,.2f} {status}\n'
            f'入场区 ${entry_lo:,}~${entry_hi:,}\n\n'
            f'📌 {dir_emoji} {dir_txt}立即准备：\n'
            f'入场 ${entry_lo:,}~${entry_hi:,}\n'
        )
        if sl_str or tp_str:
            msg += f'{sl_str}  {tp_str}\n'
        msg += f'分析时效：{age_min:.0f}min前\n\n🌿 姓赵不宣 | 不是建议'

        alerts.append((key, msg, sym, px, direction))

    if not alerts:
        print(f'HEARTBEAT_OK ({len(triggers)} triggers, none hit)'); return

    try:
        from push_hub import push_jarvis
    except ImportError:
        print('[vip_entry_monitor] push_hub导入失败'); return

    for key, msg, sym, px, direction in alerts:
        ok = push_jarvis(msg, priority='P1', source='vip_entry_monitor',
                         sym=sym, direction=direction,
                         dedup_key=key, dedup_ttl=COOLDOWN)
        if ok:
            state[key] = now
            print(f'[vip_entry_monitor] {sym} {direction} 触达推送✅ px=${px:,.2f}')
        else:
            print(f'[vip_entry_monitor] {sym} 被signal_govern拦截')

    save_state(state)

if __name__ == '__main__':
    run()
