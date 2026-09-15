"""market_quadrant.py — 四象限市场状态 shim
[2026-09-15 苏摩111] 9.1精简封印合并到 regime_scorer.py
此文件为接口兼容shim，实际逻辑在 regime_scorer.get_quadrant()
"""
from brahma_brain.regime_scorer import get_quadrant as classify_quadrant, get_quadrant as classify, get_quadrant as run

# 接口契约
classify_quadrant = classify_quadrant
classify = classify
run = run
