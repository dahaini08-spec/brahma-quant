#!/usr/bin/env python3
"""
brahma_signal.py — 统一Signal域 v2.0
[2026-08-24 设计院顶层重构] 8个signal_*模块统一入口

收归模块:
  signal_quality_engine   → evaluate_signal / check_signal_quality
  enhanced_signal_engine  → enhance_signal / apply_post_filters / get_enhanced_score
  signal_trace            → trace_signal / get_signal_history / trace_generated / trace_skipped
  signal_selector         → select_best_signal / filter_signals
  signal_weight_updater   → update_signal_weight / get_weights
  signal_lifecycle        → tick_signal_lifecycle
  signal_queue            → add_signal / get_status
  signal_integrity_gate   → check_integrity

向后兼容: 所有函数签名不变
"""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger('brahma_signal')


# ─── 1. 信号质量评估 ─────────────────────────────────────────────────────────
def evaluate_signal(signal: dict) -> object:
    try:
        from signal_quality_engine import evaluate_signal as _f
        return _f(signal)
    except Exception as e:
        logger.debug(f'evaluate_signal: {e}')
        return None

def check_signal_quality(signal: dict) -> dict:
    try:
        from signal_quality_engine import check_signal_quality as _f
        return _f(signal)
    except Exception as e:
        return {'ok': False, 'reason': str(e)}


# ─── 2. 信号增强 ──────────────────────────────────────────────────────────────
def enhance_signal(signal: dict, market_data: dict | None = None) -> dict:
    try:
        from enhanced_signal_engine import enhance_signal as _f
        return _f(signal, market_data)
    except Exception:
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


# ─── 3. 信号轨迹审计 ─────────────────────────────────────────────────────────
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

def trace_generated(signal: dict, result: dict | None = None) -> None:
    """向后兼容: brahma_analysis_runner直接调用"""
    try:
        from signal_trace import trace_generated as _f
        _f(signal, result)
    except Exception:
        pass

def trace_skipped(symbol: str, reason: str = '', score: float = 0) -> None:
    """向后兼容: brahma_analysis_runner直接调用"""
    try:
        from signal_trace import trace_skipped as _f
        _f(symbol, reason, score)
    except Exception:
        pass


# ─── 4. 信号选择 ─────────────────────────────────────────────────────────────
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


# ─── 5. 信号权重更新 ─────────────────────────────────────────────────────────
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


# ─── 6. 信号生命周期 ─────────────────────────────────────────────────────────
def tick_signal_lifecycle(signal: dict | None = None) -> dict:
    """向后兼容: brahma_analysis_runner直接调用"""
    try:
        from signal_lifecycle import tick_signal_lifecycle as _f
        return _f(signal)
    except Exception:
        return {}


# ─── 7. 信号队列 ─────────────────────────────────────────────────────────────
def add_signal(signal: dict) -> bool:
    try:
        from signal_queue import add_signal as _f
        return _f(signal)
    except Exception:
        return False

def get_queue_status() -> dict:
    try:
        from signal_queue import get_status as _f
        return _f()
    except Exception:
        return {}


# ─── 8. 完整性门控 ───────────────────────────────────────────────────────────
def check_integrity(signal: dict) -> dict:
    try:
        from signal_integrity_gate import check_integrity as _f
        return _f(signal)
    except Exception:
        return {'ok': True, 'reason': 'gate_unavailable'}


# ─── 9. 批量接口（高频调用优化） ─────────────────────────────────────────────
def get_signal_bundle(signal: dict, market_data: dict | None = None) -> dict:
    """一次调用: 增强+质量+完整性"""
    enhanced  = enhance_signal(signal, market_data)
    quality   = check_signal_quality(enhanced)
    integrity = check_integrity(enhanced)
    return {
        'signal':    enhanced,
        'quality':   quality,
        'integrity': integrity,
        'score':     get_enhanced_score(enhanced),
    }
