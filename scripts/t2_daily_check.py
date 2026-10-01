#!/usr/bin/env python3
"""
t2_daily_check.py — T2每日对标三件套合并入口
[2.0封印 2026-10-01 苏摩111]

替代原3条独立cron（均在15:xx UTC）：
  50 15 * * *  divergence_report.py   timeout=30s
  55 15 * * *  replay_ci.py           timeout=60s
  30 15 * * *  drift_checker.py       timeout=60s

改为单条：30 15 * * * python3 scripts/t2_daily_check.py

顺序执行，任一失败不影响后续，总超时150s。
接入位置：brahma_crontab.txt
"""
import subprocess, sys, time
from pathlib import Path

BASE = Path(__file__).parent.parent
SCRIPTS = [
    ('divergence_report', ['python3', str(BASE/'scripts/divergence_report.py')], 30),
    ('replay_ci',         ['python3', str(BASE/'scripts/replay_ci.py'), '--days', '2'], 60),
    ('drift_checker',     ['python3', str(BASE/'scripts/drift_checker.py')], 60),
]

total_start = time.time()
results = {}

for name, cmd, timeout in SCRIPTS:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, timeout=timeout, capture_output=False)
        elapsed = round(time.time() - t0, 1)
        results[name] = 'OK' if r.returncode == 0 else f'EXIT={r.returncode}'
        print(f'[t2_daily_check] {name}: {results[name]} ({elapsed}s)')
    except subprocess.TimeoutExpired:
        results[name] = f'TIMEOUT>{timeout}s'
        print(f'[t2_daily_check] {name}: {results[name]}')
    except Exception as e:
        results[name] = f'ERR:{e}'
        print(f'[t2_daily_check] {name}: {results[name]}')

total = round(time.time() - total_start, 1)
ok_count = sum(1 for v in results.values() if v == 'OK')
print(f'[t2_daily_check] 完成 {ok_count}/{len(SCRIPTS)} OK, 总耗时 {total}s')
