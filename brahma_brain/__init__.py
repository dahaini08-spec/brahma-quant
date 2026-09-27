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
import threading as _threading_mod  # [9.27] 连接池锁
import urllib as _urllib_mod
import urllib.request as _urllib_request

_GLOBAL_SSL_CTX = _ssl_mod.create_default_context()
_GLOBAL_SSL_CTX.check_hostname = True
_GLOBAL_SSL_CTX.verify_mode    = _ssl_mod.CERT_REQUIRED

# 全局覆盖 urllib.request.urlopen，自动注入context
_orig_urlopen = _urllib_request.urlopen

# ── [9.27 L3性能优化] urlopen连接池复用（苏摩111）──────────────────────
# 根因：urllib每次请求新建TCP+TLS握手(~40ms/次)，分析链47次≈1.9s浪费
# 方案：_ssl_urlopen内部走requests.Session(HTTP keep-alive复用,~10ms/次)
# 契约保持：read()/close()/getcode()/geturl()/info()/headers/with语法/
#          HTTPError(4xx/5xx带read/code/headers)/URLError/Request对象/method=
_HTTP_SESSION = None
_SESS_LOCK = _threading_mod.Lock()

def _get_session():
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        with _SESS_LOCK:
            if _HTTP_SESSION is None:
                try:
                    import requests as _requests
                    _s = _requests.Session()
                    _s.headers.update({'User-Agent': 'Mozilla/5.0'})
                    _HTTP_SESSION = _s
                except ImportError:
                    _HTTP_SESSION = False  # requests不可用，退回urllib
    return _HTTP_SESSION

class _WrappedResponse:
    """requests.Response → urllib-compatible wrapper"""
    __slots__ = ('_resp', '_content')
    def __init__(self, resp):
        self._resp = resp
        self._content = None
    def read(self, *a):
        if self._content is None:
            self._content = self._resp.content
        return self._content
    def close(self):
        self._resp.close()
    def getcode(self):
        return self._resp.status_code
    def geturl(self):
        return self._resp.url
    def info(self):
        return self._resp.headers
    @property
    def headers(self):
        return self._resp.headers
    def __enter__(self):
        return self
    def __exit__(self, *a):
        self.close()
    def __iter__(self):
        return iter(self._resp.iter_lines())

def _pool_urlopen(url, data=None, timeout=10, method=None, _req=None, **kwargs):
    """连接池版urlopen：语义与urllib.request.urlopen一致"""
    import urllib.error as _uerr
    if _req is None and not isinstance(url, str) and url is not None:
        _req, url = url, None  # 防御：Request对象误放url位置
    if _req is not None:
        # urllib Request对象 → 展开为url/data/method/headers
        url = _req.full_url
        if data is None:
            data = _req.data
        if method is None:
            method = _req.get_method()
        req_headers = {k: v for k, v in _req.header_items()}
        if req_headers:
            kwargs.setdefault('headers', {})
            kwargs['headers'].update(req_headers)
    kwargs.pop('context', None)  # requests自带TLS验证
    headers = kwargs.pop('headers', None)
    sess = _get_session()
    if sess is False:  # requests不可用 → 退回urllib（保持原语义）
        kw = dict(kwargs or {})
        kw.setdefault('context', _GLOBAL_SSL_CTX)
        return _orig_urlopen(url, data=data, timeout=timeout, **kw)
    import requests as _requests
    try:
        resp = sess.request(
            method=method or 'GET', url=url, data=data, timeout=timeout,
            headers=headers, allow_redirects=True,
        )
    except _requests.exceptions.Timeout as _to:
        raise TimeoutError(str(_to)) from _to
    except _requests.exceptions.RequestException as _re:
        import urllib.error as _ue2
        raise _ue2.URLError(str(_re)) from _re
    if resp.status_code >= 400:
        err = _uerr.HTTPError(url, resp.status_code, resp.reason, resp.headers, None)
        err.read = lambda: resp.content
        err.fp = _io_Wrapper(resp.content) if False else None
        raise err
    return _WrappedResponse(resp)

def _ssl_urlopen(url, data=None, timeout=10, **kwargs) -> Any:
    """ssl urlopen（池化版：同host连接复用，无ctx也安全）"""
    if isinstance(url, str):
        return _pool_urlopen(url, data=data, timeout=timeout, **kwargs)
    # Request对象路径
    return _pool_urlopen(None, data=data, timeout=timeout, _req=url, **kwargs)

_urllib_request.urlopen = _ssl_urlopen

# [9.27] requests.get/post也池化：requests.get每次新建Session=每次握手
try:
    import requests as _requests_mod
    if not getattr(_requests_mod, '_brahma_pooled', False):
        _orig_get = _requests_mod.get
        _orig_post = _requests_mod.post
        def _pooled_get(url, **kw):
            _s = _get_session()
            if _s is False:
                return _orig_get(url, **kw)
            return _s.get(url, **kw)
        def _pooled_post(url, **kw):
            _s = _get_session()
            if _s is False:
                return _orig_post(url, **kw)
            return _s.post(url, **kw)
        _requests_mod.get = _pooled_get
        _requests_mod.post = _pooled_post
        _requests_mod._brahma_pooled = True
except ImportError:
    pass

# data_cache也用同一个SSL_CTX
try:
    from data_cache import _SSL_CTX as _DC_SSL_CTX
except ImportError as _e:
    print(f"[WARN] __init__: {_e}", file=sys.stderr)

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
