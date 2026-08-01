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
