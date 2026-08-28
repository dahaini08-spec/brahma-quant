#!/usr/bin/env python3
"""
brahma_kronos.py — 统一Kronos域 v1.0
设计院 2026-08-24 重建 | 三层依赖统一入口:
  kronos_engine.py (399行) → get_kronos_score (HAR-RV + PyTorch)
  kronos_bridge.py (695行) → get_s23_kronos (bridge with cache/shadow)
  kronos_lite.py   (626行) → get_s23_score  (统计fallback)

调用优先级: bridge → engine → lite (按可用性自动降级)
向后兼容: 所有函数签名不变
新增: get_kronos_bundle() — 一次获取score+volatility+shadow
"""
from __future__ import annotations
import logging
from typing import Dict, Optional

logger = logging.getLogger('brahma_kronos')

# ══════════════════════════════════════════════════════════════════
# 1. 主入口 — 自动降级链
# ══════════════════════════════════════════════════════════════════

def get_kronos_score(symbol: str, direction: str, regime: str = '',
                     klines_15m: list | None = None) -> Dict:
    """
    Kronos评分主入口，自动降级:
    bridge(HAR-RV+shadow) → engine(PyTorch) → lite(统计) → 0分
    """
    # 优先用bridge（含缓存+shadow统计）
    try:
        from kronos_bridge import get_s23_kronos as _bridge
        r = _bridge(symbol, direction, regime, klines_15m)
        if r and r.get('score', 0) != 0:
            return r
    except Exception as e:
        logger.debug(f'kronos_bridge降级: {e}')

    # 降级到engine直接调用
    try:
        from kronos_engine import get_kronos_score as _engine
        return _engine(symbol, direction, regime, klines_15m)
    except Exception as e:
        logger.debug(f'kronos_engine降级: {e}')

    # 最终降级到lite（纯统计，无torch依赖）
    try:
        from kronos_lite import get_s23_score as _lite
        return _lite(symbol, direction, regime, klines_15m)
    except Exception as e:
        logger.debug(f'kronos_lite降级: {e}')

    return {'score': 0, 'p_up': 0.5, 'source': 'fallback', 'symbol': symbol}


def get_s23_kronos(symbol: str, direction: str, regime: str = '',
                   klines: list | None = None) -> Dict:
    """bridge接口，向后兼容"""
    try:
        from kronos_bridge import get_s23_kronos as _f
        return _f(symbol, direction, regime, klines)
    except Exception:
        return get_kronos_score(symbol, direction, regime, klines)


def get_s23_score(symbol: str, direction: str, regime: str = '',
                  klines: list | None = None) -> Dict:
    """lite接口，向后兼容"""
    try:
        from kronos_lite import get_s23_score as _f
        return _f(symbol, direction, regime, klines)
    except Exception:
        return get_kronos_score(symbol, direction, regime, klines)


def get_volatility_forecast(symbol: str, klines: list | None = None) -> Dict:
    """HAR-RV波动率预测"""
    try:
        from kronos_bridge import get_volatility_forecast as _f
        return _f(symbol, klines)
    except Exception:
        return {'forecast': 0.0, 'source': 'fallback'}


def get_shadow_stats() -> Dict:
    """bridge vs lite对比shadow统计"""
    try:
        from kronos_bridge import get_shadow_stats as _f
        return _f()
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════
# 2. 统一批量接口（新增）
# ══════════════════════════════════════════════════════════════════

def get_kronos_bundle(symbol: str, direction: str, regime: str = '',
                      klines: list | None = None) -> Dict:
    """
    一次调用获取: Kronos评分 + 波动率预测 + shadow统计
    返回: {score, p_up, vol_forecast, shadow, source}
    """
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(get_kronos_score, symbol, direction, regime, klines)
        f2 = pool.submit(get_volatility_forecast, symbol, klines)
        try:
            results['kronos'] = f1.result(timeout=12)
        except Exception:
            results['kronos'] = {'score': 0, 'p_up': 0.5, 'source': 'timeout'}
        try:
            results['vol'] = f2.result(timeout=8)
        except Exception:
            results['vol'] = {'forecast': 0.0}
    results['score']        = results['kronos'].get('score', 0)
    results['p_up']         = results['kronos'].get('p_up', 0.5)
    results['vol_forecast'] = results['vol'].get('forecast', 0.0)
    results['source']       = results['kronos'].get('source', 'unknown')
    return results
