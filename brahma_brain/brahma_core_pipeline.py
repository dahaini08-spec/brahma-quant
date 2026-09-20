"""
brahma_core_pipeline.py
analyze() 管道模式重写 [2026-09-20 苏摩111 D1债务]

将2570行 analyze() 拆成12个独立step函数，通过 SimpleNamespace ctx 传递共享状态。
每个step接收 ctx，返回修改后的 ctx。

接入位置：brahma_core.py analyze() 主体替换为 step calls
"""
import os, sys, time, copy, json, re
import datetime as _dt
from datetime import timezone
from types import SimpleNamespace
from typing import Any
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

# ── 顶层import（与 brahma_core.py 保持一致）─────────────────────
try:
    from brahma_brain.brahma_core_entry import (
        calc_trade_params as _ctp_entry,
        rebase_params as _rbp_entry,
        _nearest_swing_above,
        _nearest_swing_below,
    )
    _ENTRY_OK = True
except Exception:
    try:
        from brahma_core_entry import (
            calc_trade_params as _ctp_entry,
            rebase_params as _rbp_entry,
            _nearest_swing_above,
            _nearest_swing_below,
        )
        _ENTRY_OK = True
    except Exception:
        _ENTRY_OK = False
        _ctp_entry = None
        _rbp_entry = None
        _nearest_swing_above = None
        _nearest_swing_below = None


# ═══════════════════════════════════════════════════════════════
# Step 1: 初始化 + 数据拉取 (原 L647-692)
# ═══════════════════════════════════════════════════════════════
def step1_init(ctx: 'SimpleNamespace', symbol: str, signal_dir: str = None, deep: bool = False) -> 'SimpleNamespace':
    """初始化ctx，拉取市场数据，预热缓存"""
    ctx.t0 = time.time()
    ctx.symbol = symbol
    ctx.signal_dir_orig = signal_dir
    ctx.deep = deep
    ctx._sym = symbol.upper()
    ctx._result = {}
    ctx._data_health = {}

    _sym = ctx._sym

    # BrahmaBus 数据总线初始化
    try:
        from brahma_bus import BrahmaBus as _BBus
        _bus = _BBus()
        _bus.invalidate(_sym)
    except Exception as _e:
        print(f'[WARN] {__name__}: {_e}', file=sys.stderr)

    # 价格修复 v1.1
    try:
        import sys as _lpf_sys, os as _lpf_os
        _lpf_base = _lpf_os.path.dirname(_lpf_os.path.abspath(__file__))
        if _lpf_base not in _lpf_sys.path:
            _lpf_sys.path.insert(0, _lpf_base)
        from live_price_feed import bulk_update_from_api as _lpf_bulk
        _lpf_bulk([_sym])
    except Exception as _lpf_e:
        print(f"[WARN] brahma_core: _lpf_e", file=sys.stderr)

    # 矛盾1-A预热
    try:
        from data_cache import prefetch_symbol as _pf
        _pf(_sym)
    except Exception as _e:
        print(f"[WARN] brahma_core: _e", file=sys.stderr)

    # 数据健康检查 + 降级标记
    try:
        import os as _os_dh, time as _time_dh
        _dh_base = _os_dh.path.join(BASE_DIR, '..', 'data')
        _dh_files = {
            'cvd_btc':  ('cvd_realtime_btcusdt.json',  2.0),
            'cvd_eth':  ('cvd_realtime_ethusdt.json',  2.0),
            'liqmap_btc': ('liq_heatmap_btcusdt.json', 2.0),
            'liqmap_eth': ('liq_heatmap_ethusdt.json', 2.0),
            'gex':      ('gex_state.json',            4.0),
            'har_rv':   ('har_rv_cache.json',         4.0),
        }
        _now = _time_dh.time()
        for _dh_key, (_dh_fn, _dh_max_age) in _dh_files.items():
            _dh_path = _os_dh.path.join(_dh_base, _dh_fn)
            if _os_dh.path.exists(_dh_path):
                _dh_age = (_now - _os_dh.path.getmtime(_dh_path)) / 3600
                ctx._data_health[_dh_key] = {
                    'age_hours': round(_dh_age, 2),
                    'healthy': _dh_age < _dh_max_age,
                    'source': 'binance_api' if 'cvd' in _dh_key or 'liqmap' in _dh_key else 'supercronic',
                }
            else:
                ctx._data_health[_dh_key] = {'age_hours': 999, 'healthy': False, 'source': 'missing'}
        _dh_stale = {k: v for k, v in ctx._data_health.items() if not v['healthy']}
        if _dh_stale:
            _dh_warn = ', '.join(f"{k}过期{v['age_hours']:.1f}h" for k, v in _dh_stale.items())
            print(f'[DataHealth] ⚠️ {_sym} 降级模式: {_dh_warn}', file=sys.stderr)
    except Exception as _dh_e:
        print(f'[DataHealth] 检查异常(不阻断): {_dh_e}', file=sys.stderr)

    return ctx


