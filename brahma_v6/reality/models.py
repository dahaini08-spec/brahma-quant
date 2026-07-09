"""
brahma_v6/reality/models.py
RealityCost — 统一成本结构
设计院 · 2026-07-09

所有成本 drag 均为负数（代表损耗）。
total_drag = sum(all drags)，直接加到 gross_pnl 得到 net_pnl。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RealityCost:
    """
    一笔订单的全成本拆解。

    约定：
      - fee_drag      ≤ 0   （手续费，始终为成本）
      - slippage_drag ≤ 0   （滑点，始终为成本）
      - funding_drag  任意   （资金费率，可正可负）
      - impact_drag   ≤ 0   （市场冲击，始终为成本）

    使用方式：
        net_pnl = gross_pnl + reality_cost.total_drag
    """
    fee_drag:      float
    slippage_drag: float
    funding_drag:  float
    impact_drag:   float

    @property
    def total_drag(self) -> float:
        """所有成本之和（负值越大 = 成本越高）。"""
        return self.fee_drag + self.slippage_drag + self.funding_drag + self.impact_drag

    @property
    def total_cost(self) -> float:
        """成本绝对值（正数表示总损耗）。"""
        return abs(self.total_drag)

    def validate(self) -> None:
        """
        校验成本方向约束。
        fee / slippage / impact 必须 ≤ 0。
        funding 不限制方向（空头收、多头付）。
        """
        if self.fee_drag > 0:
            raise ValueError(f"fee_drag must be ≤ 0, got {self.fee_drag}")
        if self.slippage_drag > 0:
            raise ValueError(f"slippage_drag must be ≤ 0, got {self.slippage_drag}")
        if self.impact_drag > 0:
            raise ValueError(f"impact_drag must be ≤ 0, got {self.impact_drag}")

    def to_dict(self) -> dict:
        return {
            "fee_drag":      self.fee_drag,
            "slippage_drag": self.slippage_drag,
            "funding_drag":  self.funding_drag,
            "impact_drag":   self.impact_drag,
            "total_drag":    self.total_drag,
        }
