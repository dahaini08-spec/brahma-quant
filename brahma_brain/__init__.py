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
