#!/usr/bin/env python3
"""
dharma_weekly_report.py — 达摩院广场周报
设计院 · 2026-06-09 修复

历史Bug：任务 square-weekly-report 引用此文件但文件不存在。
truth_baseline_weekly.py 已包含完整周报逻辑，直接复用。

每周一 BJ 16:00 (UTC 08:00) 自动运行。
含成功→汇报URL；NO_DATA→HEARTBEAT_OK
"""
import sys, os
from pathlib import Path

# ── 苏摩·能量门控 P4 ──
try:
    import sys as _s; _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from soma_manager import can_run as _can
    if not _can(priority=4, task='dharma_weekly'):
        print('[Soma] 跳过: dharma_weekly'); import sys; sys.exit(0)
except Exception:
    pass
# ── end soma ──

BASE = Path(__file__).parent.parent
os.chdir(str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))

report = BASE / 'scripts' / 'truth_baseline_weekly.py'
if report.exists():
    exec(compile(open(report).read(), str(report), 'exec'))
else:
    print("HEARTBEAT_OK (truth_baseline_weekly.py not found)")
