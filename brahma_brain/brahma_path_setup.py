"""
brahma_path_setup.py — 梵天系统统一路径管理
[2026-10-05 苏摩111封印]

使用方式：所有需要路径设置的模块顶部 import brahma_path_setup  # noqa
替代之前 348 处散弹式 sys.path.insert(0, ...) 调用

原则：
- 幂等：多次 import 不重复插入
- 线程安全：检查后插入，避免并发竞态
- brahma.pth 已存在作为兜底，本模块为显式保障
"""
import sys
from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parent  # trading-system/brahma_brain/
_TRADING_ROOT = _ROOT.parent           # trading-system/
_SCRIPTS = _TRADING_ROOT / 'scripts'
_BRAIN   = _TRADING_ROOT / 'brahma_brain'

_PATHS = [
    str(_TRADING_ROOT),
    str(_SCRIPTS),
    str(_BRAIN),
]

for _p in _PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)

del _p, _P, _ROOT, _TRADING_ROOT, _SCRIPTS, _BRAIN, _PATHS
