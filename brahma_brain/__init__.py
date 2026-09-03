"""
brahma_brain/__init__.py
路径注入层 — 确保所有子模块以任意方式被import时，
brahma_brain目录都在sys.path，解决 data_cache / live_price_feed 等
模块级 import 的 ModuleNotFoundError。

[设计院封印 2026-09-03 苏摩111]
接入位置: brahma_brain/__init__.py（包级别，自动执行）
"""
import sys as _sys
import os as _os

_BRAIN_DIR = _os.path.dirname(_os.path.abspath(__file__))
_ROOT_DIR  = _os.path.dirname(_BRAIN_DIR)

for _p in [_BRAIN_DIR, _ROOT_DIR]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
