#!/usr/bin/env python3
"""
brahma_engine.py — [设计院封印 2026-08-09 苏摩111]
★ 合并决策：brahma_engine 合并进 brahma_core，此文件为薄 shim

历史：
  brahma_core.py  = 第一版，runner/scan_all/全系统一直调用
  brahma_engine.py = 后期重构版，但从未完成迁移
  两轨道并行 → 修了又修的根本原因

解法：
  brahma_core 是唯一真相
  此文件所有调用全部代理到 brahma_core
  40个调用brahma_core的脚本不受影响
  37个调用brahma_engine的脚本自动走brahma_core

不要修改此文件。要修改分析逻辑，直接改 brahma_core.py。
"""

# ── 全量代理到 brahma_core ──────────────────────────────────
from brahma_brain.brahma_core import (
    analyze,
    calc_trade_params,
    format_report,
    rebase_params,
    _nearest_swing_above,
    _nearest_swing_below,
)

# brahma_engine 独有函数：_calc_mtf_alignment → 已合并进 brahma_core
# 如果有脚本直接调用此函数，从 brahma_core 提供
try:
    from brahma_brain.brahma_core import _calc_mtf_alignment
except ImportError:
    def _calc_mtf_alignment(*args, **kwargs):
        return {}

# 向后兼容：部分脚本可能用 from brahma_engine import xxx
__all__ = ['analyze', 'calc_trade_params', 'format_report',
           'rebase_params', '_calc_mtf_alignment',
           '_nearest_swing_above', '_nearest_swing_below']
