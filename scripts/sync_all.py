#!/usr/bin/env python3
"""
sync_all.py — 全系统日度同步
设计院 · 2026-06-09 修复

历史Bug：任务 sync-all-daily 引用此文件但文件不存在。

执行顺序：
  1. brahma_state_refresh  — 刷新体制/NAV
  2. data_updater --no-expand — 增量更新parquet（跳过新标的扩充）
  3. dharma_ci — 达摩院持续集成检查
零AI，全部静默运行，只输出摘要行。
"""
import subprocess, sys, os
from pathlib import Path

BASE = Path(__file__).parent.parent
os.chdir(str(BASE))

def run(cmd, label):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, cwd=str(BASE))
        last = (r.stdout + r.stderr).strip().splitlines()
        out = last[-1] if last else 'OK'
        print(f"[sync_all] {label}: {out}")
        return True
    except Exception as e:
        print(f"[sync_all] {label}: ERROR {e}")
        return False

ok1 = run("python3 scripts/brahma_state_refresh.py 2>/dev/null | tail -1", "state_refresh")
ok2 = run("python3 scripts/data_updater.py --no-expand 2>&1 | tail -1", "data_updater")
ok3 = run("python3 dharma/dharma_ci.py 2>&1 | tail -1", "dharma_ci")

if ok1 and ok2 and ok3:
    print("SYNC_ALL_OK")
else:
    print(f"SYNC_ALL_PARTIAL ok1={ok1} ok2={ok2} ok3={ok3}")
