"""
brahma_v6/reality/fee_model.py
手续费模型 — Maker/Taker 分层
设计院 · 2026-07-09

Binance USDS-M Futures 标准费率：
  Taker: 0.0500%  (市价单 / 止损单)
  Maker: 0.0200%  (限价挂单成交)
VIP 等级可覆盖，通过 FeeModel(taker_rate, maker_rate) 注入。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderSide(str, Enum):
    TAKER = "TAKER"
    MAKER = "MAKER"


@dataclass
class FeeModel:
    """
    基于成交量的手续费模型。

    Args:
        taker_rate: Taker 费率（小数，如 0.0005 = 0.05%）
        maker_rate: Maker 费率（小数，如 0.0002 = 0.02%）
    """
    taker_rate: float = 0.0005   # Binance USDS-M 标准 Taker
    maker_rate: float = 0.0002   # Binance USDS-M 标准 Maker

    def estimate(
        self,
        notional: float,
        side: OrderSide = OrderSide.TAKER,
    ) -> float:
        """
        估算手续费成本（返回负数）。

        Args:
            notional: 名义价值（entry_price × quantity）
            side: TAKER 或 MAKER

        Returns:
            fee_drag ≤ 0
        """
        if notional < 0:
            raise ValueError(f"notional must be ≥ 0, got {notional}")
        rate = self.taker_rate if side == OrderSide.TAKER else self.maker_rate
        # 开仓 + 平仓各收一次
        return -(notional * rate * 2)

    def estimate_one_way(
        self,
        notional: float,
        side: OrderSide = OrderSide.TAKER,
    ) -> float:
        """仅估算单边（开 or 平）手续费。"""
        if notional < 0:
            raise ValueError(f"notional must be ≥ 0, got {notional}")
        rate = self.taker_rate if side == OrderSide.TAKER else self.maker_rate
        return -(notional * rate)
