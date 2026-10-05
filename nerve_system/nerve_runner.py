#!/usr/bin/env python3
"""
nerve_runner.py — 太医官独立运行入口
供 cron / 手动调用，不依赖外部模块导入路径

用法：
  python3 nerve_system/nerve_runner.py          # 完整扫描
  python3 nerve_system/nerve_runner.py --fast   # 快速模式（跳过L4 AST）
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nerve_system.nerve_core import run_nerve, format_report

if __name__ == "__main__":
    fast = "--fast" in sys.argv
    summary = run_nerve(fast=fast)
    print(format_report(summary))
