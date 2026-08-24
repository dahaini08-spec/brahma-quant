#!/usr/bin/env python3
"""
brahma_ml_engine.py — 统一ML/量化域 v1.0
设计院 2026-08-24 重建 | 替换4个旧模块:
  har_rv_engine.py   (166行) → HAR-RV波动率预测
  hurst_engine.py    (200行) → Hurst指数体制验证
  ic_tracker.py      (220行) → IC信息系数追踪
  online_learner_v2.py(240行) → 在线学习/信号权重更新

向后兼容: 所有函数签名不变
新增: get_ml_bundle() — 一次调用获取HAR-RV+Hurst+IC
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional

logger = logging.getLogger('brahma_ml_engine')

# ══════════════════════════════════════════════════════════════════
# 1. HAR-RV波动率预测
# ══════════════════════════════════════════════════════════════════

def get_har_rv_forecast(symbol: str, klines: list | None = None) -> Dict:
    """HAR-RV波动率预测 — 替代Kronos torch依赖"""
    try:
        from har_rv_engine import get_har_rv_forecast as _f
        return _f(symbol, klines)
    except Exception as e:
        logger.debug(f'har_rv降级: {e}')
        return {'forecast': 0.0, 'rv_1d': 0.0, 'rv_5d': 0.0, 'source': 'fallback'}

def har_rv_score(symbol: str, signal_dir: str, klines: list | None = None) -> float:
    """HAR-RV评分调整量（-10~+10）"""
    try:
        from har_rv_engine import har_rv_score as _f
        return _f(symbol, signal_dir, klines)
    except Exception:
        return 0.0

# ══════════════════════════════════════════════════════════════════
# 2. Hurst指数体制验证
# ══════════════════════════════════════════════════════════════════

def get_hurst_index(symbol: str, klines: list | None = None) -> Dict:
    """Hurst指数 — >0.6趋势 / 0.4-0.6随机 / <0.4均值回归"""
    try:
        from hurst_engine import get_hurst_index as _f
        return _f(symbol, klines)
    except Exception as e:
        logger.debug(f'hurst降级: {e}')
        return {'hurst': 0.5, 'regime_confirm': False, 'source': 'fallback'}

def hurst_regime_check(symbol: str, regime: str, klines: list | None = None) -> Dict:
    """验证当前体制与Hurst指数是否一致"""
    try:
        from hurst_engine import hurst_regime_check as _f
        return _f(symbol, regime, klines)
    except Exception:
        return {'consistent': True, 'score_adj': 0, 'source': 'fallback'}

# ══════════════════════════════════════════════════════════════════
# 3. IC信息系数追踪
# ══════════════════════════════════════════════════════════════════

def update_ic(signal_id: str, predicted_score: float,
              actual_return: float, regime: str = '') -> None:
    """更新IC追踪记录"""
    try:
        from ic_tracker import update_ic as _f
        _f(signal_id, predicted_score, actual_return, regime)
    except Exception:
        pass

def get_ic_summary(regime: str = '', window: int = 50) -> Dict:
    """获取IC统计摘要"""
    try:
        from ic_tracker import get_ic_summary as _f
        return _f(regime, window)
    except Exception:
        return {'ic_mean': 0.0, 'ic_ir': 0.0, 'n': 0}

def get_ic_score_adj(regime: str, direction: str) -> float:
    """基于IC历史给出评分调整"""
    try:
        from ic_tracker import get_ic_score_adj as _f
        return _f(regime, direction)
    except Exception:
        return 0.0

# ══════════════════════════════════════════════════════════════════
# 4. 在线学习
# ══════════════════════════════════════════════════════════════════

def online_update(signal: Dict, outcome: float) -> None:
    """在线学习更新"""
    try:
        from online_learner_v2 import online_update as _f
        _f(signal, outcome)
    except Exception:
        pass

def get_adaptive_weight(regime: str, direction: str) -> float:
    """获取自适应权重"""
    try:
        from online_learner_v2 import get_adaptive_weight as _f
        return _f(regime, direction)
    except Exception:
        return 1.0

# ══════════════════════════════════════════════════════════════════
# 5. 统一批量接口
# ══════════════════════════════════════════════════════════════════

def get_ml_bundle(symbol: str, signal_dir: str, regime: str = '',
                  klines: list | None = None) -> Dict:
    """
    一次调用: HAR-RV预测 + Hurst验证 + IC评分调整 (并行)
    返回: {har_rv, hurst, ic_adj, score_adj_total}
    """
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        f1 = pool.submit(har_rv_score, symbol, signal_dir, klines)
        f2 = pool.submit(hurst_regime_check, symbol, regime, klines)
        f3 = pool.submit(get_ic_score_adj, regime, signal_dir)
        try: results['har_adj']  = f1.result(timeout=8)
        except: results['har_adj'] = 0.0
        try: results['hurst']    = f2.result(timeout=8)
        except: results['hurst'] = {'consistent': True, 'score_adj': 0}
        try: results['ic_adj']   = f3.result(timeout=5)
        except: results['ic_adj'] = 0.0

    hurst_adj = results['hurst'].get('score_adj', 0) if isinstance(results['hurst'], dict) else 0
    results['score_adj_total'] = results['har_adj'] + hurst_adj + results['ic_adj']
    return results
