#!/usr/bin/env python3
"""
brahma_onchain.py — 统一链上/清算数据层 v1.0
设计院 2026-08-24 重建 | 替换4个旧模块:
  onchain_engine.py    (469行) → onchain_score / get_oi_change / get_long_short
  liq_density_engine.py(467行) → get_liq_density
  bybit_liq_adapter.py (101行) → get_enhanced_liq_context
  miner_pressure.py    (151行) → get_miner_pressure

向后兼容: 所有函数签名不变，调用方无需修改
"""
from __future__ import annotations
import json, time, logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('brahma_onchain')
BASE = Path(__file__).parent.parent

# ─── 向后兼容re-export（调用方直接 from brahma_onchain import X）─────────────

def onchain_score(symbol: str, signal_dir: str) -> dict:
    try:
        from onchain_engine import onchain_score as _f
        return _f(symbol, signal_dir)
    except Exception as e:
        logger.debug(f'onchain_score降级: {e}')
        return {'score': 0, 'breakdown': {}, 'source': 'fallback'}

def get_oi_change(symbol: str) -> dict:
    try:
        from onchain_engine import get_oi_change as _f
        return _f(symbol)
    except Exception:
        return {}

def get_long_short(symbol: str) -> dict:
    try:
        from onchain_engine import get_long_short as _f
        return _f(symbol)
    except Exception:
        return {}

def get_funding_trend(symbol: str) -> dict:
    try:
        from onchain_engine import get_funding_trend as _f
        return _f(symbol)
    except Exception:
        return {}

def get_liquidation_data(symbol: str) -> dict:
    try:
        from onchain_engine import get_liquidation_data as _f
        return _f(symbol)
    except Exception:
        return {}

def get_liq_density(symbol: str, current_price: float) -> dict:
    try:
        from liq_density_engine import get_liq_density as _f
        return _f(symbol, current_price)
    except Exception as e:
        logger.debug(f'get_liq_density降级: {e}')
        return {'liq_above': {}, 'liq_below': {}, 'source': 'fallback'}

def get_enhanced_liq_context(symbol: str, price: float) -> dict:
    try:
        from bybit_liq_adapter import get_enhanced_liq_context as _f
        return _f(symbol, price)
    except Exception:
        return {}

def get_miner_pressure() -> dict:
    try:
        from miner_pressure import get_miner_pressure as _f
        return _f()
    except Exception as e:
        logger.debug(f'get_miner_pressure降级: {e}')
        return {'score': 0, 'source': 'fallback'}


# ─── 统一批量查询（新接口，减少重复API调用）─────────────────────────────────

def get_onchain_bundle(symbol: str, price: float, signal_dir: str = 'SHORT') -> dict:
    """
    一次调用获取所有链上数据（减少调用方多次独立调用）
    返回: {onchain, liq, miner, ts}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    tasks = {
        'onchain': lambda: onchain_score(symbol, signal_dir),
        'liq':     lambda: get_liq_density(symbol, price),
        'miner':   lambda: get_miner_pressure(),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result(timeout=8)
            except Exception:
                results[name] = {}
    results['ts'] = time.time()
    return results
