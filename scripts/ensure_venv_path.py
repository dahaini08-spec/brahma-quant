"""
ensure_venv_path.py — 确保 venv 包路径在 sys.path 中
所有需要 torch/safetensors/hf_hub 的脚本 import 此模块即可

用法:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.ensure_venv_path import *   # 或直接 import ensure_venv_path
"""
import sys
from pathlib import Path

_VENV_SITE = Path(__file__).parent.parent / 'venv' / 'lib' / 'python3.11' / 'site-packages'
_VENV_STR  = str(_VENV_SITE)

if _VENV_SITE.exists() and _VENV_STR not in sys.path:
    sys.path.insert(1, _VENV_STR)

# libgomp.so.1 持久化 (torch自带)
import os as _os, pathlib as _pl

_GOMP_DST  = _pl.Path('/usr/local/lib/libgomp.so.1')
_GOMP_SRC  = _VENV_SITE.parent.parent.parent / 'venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1'
_GOMP_SRC2 = _pl.Path('/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1')

def _ensure_libgomp():
    for src in [_GOMP_SRC, _GOMP_SRC2]:
        if src.exists() and not _GOMP_DST.exists():
            try:
                _GOMP_DST.symlink_to(src)
                _os.system('ldconfig 2>/dev/null')
            except Exception:
                pass
            break

_ensure_libgomp()
