"""macro_engine.py — 转发shim（2026-09-01 设计院精简封印）
实际代码已合并进 narrative_engine.py

INTERFACE CONTRACT:
  转发层，将 macro_engine 导入请求重定向至 narrative_engine.py
  所有公开函数与 narrative_engine 一致
"""
from brahma_brain.narrative_engine import *  # noqa: F401,F403
