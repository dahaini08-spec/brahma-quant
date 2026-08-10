"""
position_manager.py — 仓位倍数管理器
设计院 2026-08-10 恢复 | 原版在精简时误删

功能：根据近期胜率/连胜历史动态计算仓位倍数
  - 正常: 1.0x
  - 连胜≥3笔: 1.5x（中仓解锁）
  - 连胜≥5笔: 2.0x（重仓解锁）
  - 连败≥2笔: 0.7x（缩仓）
  - 连败≥3笔: 0.5x（轻仓）

苏摩111封印：MAX_MULT=2.0，MIN_MULT=0.5
"""

import json
from pathlib import Path
from typing import Callable

_DATA_ROOT = Path(__file__).parent.parent / 'data'
_SIGNAL_LOG = _DATA_ROOT / 'live_signal_log.jsonl'
_EXEC_LOG    = _DATA_ROOT / 'auto_executor_log.jsonl'

# 连胜/连败阈值
_WIN_THRESHOLDS  = [(5, 2.0), (3, 1.5)]   # (连胜数, 倍数)
_LOSS_THRESHOLDS = [(3, 0.5), (2, 0.7)]   # (连败数, 倍数)
_DEFAULT_MULT    = 1.0


def _load_recent_results(n: int = 10) -> list[str]:
    """
    读取最近n笔已结算信号的结果（win/loss/timeout）
    优先从 auto_executor_log 读取实际执行结果
    """
    results = []
    try:
        if _EXEC_LOG.exists():
            with open(_EXEC_LOG) as f:
                entries = [json.loads(l) for l in f if l.strip()]
            # 只看已结算的（有 outcome 字段）
            settled = [e for e in entries if e.get('outcome') in ('win', 'loss', 'timeout')]
            settled.sort(key=lambda x: x.get('ts', 0), reverse=True)
            results = [e['outcome'] for e in settled[:n]]
    except Exception:
        pass
    return results


def _calc_streak(results: list[str]) -> tuple[int, int]:
    """
    计算当前连胜/连败数
    returns: (win_streak, loss_streak)
    """
    if not results:
        return 0, 0

    win_streak = 0
    loss_streak = 0

    # 从最新往前数
    for r in results:
        if r == 'win':
            if loss_streak > 0:
                break
            win_streak += 1
        elif r in ('loss', 'timeout'):
            if win_streak > 0:
                break
            loss_streak += 1
        else:
            break

    return win_streak, loss_streak


def get_position_multiplier() -> float:
    """
    主接口：返回当前仓位倍数
    brahma_core / brahma_scoring 调用此函数

    Returns:
        float: 0.5 ~ 2.0
    """
    try:
        results = _load_recent_results(10)
        win_streak, loss_streak = _calc_streak(results)

        # 连胜解锁
        for threshold, mult in _WIN_THRESHOLDS:
            if win_streak >= threshold:
                return mult

        # 连败缩仓
        for threshold, mult in _LOSS_THRESHOLDS:
            if loss_streak >= threshold:
                return mult

        return _DEFAULT_MULT

    except Exception:
        return _DEFAULT_MULT


def get_multiplier_reason() -> str:
    """返回倍数原因描述（供日志使用）"""
    try:
        results = _load_recent_results(10)
        win_streak, loss_streak = _calc_streak(results)
        mult = get_position_multiplier()

        if win_streak >= 5:
            return f'连胜{win_streak}笔 → 重仓解锁×{mult}'
        elif win_streak >= 3:
            return f'连胜{win_streak}笔 → 中仓解锁×{mult}'
        elif loss_streak >= 3:
            return f'连败{loss_streak}笔 → 轻仓模式×{mult}'
        elif loss_streak >= 2:
            return f'连败{loss_streak}笔 → 缩仓模式×{mult}'
        else:
            return f'正常仓位×{mult}'
    except Exception:
        return '正常仓位×1.0'


if __name__ == '__main__':
    mult = get_position_multiplier()
    reason = get_multiplier_reason()
    print(f'当前仓位倍数: {mult}x | {reason}')
