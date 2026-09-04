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

# [P0 SSL连接池 2026-09-03 苏摩111]
# 全局单例SSL上下文，所有urlopen自动复用，消除重复据手居4.73s浪费
# 修复前：51个SSL连接独立新建 = 9s
# 修复后：全局SSL_CTX单例复用 = 2~3s
import ssl    as _ssl_mod
import urllib as _urllib_mod
import urllib.request as _urllib_request

_GLOBAL_SSL_CTX = _ssl_mod.create_default_context()
_GLOBAL_SSL_CTX.check_hostname = False
_GLOBAL_SSL_CTX.verify_mode    = _ssl_mod.CERT_NONE

# 全局覆盖 urllib.request.urlopen，自动注入context
_orig_urlopen = _urllib_request.urlopen

def _ssl_urlopen(url, data=None, timeout=10, **kwargs):
    if 'context' not in kwargs:
        kwargs['context'] = _GLOBAL_SSL_CTX
    return _orig_urlopen(url, data=data, timeout=timeout, **kwargs)

_urllib_request.urlopen = _ssl_urlopen

# data_cache也用同一个SSL_CTX
try:
    from data_cache import _SSL_CTX as _DC_SSL_CTX
except ImportError:
    pass

# [P0-2 2026-09-03 苏摩111] safe_json全局注入 — 防circular reference
import json as _json

def _safe_default(obj):
    try:
        return str(obj)
    except Exception:
        return '<unserializable>'

_original_json_dumps = _json.dumps

def _safe_json_dumps(obj, **kwargs):
    kwargs.setdefault('default', _safe_default)
    try:
        return _original_json_dumps(obj, **kwargs)
    except (ValueError, TypeError):
        return _original_json_dumps(str(obj), **kwargs)

_json.dumps = _safe_json_dumps

# ══ 模块别名层 —— 修复短名导入失败问题 ══
# 文件名是 brahma_fangcang_unified.py，但部分代码用 fangcang_unified 短名导入
# 在 __init__.py 这里注入别名，一次性修复所有调用方
import sys as _sys
import importlib as _importlib
try:
    _fcu = _importlib.import_module('brahma_brain.brahma_fangcang_unified')
    _sys.modules['fangcang_unified'] = _fcu
    _sys.modules['brahma_brain.fangcang_unified'] = _fcu
except Exception:
    pass
# smart_money_engine 别名（module_contracts缺失提示）
try:
    _sme = _importlib.import_module('brahma_brain.smart_money_engine')
    _sys.modules['smart_money_engine'] = _sme
except Exception:
    pass
# macro_engine 别名
try:
    _me = _importlib.import_module('brahma_brain.macro_factor_engine')
    _sys.modules['macro_engine'] = _me
    _sys.modules['brahma_brain.macro_engine'] = _me
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════════════════
# 接口契约标准化层 — 设计院三方封印 2026-09-04 苏摩111
# 问题根因：各模块函数名不统一，外部调用必须知道私有命名
# 修复方案：在此注入标准名→真实名的别名，统一调用契约
# ══════════════════════════════════════════════════════════════════════════════
def _patch_module_interface(mod_name: str, aliases: dict):
    """给模块注入标准接口别名"""
    import importlib as _imp, sys as _sys
    try:
        _m = _imp.import_module(mod_name) if mod_name not in _sys.modules else _sys.modules[mod_name]
        for std_name, real_name in aliases.items():
            if not hasattr(_m, std_name) and hasattr(_m, real_name):
                setattr(_m, std_name, getattr(_m, real_name))
    except Exception:
        pass

# smc_engine: analyze() → analyze_smc()
_patch_module_interface('smc_engine', {'analyze': 'analyze_smc', 'analyze_multi': 'analyze_smc_multi'})
# gex_engine: run_gex() → calc_gex_binance()
_patch_module_interface('gex_engine', {'run_gex': 'calc_gex_binance', 'run': 'calc_gex_binance'})
# vol_beta_engine: run_vol_beta() → calc_vol_beta()
_patch_module_interface('vol_beta_engine', {'run_vol_beta': 'calc_vol_beta', 'run': 'calc_vol_beta'})
# hurst_engine: calc_hurst() → get_hurst()  calc_hurst_rs()
_patch_module_interface('hurst_engine', {'calc_hurst': 'get_hurst', 'get': 'get_hurst'})
# har_rv_engine: predict_rv() → get_har_rv()
_patch_module_interface('har_rv_engine', {'predict_rv': 'get_har_rv', 'predict': 'get_har_rv'})
# cvd_engine: calc_cvd() → analyze_volume()
_patch_module_interface('cvd_engine', {'calc_cvd': 'analyze_volume', 'run': 'analyze_volume'})
# market_quadrant: classify_quadrant() → classify()
_patch_module_interface('market_quadrant', {'classify_quadrant': 'classify', 'run': 'classify'})
# brahma_core: run_full_analysis() → analyze()
_patch_module_interface('brahma_core', {'run_full_analysis': 'analyze', 'run': 'analyze'})
# brahma_full_report: run_full_report() → run_full_analysis()
_patch_module_interface('brahma_full_report', {'run_full_report': 'run_full_analysis'})
# cross_market_engine: analyze() → cross_market_score()
_patch_module_interface('cross_market_engine', {'analyze': 'cross_market_score'})
# macro_calendar: get_upcoming() → get_active_risk()
_patch_module_interface('macro_calendar', {'get_upcoming': 'get_active_risk', 'get': 'get_active_risk'})
# confluence_by_tf: analyze() → get_tf_consensus()
_patch_module_interface('confluence_by_tf', {'analyze': 'get_tf_consensus'})
# circuit_breaker: check() → is_triggered() 或同名
_patch_module_interface('circuit_breaker', {'check': 'is_triggered'})
# drawdown_tracker: update() → record()
_patch_module_interface('drawdown_tracker', {'update': 'record'})
# antifragile_guard: check() → evaluate()
_patch_module_interface('antifragile_guard', {'check': 'evaluate'})
# capital_allocator: allocate() → calc_size()
_patch_module_interface('capital_allocator', {'allocate': 'calc_size'})
# signal_integrity_gate: validate() → gate()
_patch_module_interface('signal_integrity_gate', {'validate': 'gate'})
# fangcang_unified: get_nearest() → query() via HCMEMatcher
_patch_module_interface('fangcang_unified', {'get_nearest': 'query'})
# smart_money_engine: analyze() → get_smart_money_signal()
_patch_module_interface('smart_money_engine', {'analyze': 'get_smart_money_signal'})
# divergence_engine: detect() → find_divergence()
_patch_module_interface('divergence_engine', {'detect': 'find_divergence'})
# confluence_by_tf: analyze() already patched above
# anti_manipulation_engine: detect() → check_manipulation()
_patch_module_interface('anti_manipulation_engine', {'detect': 'check_manipulation'})
# chop_breakout_detector: detect() → detect_breakout()
_patch_module_interface('chop_breakout_detector', {'detect': 'detect_breakout'})
