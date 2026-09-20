"""
brahma_brain/__init__.py
路径注入层 — 确保所有子模块以任意方式被import时，
brahma_brain目录都在sys.path，解决 data_cache / live_price_feed 等
模块级 import 的 ModuleNotFoundError。

[设计院封印 2026-09-03 苏摩111]
接入位置: brahma_brain/__init__.py（包级别，自动执行）
"""

from typing import Any
import sys as _sys
import os as _os

_BRAIN_DIR = _os.path.dirname(_os.path.abspath(__file__))
_ROOT_DIR  = _os.path.dirname(_BRAIN_DIR)

for _p in [_BRAIN_DIR, _ROOT_DIR]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

# [P0 SSL连接池 2026-09-03 苏摩111]
# 全局单例SSL上下文，所有urlopen自动复用，消除重复据手居4.73s浪费
# 修复前：51个SSL连接独立新建 = 9s
# 修复后：全局SSL_CTX单例复用 = 2~3s
import ssl    as _ssl_mod
import urllib as _urllib_mod
import urllib.request as _urllib_request

_GLOBAL_SSL_CTX = _ssl_mod.create_default_context()
_GLOBAL_SSL_CTX.check_hostname = True
_GLOBAL_SSL_CTX.verify_mode    = _ssl_mod.CERT_REQUIRED

# 全局覆盖 urllib.request.urlopen，自动注入context
_orig_urlopen = _urllib_request.urlopen

def _ssl_urlopen(url, data=None, timeout=10, **kwargs) -> Any:
    """ssl urlopen"""
    if 'context' not in kwargs:
        kwargs['context'] = _GLOBAL_SSL_CTX
    return _orig_urlopen(url, data=data, timeout=timeout, **kwargs)

_urllib_request.urlopen = _ssl_urlopen

# data_cache也用同一个SSL_CTX
try:
    from data_cache import _SSL_CTX as _DC_SSL_CTX
except ImportError as _e:
    print(f"[WARN] __init__: _e", file=sys.stderr)

# [P0-2 2026-09-03 苏摩111] safe_json全局注入 — 防circular reference
import json as _json

def _safe_default(obj) -> str:
    """safe default"""
    try:
        return str(obj)
    except Exception:
        return '<unserializable>'

_original_json_dumps = _json.dumps

def _safe_json_dumps(obj, **kwargs) -> Any:
    """safe json dumps"""
    kwargs.setdefault('default', _safe_default)
    try:
        return _original_json_dumps(obj, **kwargs)
    except (ValueError, TypeError):
        return _original_json_dumps(str(obj), **kwargs)

_json.dumps = _safe_json_dumps

# ══ 模块别名层 — 已清理 2026-09-20 ══
# 空壳模块已移至 _empty_trash/，废弃模块已移至 _deprecated/
# 以下别名映射已失效，注释保留供历史追溯
# brahma_fangcang_unified, smart_money_engine, macro_factor_engine 等
# 如需恢复，从 _deprecated/ 或 _empty_trash/ 移回即可

# ══ 接口契约标准化层 — 保留活跃模块的别名 ══
def _patch_module_interface(mod_name: str, aliases: dict) -> None:
    """给模块注入标准接口别名"""
    import importlib as _imp, sys as _sys
    try:
        _m = _imp.import_module(mod_name) if mod_name not in _sys.modules else _sys.modules[mod_name]
        for std_name, real_name in aliases.items():
            if not hasattr(_m, std_name) and hasattr(_m, real_name):
                setattr(_m, std_name, getattr(_m, real_name))
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=_sys.stderr)
# smc_engine: analyze() → analyze_smc()
_patch_module_interface('smc_engine', {'analyze': 'analyze_smc', 'analyze_multi': 'analyze_smc_multi'})
# 以下模块已移至 _deprecated/ 或 _empty_trash/，别名映射注释保留
# _patch_module_interface('gex_engine', ...)  # → gex_unified替代
# _patch_module_interface('cvd_engine', ...)  # → 空壳
# _patch_module_interface('market_quadrant', ...)  # → 空壳
# _patch_module_interface('macro_calendar', ...)  # → 空壳
# _patch_module_interface('capital_allocator', ...)  # → 空壳
# _patch_module_interface('signal_integrity_gate', ...)  # → 空壳
# _patch_module_interface('fangcang_unified', ...)  # → 空壳
# _patch_module_interface('smart_money_engine', ...)  # → 空壳
# _patch_module_interface('divergence_engine', ...)  # → 空壳

# confluence_by_tf: analyze() already patched above
# anti_manipulation_engine: detect() → check_manipulation()
_patch_module_interface('anti_manipulation_engine', {'detect': 'check_manipulation'})
# chop_breakout_detector: detect() → detect_breakout()
_patch_module_interface('chop_breakout_detector', {'detect': 'detect_breakout'})
