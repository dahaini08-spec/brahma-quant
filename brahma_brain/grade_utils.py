"""
grade_utils.py — 转发shim（2026-09-01 设计院精简封印）
实际代码已合并进 math_utils.py
保留本文件确保所有调用方 import 不变
"""
from brahma_brain.math_utils import (  # noqa: F401
    GRADE_LABEL_MAP,
    grade_to_label,
    parse_grade,
    enrich_signal_grade,
)

__all__ = ['GRADE_LABEL_MAP', 'grade_to_label', 'parse_grade', 'enrich_signal_grade']
