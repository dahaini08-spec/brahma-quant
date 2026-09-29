"""capital_allocator.py — 资金分配器 shim
[2026-09-15 苏摩111] 9.1精简封印合并到 position_sizer.py
此文件为接口兼容shim，实际逻辑在 position_sizer.get_position_pct()
"""
from brahma_brain.position_sizer import get_position_pct as allocate, get_position_pct as calc_size, get_position_pct as run

# 接口契约
allocate = allocate
calc_size = calc_size
run = run
