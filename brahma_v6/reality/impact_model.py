"""
brahma_v6/reality/impact_model.py
市场冲击模型 — 基于流动性比率
设计院 · 2026-07-09

市场冲击 = 订单规模 / 流动性深度 的函数。
冲击随名义价值增大而增大（非线性）。

公式（简化 Almgren-Chriss）：
  impact_bps = k * sqrt(notional / adv)
  其中 adv = 日均成交量 USD
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class ImpactModel:
    """
    市场冲击估算模型。

    Args:
        impact_factor:  冲击系数 k（默认 10 bps 每 sqrt(1) 流动性比率）
        max_impact_bps: 冲击上限（bps）
    """
    impact_factor:  float = 10.0    # 保守值，适用于主流币
    max_impact_bps: float = 100.0   # 最大 1% 冲击

    def estimate(
        self,
        notional: float,
        avg_daily_volume_usd: float = 500_000_000.0,
    ) -> float:
        """
        估算市场冲击成本（返回负数）。

        Args:
            notional:            订单名义价值（USD）
            avg_daily_volume_usd: 标的日均成交量（USD）

        Returns:
            impact_drag ≤ 0
        """
        if notional < 0:
            raise ValueError(f"notional must be ≥ 0, got {notional}")
        if avg_daily_volume_usd <= 0:
            raise ValueError(f"avg_daily_volume_usd must be > 0")
        if notional == 0:
            return 0.0

        liquidity_ratio = notional / avg_daily_volume_usd
        impact_bps = self.impact_factor * math.sqrt(liquidity_ratio)
        impact_bps = min(impact_bps, self.max_impact_bps)

        # 开仓 + 平仓各有冲击
        total_impact_bps = impact_bps * 2
        return -(notional * total_impact_bps / 10_000)

    def estimate_single_leg(
        self,
        notional: float,
        avg_daily_volume_usd: float = 500_000_000.0,
    ) -> float:
        """单边冲击（仅开仓 or 仅平仓）。"""
        return self.estimate(notional, avg_daily_volume_usd) / 2
