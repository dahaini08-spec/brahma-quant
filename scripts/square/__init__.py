"""
trading-system/scripts/square/__init__.py
代理层：预注册 workspace/scripts/square 子模块，防止遮蔽
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_WS_SQUARE = _os.path.normpath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                  '..', '..', '..', 'scripts', 'square')
)

def _register(name):
    full = f'square.{name}'
    if full in _sys.modules:
        return _sys.modules[full]
    path = _os.path.join(_WS_SQUARE, f'{name}.py')
    if not _os.path.exists(path):
        return None
    spec = _ilu.spec_from_file_location(full, path)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[full] = mod
    try:
        spec.loader.exec_module(mod)
    except SyntaxError:
        del _sys.modules[full]
        return None
    return mod

# 启动时预注册核心子模块（跳过有语法错误的）
_SUBMODULES = [
    'poster', 'kol_templates', 'brand_voice', 'quality_gate',
    'bureau_gates', 'bureau_publisher', 'config', 'sq_key_loader',
    'hot_topics_post', 'brahma_post', 'signal_tracker', 'state_manager',
]
for _name in _SUBMODULES:
    try:
        _m = _register(_name)
        if _m is not None:
            globals()[_name] = _m
    except Exception:
        pass

def __getattr__(attr):
    m = _register(attr)
    if m is not None:
        globals()[attr] = m
        return m
    raise AttributeError(f"module 'square' has no attribute {attr!r}")
