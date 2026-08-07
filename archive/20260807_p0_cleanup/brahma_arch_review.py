#!/usr/bin/env python3
"""
brahma_arch_review.py — 梵天架构日报
设计院 · 2026-07-15

聚合 coordinator + self_heal + performance_logger 输出架构健康日报
"""
import sys, json, time, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent

def run_arch_review():
    now = time.time()
    ts  = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
    report = []

    # 1. coordinator health
    try:
        r = subprocess.run(
            ['python3', str(BASE/'scripts/brahma_coordinator.py'), 'health_check'],
            capture_output=True, text=True, timeout=15
        )
        report.append(r.stdout.strip())
    except Exception as e:
        report.append(f'coordinator异常: {e}')

    # 2. 信号统计
    try:
        sig_f = BASE / 'data/live_signal_log.jsonl'
        lines = sig_f.read_text().strip().splitlines() if sig_f.exists() else []
        today = [json.loads(l) for l in lines if (now - float(json.loads(l).get('ts',0))) < 86400]
        valid = [s for s in today if s.get('valid') and float(s.get('score',0)) >= 155]
        report.append(f'[信号] 今日{len(today)}条 | 有效{len(valid)}条')
    except: pass

    # 3. 持仓状态
    try:
        wp  = BASE / 'data/wuqu_positions.json'
        raw = json.loads(wp.read_text()) if wp.exists() else []
        active  = [p for p in raw if isinstance(p,dict) and p.get('status') in ('open','active')]
        closed  = [p for p in raw if isinstance(p,dict) and p.get('status') == 'closed']
        pnl_day = sum(float(p.get('realized_pnl',0)) for p in closed
                      if (now - time.mktime(time.strptime(p.get('closed_at','2000-01-01T00:00:00Z'),
                          '%Y-%m-%dT%H:%M:%SZ'))) < 86400)
        report.append(f'[持仓] 活跃{len(active)}仓 | 今日已平{len([p for p in closed if True])}仓 | 今日已实现${pnl_day:+.4f}')
    except: pass

    # 4. 性能日志
    try:
        pf = BASE / 'data/live_performance_log.jsonl'
        lines2 = pf.read_text().strip().splitlines() if pf.exists() else []
        print(f'[架构日报] {ts}')
        for line in report:
            print(line)
        print(f'[perf_log] 累计{len(lines2)}条交易记录')
    except:
        print(f'[架构日报] {ts}')
        for line in report:
            print(line)

    # 无异常则静默
    has_error = any('ERROR' in r or '❌' in r for r in report)
    if not has_error:
        print('HEARTBEAT_OK')

if __name__ == '__main__':
    run_arch_review()
