"""
brahma_v6/reality/funding_model.py
资金费率模型
设计院 · 2026-07-09

Binance USDS-M 永续：每 8 小时结算一次。
多头付资金费 → funding_drag < 0
空头收资金费 → funding_drag > 0（降低成本）

估算方式：
  funding_drag = -direction_sign × notional × rate × periods
  direction_sign = +1 (LONG), -1 (SHORT)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    LONG  = "LONG"
    SHORT = "SHORT"


@dataclass
class FundingModel:
    """
    资金费率成本/收益估算。

    Args:
        default_rate:      默认年化 8H 费率（小数，如 0.0001 = 0.01%）
        settlement_hours:  结算周期（Binance 默认 8H）
    """
    default_rate:     float = 0.0001    # 0.01% per 8H（温和市场）
    settlement_hours: float = 8.0

    def estimate(
        self,
        notional: float,
        direction: Direction,
        holding_hours: float,
        funding_rate: float = None,
    ) -> float:
        """
        估算持仓期间累计资金费成本/收益（可正可负）。

        Args:
            notional:      名义价值（USD）
            direction:     LONG 或 SHORT
            holding_hours: 持仓小时数
            funding_rate:  实际费率（None = 使用 default_rate）

        Returns:
            funding_drag（负 = 净成本，正 = 净收益）
        """
        if notional < 0:
            raise ValueError(f"notional must be ≥ 0")
        if holding_hours < 0:
            raise ValueError(f"holding_hours must be ≥ 0")

        rate = funding_rate if funding_rate is not None else self.default_rate
        periods = holding_hours / self.settlement_hours
        gross_funding = notional * rate * periods

        # LONG 付出（负），SHORT 收入（正）
        sign = -1 if direction == Direction.LONG else 1
        return sign * gross_funding

    def annualized_rate(self, rate_8h: float) -> float:
        """8H 费率转年化。"""
        return rate_8h * (8760 / self.settlement_hours)
