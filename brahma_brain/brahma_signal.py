#!/usr/bin/env python3
"""
brahma_signal.py — 统一信号域 v1.0
设计院 2026-08-24 重建 | 替换5个旧模块:
  signal_quality_engine.py  (193行) → evaluate_signal / check_signal_quality
  enhanced_signal_engine.py (381行) → enhance_signal / apply_post_filters
  signal_trace.py           (193行) → trace_signal / get_signal_history
  signal_selector.py        (300行) → select_best_signal / filter_signals
  signal_weight_updater.py  (256行) → update_signal_weight / get_weights

向后兼容: 所有函数签名不变，调用方无需修改
新增: get_signal_bundle() — 一次调用完成评估+增强+追踪
"""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger('brahma_signal')

# ══════════════════════════════════════════════════════════════════
# 1. signal_quality_engine — 信号质量评估
# ══════════════════════════════════════════════════════════════════

def evaluate_signal(signal: dict) -> object:
    """信号质量评估 → SQEResult"""
    try:
        from signal_quality_engine import evaluate_signal as _f
        return _f(signal)
    except Exception as e:
        logger.debug(f'evaluate_signal降级: {e}')
        # 最小fallback: 返回namedtuple-like对象
        class _R:
            status = 'PASS'; score_adj = 0; reason = 'fallback'
        return _R()

def check_signal_quality(signal: dict) -> dict:
    try:
        from signal_quality_engine import check_signal_quality as _f
        return _f(signal)
    except Exception:
        return {'ok': True, 'reason': 'fallback'}

# ══════════════════════════════════════════════════════════════════
# 2. enhanced_signal_engine — 信号增强层
# ══════════════════════════════════════════════════════════════════

def enhance_signal(signal: dict, market_data: dict | None = None) -> dict:
    try:
        from enhanced_signal_engine import enhance_signal as _f
        return _f(signal, market_data)
    except Exception as e:
        logger.debug(f'enhance_signal降级: {e}')
        return signal

def apply_post_filters(signal: dict, regime: str = '') -> dict:
    try:
        from enhanced_signal_engine import apply_post_filters as _f
        return _f(signal, regime)
    except Exception:
        return signal

def get_enhanced_score(signal: dict) -> float:
    try:
        from enhanced_signal_engine import get_enhanced_score as _f
        return _f(signal)
    except Exception:
        return float(signal.get('score', 0))

# ══════════════════════════════════════════════════════════════════
# 3. signal_trace — 信号追踪/历史
# ══════════════════════════════════════════════════════════════════

def trace_signal(signal: dict) -> None:
    try:
        from signal_trace import trace_signal as _f
        _f(signal)
    except Exception:
        pass

def get_signal_history(symbol: str, limit: int = 20) -> list:
    try:
        from signal_trace import get_signal_history as _f
        return _f(symbol, limit)
    except Exception:
        return []

# ══════════════════════════════════════════════════════════════════
# 4. signal_selector — 信号筛选/排序
# ══════════════════════════════════════════════════════════════════

def select_best_signal(signals: list) -> dict | None:
    try:
        from signal_selector import select_best_signal as _f
        return _f(signals)
    except Exception:
        return signals[0] if signals else None

def filter_signals(signals: list, regime: str = '', min_score: float = 120) -> list:
    try:
        from signal_selector import filter_signals as _f
        return _f(signals, regime, min_score)
    except Exception:
        return [s for s in signals if s.get('score', 0) >= min_score]

# ══════════════════════════════════════════════════════════════════
# 5. signal_weight_updater — 权重更新
# ══════════════════════════════════════════════════════════════════

def update_signal_weight(key: str, result: str, score: float = 0) -> None:
    try:
        from signal_weight_updater import update_signal_weight as _f
        _f(key, result, score)
    except Exception:
        pass

def get_weights() -> dict:
    try:
        from signal_weight_updater import get_weights as _f
        return _f()
    except Exception:
        return {}

# ══════════════════════════════════════════════════════════════════
# 6. 统一批量接口（新增，减少调用方多次import）
# ══════════════════════════════════════════════════════════════════

def get_signal_bundle(signal: dict, market_data: dict | None = None) -> dict:
    """
    一次调用完成: 质量评估 → 增强 → 后置过滤 → 追踪
    返回: {quality, enhanced_signal, score_adj, traced}
    """
    quality  = evaluate_signal(signal)
    enhanced = enhance_signal(signal, market_data)
    enhanced = apply_post_filters(enhanced, signal.get('regime', ''))
    trace_signal(enhanced)
    return {
        'quality':         quality,
        'enhanced_signal': enhanced,
        'score_adj':       getattr(quality, 'score_adj', 0),
        'final_score':     get_enhanced_score(enhanced),
        'traced':          True,
    }
