"""
s7_liq_config.py — 转发shim（2026-09-01 设计院精简封印）
实际常量已合并进 liq_density_engine.py
"""
from brahma_brain.liq_density_engine import (  # noqa: F401
    LIQ_DENSITY_THRESHOLDS_BY_TIER,
    LIQ_DENSITY_THRESHOLDS,
    LIQ_DENSITY_BONUS,
    LIQ_CHAOS_THRESHOLD,
    LIQ_CHAOS_PENALTY,
    LIQ_DIRECTION_RATIO,
)

__all__ = [
    'LIQ_DENSITY_THRESHOLDS_BY_TIER', 'LIQ_DENSITY_THRESHOLDS',
    'LIQ_DENSITY_BONUS', 'LIQ_CHAOS_THRESHOLD',
    'LIQ_CHAOS_PENALTY', 'LIQ_DIRECTION_RATIO',
]
