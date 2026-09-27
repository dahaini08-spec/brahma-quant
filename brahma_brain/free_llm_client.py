"""
free_llm_client.py — SSOT薄代理（brahma_brain侧）
[2026-09-27 苏摩111] 双副本漂移根治：此副本曾是死路由表（minimax-m3主模型404），
导致 brahma_brain 侧调用方（fangcang_engine/llm_council懒加载/函数内import）
长期打到已下架模型。现统一单源 scripts/free_llm_client.py，本文件仅做路径代理。

接入位置：scripts/free_llm_client.py（唯一实现）
  brahma_brain/llm_council.py   → from free_llm_client import council_* (永久禁用分支)
  brahma_brain/fangcang_engine.py → from free_llm_client import chat
"""
from pathlib import Path as _Path
import sys as _sys

_scripts = str(_Path(__file__).resolve().parent.parent / 'scripts')
if _scripts not in _sys.path:
    _sys.path.insert(0, _scripts)
# 注意：包导入(brahma_brain.free_llm_client)会递归命中本文件，标准import free_llm_client
# 无法触发顶层绝对导入（sys.path优先本目录）——必须绕过：直接exec scripts单源
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('_brahma_free_llm_ssot',
    str(_Path(__file__).resolve().parent.parent / 'scripts' / 'free_llm_client.py'))
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# SSOT单源：全部公共API来自 scripts/free_llm_client.py
chat            = _mod.chat
council_llm     = _mod.council_llm
council_three_way = _mod.council_three_way
TASK_MODEL_MAP  = _mod.TASK_MODEL_MAP
FALLBACK_MODELS = _mod.FALLBACK_MODELS
API_KEY         = _mod.API_KEY
BASE_URL        = _mod.BASE_URL
BRAHMA_CONSTITUTION = _mod.BRAHMA_CONSTITUTION
llm_last_error  = _mod.llm_last_error
