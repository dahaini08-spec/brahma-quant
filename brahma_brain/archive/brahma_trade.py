# brahma_trade.py · 梵天出场参数辅助函数
# 从 brahma_core.py 拆分 · 2026-07-12 设计院6方联合封印
# 职责: _nearest_swing_above / _nearest_swing_below / calc_trade_params / rebase_params
# 依赖: brahma_core_entry (实际逻辑已在2026-07-01迁入entry)

import os, sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

try:
    from brahma_brain.brahma_core_entry import (
        calc_trade_params as _ctp_entry,
        rebase_params as _rbp_entry,
    )
    _ENTRY_OK = True
except Exception:
    _ENTRY_OK = False

def _nearest_swing_above(swing_highs: list, entry: float) -> float:
    """找到入场价上方最近的摆动高点（用于做空止损）"""
    candidates = [v for v in swing_highs if v > entry]
    return min(candidates) if candidates else entry * 1.015

def _nearest_swing_below(swing_lows: list, entry: float) -> float:
    """找到入场价下方最近的摆动低点（用于做多止损）"""
    candidates = [v for v in swing_lows if v < entry]
    return max(candidates) if candidates else entry * 0.985

def calc_trade_params(ms: dict, smc: dict, signal_dir: str,
                      mtf_result: dict = None) -> dict:
    """[已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名"""
    if _ENTRY_OK:
        return _ctp_entry(ms, smc, signal_dir, mtf_result)
    raise ImportError('brahma_core_entry not available')


def rebase_params(params: dict, current_price: float,
                  symbol: str = '') -> dict:
    """[已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名"""
    if _ENTRY_OK:
        return _rbp_entry(params, current_price, symbol)
    raise ImportError('brahma_core_entry not available')


# ═══════════════════════════════════════════════════════════════
# 主分析入口
# ═══════════════════════════════════════════════════════════════

