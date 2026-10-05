"""
scripts/brahma_path_setup.py — thin wrapper
[2026-10-05 苏摩111封印]
"""
import sys
from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parent.parent  # trading-system/
_BRAIN = _ROOT / 'brahma_brain'

for _p in [str(_ROOT), str(_ROOT / 'scripts'), str(_BRAIN)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

del _p, _P, _ROOT, _BRAIN
