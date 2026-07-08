#!/usr/bin/env python3
"""
实盘 vs 铁证偏差自动报警器 v1.0
================================
[P1-4 设计院 2026-06-24]

功能：
  - 持续监测实盘 WR 是否显著偏离铁证预期
  - 偏离 > WARN_THRESHOLD(20pp) → 推送警告
  - 偏离 > BLOCK_THRESHOLD(35pp) 且 n≥20 → 推送封禁建议
  - 首次发现新死穴 → 推送并写入 LIVE_WR_PENALTY 建议

运行方式：
  openclaw cron → 每2小时执行一次
"""
import json, sys, os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

BASE = Path('/root/.openclaw/workspace/trading-system')
DATA = BASE / 'data'

# 报警阈值
WARN_THRESHOLD  = 20   # pp
BLOCK_THRESHOLD = 35   # pp，且 n≥20
MIN_N_WARN      = 10   # 至少N条才报警
MIN_N_BLOCK     = 20   # 至少N条才建议封禁

# ── 加载铁证矩阵 ──────────────────────────────────────
def load_iron_evidence():
    """合并 BTC/ETH 铁证 + 中小币铁证"""
    combined = {}
    # BTC/ETH 铁证（全局参考）
    try:
        d = json.load(open(DATA / 'dharma_iron_evidence.json'))
        rdm = d.get('regime_direction_matrix', {})
        for key, v in rdm.items():
            if key.startswith('_') or not isinstance(v, dict): continue
            wr = v.get('wr', 0)
            if isinstance(wr, (int, float)) and wr <= 1.0: wr = wr * 100
            combined[key] = {
                'wr': float(wr), 'n': v.get('n', 0), 'source': 'btceth_6.5y'
            }
    except Exception as e:
        print(f'[WARN] BTC/ETH铁证加载失败: {e}')

    # 中小币铁证（分标的）
    try:
        altcoin = json.load(open(DATA / 'altcoin_iron_evidence.json'))
        for sym, data in altcoin.items():
            for combo_key, m in data.get('wr_matrix', {}).items():
                combined[f'{sym}__{combo_key}'] = {
                    'wr': m['wr'], 'n': m['n'], 'source': f'{sym}_offline'
                }
    except: pass

    return combined

# ── 加载实盘数据 ──────────────────────────────────────
def load_live_stats():
    """按 symbol×regime×direction 统计实盘 WR
    [设计院修复 2026-06-24] 排除TIMEOUT：只统计真实触达(TP1/TP2/SL)的信号
    原逻辑用pnl_pct>0判断WIN，TIMEOUT的pnl_pct通常为负→被算LOSS→WR虚假偏低
    """
    lines = (DATA / 'live_signal_log.jsonl').read_text().splitlines()
    groups = defaultdict(lambda: {'wins': 0, 'losses': 0, 'timeouts': 0, 'n_eff': 0, 'n_total': 0})

    for line in lines:
        if not line.strip(): continue
        try:
            r = json.loads(line)
            if not r.get('settled'): continue

            sym    = r.get('symbol', '?')
            regime = r.get('regime', '?')
            dr     = r.get('direction', '?')
            outcome = r.get('outcome') or r.get('status') or r.get('result', '')

            for key in [f'{regime}_{dr}', f'{sym}__{regime}_{dr}']:
                groups[key]['n_total'] += 1
                if outcome in ('TP1', 'TP2', 'WIN'):
                    groups[key]['wins'] += 1
                    groups[key]['n_eff'] += 1
                elif outcome in ('SL', 'LOSS'):
                    groups[key]['losses'] += 1
                    groups[key]['n_eff'] += 1
                else:  # TIMEOUT / REGIME_EXPIRED 等
                    groups[key]['timeouts'] += 1
        except: pass

    # 计算 WR（仅基于有效触达，排除TIMEOUT）
    result = {}
    for key, v in groups.items():
        n_eff = v['n_eff']  # 真实WIN+LOSS
        if n_eff < 1: continue
        result[key] = {
            'wr':      round(v['wins'] / n_eff * 100, 1),
            'n':       n_eff,          # 报警逻辑的n用有效样本
            'n_total': v['n_total'],   # 总信号数（含TIMEOUT）供参考
            'wins':    v['wins'],
            'losses':  v['losses'],
            'timeouts':v['timeouts'],
        }
    return result