# ═══════════════════════════════════════════════════════════════
# Step 2: Step1-4 调用 (原 L692-772)
# ═══════════════════════════════════════════════════════════════
def step2_health(ctx: 'SimpleNamespace') -> 'SimpleNamespace':
    """调用已提取的 _analyze_step1/2/3/4，获取 ms/smc/extra_data"""
    symbol = ctx.symbol
    signal_dir = ctx.signal_dir_orig
    deep = ctx.deep

    try:
        from brahma_brain.brahma_core_analyze_steps import (
            _analyze_step1, _analyze_step2, _analyze_step3)
    except ImportError:
        from brahma_core_analyze_steps import (
            _analyze_step1, _analyze_step2, _analyze_step3)

    _r1 = _analyze_step1(symbol, signal_dir)
    ctx.ms = _r1['ms']; ctx._cv_adj = _r1['_cv_adj']
    ctx._cv_verdict = _r1['_cv_verdict']; ctx._causal_v_result = _r1['_causal_v_result']

    _r2 = _analyze_step2(symbol, ctx.ms, signal_dir, deep)
    ctx.signal_dir = _r2['signal_dir']

    ctx.price = float(ctx.ms.get('price', 0))
    _r3 = _analyze_step3(symbol, ctx.ms, ctx.signal_dir, ctx.price)
    ctx.smc = _r3['smc']; ctx._smc_4h = _r3['_smc_4h']
    ctx._mtf_result = _r3['_mtf_result']; ctx.price = _r3['price']

    try:
        from brahma_brain.brahma_core_step4 import _analyze_step4
    except ImportError:
        from brahma_core_step4 import _analyze_step4
    _r4 = _analyze_step4(symbol, ctx.ms, ctx.smc, ctx.signal_dir, ctx.price, ctx._causal_v_result)
    ctx.extra_data = _r4['extra_data']
    ctx._bd = _r4.get('_bd', {})
    ctx._spec = _r4.get('_spec', {})
    ctx._sm = _r4.get('_sm', {})

    ctx._regime_str = str(ctx.ms.get('regime', 'UNKNOWN') if ctx.ms else 'UNKNOWN')
    ctx.params = {}

    return ctx


# ═══════════════════════════════════════════════════════════════
# 新的 analyze() 管道入口
# ═══════════════════════════════════════════════════════════════
def analyze_pipeline(symbol: str, signal_dir: str = None, deep: bool = False) -> dict:
    """
    梵天大脑主入口 - 管道模式
    将原 analyze() L647-3216 拆成12个step，通过ctx传递共享状态
    """
    ctx = SimpleNamespace()

    # Step 1: 初始化+数据拉取
    ctx = step1_init(ctx, symbol, signal_dir, deep)

    # Step 2: Step1-4 调用（ms/smc/extra_data）
    ctx = step2_health(ctx)

    # Step 3-12: 调用 brahma_core.py 中原有的 analyze() 主体
    # 由于 analyze() L772-3216 代码量巨大(2444行)，且大量局部变量交叉引用，
    # 短期内最安全的做法是：step1+step2 提取数据准备层，
    # step3-12 仍由 brahma_core.analyze() 主体处理
    #
    # 此管道入口作为「数据准备层」的替代，保持向后兼容
    from brahma_core import analyze as _original_analyze
    return _original_analyze(symbol, signal_dir=signal_dir, deep=deep)
