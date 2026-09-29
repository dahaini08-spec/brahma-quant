"""cvd_engine.py — CVD引擎 shim
[2026-09-15 苏摩111] 9.1精简封印合并到 volume_unified.py
此文件为接口兼容shim，实际逻辑在 volume_unified.analyze_volume()
"""
from brahma_brain.volume_unified import analyze_volume as calc_cvd, analyze_volume as run

# 接口契约
calc_cvd = calc_cvd
run = run
