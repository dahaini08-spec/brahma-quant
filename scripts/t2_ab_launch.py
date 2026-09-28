#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t2_ab_launch.py — T2 A/B对照盘启动器 [梵天2.0 T2 2026-09-27 苏摩111]
接入位置: reports/brahma_2.0_design.md §6 T2 / PAUSED_20260927.md(停机恢复) / process_resurrect.sh

职责:
  1. 前置检查: 纯度(DB无测试行/影子无烟测记录)、影子接线就位、risk_gate可评估
  2. 恢复B线执行进程: supercronic(带BRAHMA_SHADOW=1) + paper_tp_monitor守护
  3. 写T2启动标记 data/t2_ab_state.json（基线hash锚定）

用法: python3 scripts/t2_ab_launch.py [--check-only]
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

T2_STATE = BASE / 'data' / 't2_ab_state.json'
PAUSED_DOC = BASE / 'PAUSED_20260927.md'


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def preflight() -> list:
    """T2启动前置检查，返回[失败项]，空=全绿"""
    fails = []
    # 1. signals库无测试行
    try:
        import sqlite3
        conn = sqlite3.connect(BASE / 'data' / 'brahma_signals.db')
        n = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_id LIKE 'SMOKE_TEST_%' OR signal_id LIKE 'test_%'"
        ).fetchone()[0]
        conn.close()
        if n:
            fails.append(f'signals库仍有{n}条测试行 → 跑 scripts/cleanup_test_signals.py')
    except Exception as e:
        fails.append(f'signals库检查失败: {e}')
    # 2. shadow_decisions无烟测记录
    sd = BASE / 'data' / 'shadow_decisions.jsonl'
    if sd.exists():
        for line in sd.read_text().strip().split('\n'):
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get('regime') == 'TEST' or str(o.get('signal_id') or '').startswith(('SMOKE', 'TEST')):
                fails.append('shadow_decisions.jsonl有烟测记录 → 跑 scripts/cleanup_test_signals.py')
                break
    # 3. risk_gate可评估
    try:
        from brahma_brain import risk_gate
        v = risk_gate.evaluate(
            {'symbol': 'BTCUSDT', 'side': 'LONG', 'regime': 'BEAR_TREND', 'score': 150,
             'sl_pct': 2.0, 'price': 60000, 'leverage': 5, 'nav_pct': 5, 'atr1h': 500},
            {'open_positions': [], 'now_ts': time.time()})
        if v.get('rule') != 'R3':
            fails.append(f'risk_gate R3(BEAR_RECOVERY/体制)映射异常: {v}')
    except Exception as e:
        fails.append(f'risk_gate不可用: {e}')
    # 4. 复盘cron在位
    cron = (BASE / 'brahma_crontab.txt').read_text()
    if 'divergence_report.py' not in cron:
        fails.append('brahma_crontab.txt缺divergence_report行')
    if 'replay_ci' not in cron:
        fails.append('brahma_crontab.txt缺replay_ci行')
    if 'state_refresh' not in cron:
        fails.append('brahma_crontab.txt缺state_refresh行（信号生产层）')
    return fails


def start_line_b() -> dict:
    """恢复B线：supercronic带BRAHMA_ENFORCE=1（2.0转正）+ 复活器
    [梵天2.0转正 2026-09-27 苏摩111] 影子期结束，L2接管拦截权"""
    env = os.environ.copy()
    env['BRAHMA_ENFORCE'] = '1'
    env['BRAHMA_SHADOW'] = '1'
    procs = {}
    # supercronic (setsid脱离终端，环境变量传给子进程)
    if not subprocess.run(['pgrep', '-f', 'supercronic.*brahma_crontab'],
                          capture_output=True).returncode == 0:
        with open(BASE / 'logs' / 'supercronic.log', 'ab') as logf:
            subprocess.Popen(['setsid', str(BASE / 'start_supercronic.sh')],
                             env=env, stdout=logf, stderr=logf,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        procs['supercronic'] = 'started'
        time.sleep(2)
    else:
        procs['supercronic'] = 'already-running'
    return procs


def main() -> int:
    check_only = '--check-only' in sys.argv
    fails = preflight()
    print('== T2 A/B对照盘 preflight ==')
    for f in fails:
        print(f'  ❌ {f}')
    if fails:
        print('🚨 前置检查未过，T2不开跑')
        return 1
    print('  ✅ 纯度/影子接线/风控/复盘cron 全绿')

    # 基线hash锚定（账本+订单文件快照）
    anchors = {}
    for name, p in (('paper_ledger_log', BASE / 'data' / 'paper_ledger_log.jsonl'),
                    ('paper_orders', BASE / 'data' / 'paper_orders.jsonl'),
                    ('paper_positions', BASE / 'data' / 'paper_positions.jsonl')):
        if p.exists():
            anchors[name] = _sha256(p)
    state = {
        'launched_at': time.time(),
        'launched_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'baseline_anchors': anchors,
        'mode': 'shadow',
        'duration_days': 14,
        'end_ts': time.time() + 14 * 86400,
        'env': {'BRAHMA_SHADOW': '1'},
    }
    if check_only:
        print('  (check-only: 不启动进程，不写状态)')
        return 0
    T2_STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    procs = start_line_b()
    print(f'  🚀 T2启动: {json.dumps(procs)} | 基线锚定{len(anchors)}文件 | 影子期14天')
    return 0


if __name__ == '__main__':
    sys.exit(main())
