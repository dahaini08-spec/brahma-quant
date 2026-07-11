"""
metrics.py — 标准化回测指标计算
封印: Brahma 2.0 P0-Plus 2026-07-11
"""

from __future__ import annotations

import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤（负数，如 -0.15 表示 -15%）"""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def profit_factor(returns: pd.Series) -> float:
    """盈亏比 = 总盈利 / abs(总亏损)"""
    gains = returns[returns > 0].sum()
    losses = returns[returns < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / abs(losses))


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 252) -> float:
    """年化 Sharpe（假设无风险利率=0）"""
    if len(returns) < 2:
        return 0.0
    mean = returns.mean()
    std = returns.std()
    if std == 0:
        return 0.0
    return float(mean / std * (periods_per_year ** 0.5))


def calc_metrics(returns: pd.Series) -> dict:
    """
    标准指标字典，固定字段集，保证 SimFactory 报告可审计。

    字段:
        trades          int    交易笔数
        total_return    float  累计净收益率
        win_rate        float  胜率 [0,1]
        profit_factor   float  盈亏比
        avg_return      float  每笔平均净收益
        max_drawdown    float  最大回撤（≤0）
        sharpe          float  年化 Sharpe
    """
    returns = pd.Series(returns).dropna()

    if returns.empty:
        return {
            "trades": 0,
            "total_return": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
        }

    equity = (1 + returns).cumprod()

    return {
        "trades": int(len(returns)),
        "total_return": float(equity.iloc[-1] - 1),
        "win_rate": float((returns > 0).mean()),
        "profit_factor": profit_factor(returns),
        "avg_return": float(returns.mean()),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe_ratio(returns),
    }
