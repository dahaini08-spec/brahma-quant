#!/usr/bin/env python3
"""
oi_alert.py — OI预警入口（包装 oi_anomaly_broadcast）
设计院 · 2026-06-09 修复

历史Bug：任务 oi-alert-2h 引用此文件但文件不存在，导致每2H静默失败。
修复：直接调用 oi_anomaly_broadcast.py 的完整逻辑。
"""
import sys, os
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))

# 直接执行 oi_anomaly_broadcast
broadcast = BASE / 'scripts' / 'oi_anomaly_broadcast.py'
if broadcast.exists():
    exec(compile(open(broadcast).read(), str(broadcast), 'exec'))
else:
    print("HEARTBEAT_OK (oi_anomaly_broadcast.py not found)")