# ── 偏差检测 ──────────────────────────────────────────
def detect_deviations(iron, live):
    alerts = []

    # 加载当前黑名单（避免重复报警）
    try:
        import auto_execute_gate as aeg
        current_block = set(aeg.LIVE_WR_PENALTY.keys())
    except: current_block = set()

    for key, live_v in live.items():
        n    = live_v['n']
        wr_l = live_v['wr']

        if n < MIN_N_WARN: continue

        # 获取铁证参考（优先标的专属，其次通用）
        iron_ref = iron.get(key) or iron.get(key.split('__')[-1] if '__' in key else key)
        if not iron_ref: continue

        wr_i = iron_ref['wr']
        gap  = wr_l - wr_i

        # 仅报负偏离（实盘比铁证差）
        if gap >= -WARN_THRESHOLD: continue

        severity = '🆘 封禁建议' if (gap <= -BLOCK_THRESHOLD and n >= MIN_N_BLOCK) else '⚠️ 警告'
        already_blocked = key.replace('__','_').split('_')[0] + '_' + key.split('_')[-1] in current_block if '__' in key else key in current_block

        alerts.append({
            'key':           key,
            'live_wr':       wr_l,
            'iron_wr':       wr_i,
            'gap_pp':        round(gap, 1),
            'n':             n,
            'severity':      severity,
            'already_blocked': already_blocked,
        })

    return sorted(alerts, key=lambda x: x['gap_pp'])

# ── 主函数 ────────────────────────────────────────────
def main():
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f'[实盘偏差报警器] {ts}')

    iron = load_iron_evidence()
    live = load_live_stats()

    alerts = detect_deviations(iron, live)

    # 加载上次报警状态（防重复推送）
    state_path = DATA / 'deviation_alert_state.json'
    prev_alerts = {}
    if state_path.exists():
        try: prev_alerts = json.load(open(state_path))
        except: pass

    new_alerts = []
    for a in alerts:
        prev = prev_alerts.get(a['key'], {})
        # 新出现，或严重程度升级，或 n 增加超20条
        if (a['key'] not in prev_alerts or
            prev.get('severity') != a['severity'] or
            a['n'] - prev.get('n', 0) >= 20):
            new_alerts.append(a)

    # 输出报告
    if not alerts:
        print('✅ 无偏差报警，实盘与铁证对齐正常')
        return 'HEARTBEAT_OK'

    print(f'\n📊 偏差检测结果（总{len(alerts)}项，新增{len(new_alerts)}项）\n')
    print(f'{"#":<3} {"组合Key":<35} {"实盘WR":>7} {"铁证WR":>7} {"差距":>7} {"n":>5}  {"状态"}')
    print('─' * 80)
    for i, a in enumerate(alerts[:15], 1):
        blocked = ' [已封禁]' if a['already_blocked'] else ''
        print(f'{i:<3} {a["key"][:35]:<35} {a["live_wr"]:>6.1f}% {a["iron_wr"]:>6.1f}% {a["gap_pp"]:>+6.1f}pp {a["n"]:>5}  {a["severity"]}{blocked}')

    # 封禁建议汇总
    block_suggestions = [a for a in alerts if '封禁' in a['severity'] and not a['already_blocked']]
    if block_suggestions:
        print(f'\n🚨 建议新增到 LIVE_WR_PENALTY（{len(block_suggestions)}项）:')
        for a in block_suggestions:
            clean_key = a['key'].replace('__','_').split('__')[-1] if '__' in a['key'] else a['key']
            print(f"    '{clean_key}': ({a['live_wr']}, {a['n']}, 0.0),  # WR={a['live_wr']}% gap={a['gap_pp']}pp")

    # 保存状态
    new_state = {a['key']: {'severity': a['severity'], 'n': a['n'], 'ts': ts} for a in alerts}
    json.dump(new_state, open(state_path, 'w'), indent=2, ensure_ascii=False)

    if new_alerts:
        return f'发现 {len(new_alerts)} 项新偏差报警，详见日志'
    return 'HEARTBEAT_OK'

if __name__ == '__main__':
    result = main()
    print(f'\n[结果] {result}')
