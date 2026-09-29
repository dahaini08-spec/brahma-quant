"""macro_calendar.py — 宏观事件日历 shim
[2026-09-15 苏摩111] 9.1精简封印合并到 narrative_engine.py
此文件为接口兼容shim，实际逻辑在 narrative_engine.get_active_risk()
"""
from brahma_brain.narrative_engine import get_active_risk as get_upcoming, get_active_risk as get, get_active_risk as run

# 接口契约
get_upcoming = get_upcoming
get = get
run = run
