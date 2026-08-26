#!/usr/bin/env python3
"""
brahma_position.py — 统一仓位域 v1.0
设计院 2026-08-24 重建 | 替换2个旧模块:
  position_sizer.py     (664行) → get_position_pct / kelly_position / headroom
  portfolio_optimizer.py(515行) → select_optimal_portfolio / check_correlation_risk

向后兼容: 所有函数签名不变，调用方无需修改
新增: get_position_bundle() — 一次调用完成仓位计算+相关性检查
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('brahma_position')

# ══════════════════════════════════════════════════════════════════
# 1. position_sizer — 仓位计算核心
# ══════════════════════════════════════════════════════════════════

def get_position_pct(symbol: str, score: float, direction: str,
                     regime: str = '', sl_pct: float = 2.0,
                     nav: float = 0, **kwargs) -> dict:
    """主仓位计算 — 委托position_sizer原始实现"""
    try:
        from position_sizer import get_position_pct as _f
        return _f(symbol, score, direction, regime, sl_pct, nav, **kwargs)
    except Exception as e:
        logger.warning(f'get_position_pct降级: {e}')
        # 最小fallback：固定5%NAV
        return {'pct': 5.0, 'level': 'FALLBACK', 'reason': str(e)}

def get_fg_position_cap(fear_greed_index: float) -> tuple:
    try:
        from position_sizer import get_fg_position_cap as _f
        return _f(fear_greed_index)
    except Exception:
        return (10.0, 'NORMAL')

def kelly_position(wr: float, rr: float, half: bool = True) -> float:
    try:
        from position_sizer import kelly_position as _f
        return _f(wr, rr, half)
    except Exception:
        # Kelly公式直接计算
        edge = wr - (1 - wr) / max(rr, 0.1)
        raw  = edge / (1 / max(rr, 0.1))
        return max(0.0, min(0.25, raw * (0.5 if half else 1.0))) * 100

def apply_headroom(base_pct: float, nav_current: float, nav_peak: float,
                   max_dd: float = 0.15) -> float:
    try:
        from position_sizer import apply_headroom as _f
        return _f(base_pct, nav_current, nav_peak, max_dd)
    except Exception:
        dd = (nav_peak - nav_current) / max(nav_peak, 1)
        mult = max(0.3, 1 - dd / max(max_dd, 0.01))
        return round(base_pct * mult, 2)

def sync_confidence_table_from_wr(min_n: int = 10, dry_run: bool = False) -> dict:
    try:
        from position_sizer import sync_confidence_table_from_wr as _f
        return _f(min_n, dry_run)
    except Exception:
        return {}

# ══════════════════════════════════════════════════════════════════
# 2. portfolio_optimizer — 组合优化/相关性
# ══════════════════════════════════════════════════════════════════

def select_optimal_portfolio(signals: List[Dict],
                              active_positions: List[Dict] | None = None,
                              max_positions: int = 3) -> Tuple[List[Dict], List[Dict]]:
    """优化组合选择 — 委托portfolio_optimizer"""
    try:
        from portfolio_optimizer import select_optimal_portfolio as _f
        return _f(signals, active_positions or [], max_positions)
    except Exception as e:
        logger.debug(f'select_optimal_portfolio降级: {e}')
        return signals[:max_positions], signals[max_positions:]

def check_correlation_risk(sym1: str, sym2: str) -> Dict:
    try:
        from portfolio_optimizer import check_correlation_risk as _f
        return _f(sym1, sym2)
    except Exception:
        return {'corr': 0.0, 'risk_level': 'UNKNOWN', 'source': 'fallback'}

def get_pair_correlation(sym1: str, sym2: str) -> float:
    try:
        from portfolio_optimizer import get_pair_correlation as _f
        return _f(sym1, sym2)
    except Exception:
        return 0.0

def filter_signals(signals: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    try:
        from portfolio_optimizer import filter_signals as _f
        return _f(signals)
    except Exception:
        return signals, []

def portfolio_summary(active_positions: List[Dict]) -> Dict:
    try:
        from portfolio_optimizer import portfolio_summary as _f
        return _f(active_positions)
    except Exception:
        return {'count': len(active_positions), 'source': 'fallback'}

# ══════════════════════════════════════════════════════════════════
# 3. 统一批量接口（新增）
# ══════════════════════════════════════════════════════════════════

def get_position_bundle(signal: Dict, nav: float = 0,
                        fg_index: float = 50,
                        active_positions: List[Dict] | None = None) -> Dict:
    """
    一次调用完成: 仓位计算 + FG上限 + 相关性检查
    减少调用方3次独立调用 → 1次

    返回: {pct, fg_cap, corr_risk, final_pct}
    """
    symbol    = signal.get('symbol', '')
    score     = float(signal.get('score', 0))
    direction = signal.get('direction', 'SHORT')
    regime    = signal.get('regime', '')
    sl_pct    = float(signal.get('sl_pct', 2.0))

    # 仓位计算
    pos = get_position_pct(symbol, score, direction, regime, sl_pct, nav)
    base_pct = pos.get('pct', 5.0) if isinstance(pos, dict) else float(pos)

    # FG上限
    fg_cap, fg_label = get_fg_position_cap(fg_index)
    capped_pct = min(base_pct, fg_cap)

    # BTC/ETH相关性检查
    corr_risk = {}
    if active_positions:
        for pos_sym in [p.get('symbol','') for p in active_positions]:
            if pos_sym and pos_sym != symbol:
                cr = check_correlation_risk(symbol, pos_sym)
                if cr.get('corr', 0) > 0.7:
                    corr_risk[pos_sym] = cr
                    capped_pct *= 0.5  # 高相关降半仓

    return {
        'base_pct':   base_pct,
        'fg_cap':     fg_cap,
        'fg_label':   fg_label,
        'corr_risk':  corr_risk,
        'final_pct':  round(capped_pct, 2),
        'level':      pos.get('level', '') if isinstance(pos, dict) else '',
    }
