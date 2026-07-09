"""
brahma_v6/reality/slippage_model.py
滑点模型 — 基于价差和成交量比率
设计院 · 2026-07-09

两层滑点：
  1. 半价差（bid-ask spread 的一半）
  2. 市场冲击（订单量 / 流动性深度）

默认保守参数适用于 Binance 主流永续合约。
小市值标的应注入更高的 spread_bps 和 impact_bps。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SlippageModel:
    """
    滑点估算模型。

    Args:
        spread_bps:    半价差（bps = 万分之一）
        impact_bps_per_m:  每百万 USD 成交量的冲击（bps）
        max_slippage_bps:  滑点上限（防极端情况）
    """
    spread_bps:         float = 2.0    # BTC/ETH 约 1-2 bps
    impact_bps_per_m:   float = 1.0    # 每 $1M 冲击约 1 bps
    max_slippage_bps:   float = 50.0   # 滑点上限 0.50%

    def estimate(
        self,
        notional: float,
        avg_daily_volume_usd: float = 500_000_000.0,
    ) -> float:
        """
        估算总滑点成本（返回负数）。

        Args:
            notional: 订单名义价值（USD）
            avg_daily_volume_usd: 标的日均成交量（USD），用于计算冲击

        Returns:
            slippage_drag ≤ 0
        """
        if notional < 0:
            raise ValueError(f"notional must be ≥ 0, got {notional}")
        if avg_daily_volume_usd <= 0:
            raise ValueError(f"avg_daily_volume_usd must be > 0")

        # 半价差（开仓 + 平仓）
        spread_cost_bps = self.spread_bps * 2

        # 市场冲击（订单量占日成交量比例）
        volume_ratio = notional / avg_daily_volume_usd
        impact_cost_bps = self.impact_bps_per_m * volume_ratio * 100   # 每 1% 步骤

        total_bps = min(spread_cost_bps + impact_cost_bps, self.max_slippage_bps)
        return -(notional * total_bps / 10_000)

    def estimate_by_bps(self, notional: float, total_bps: float) -> float:
        """直接按 bps 估算（回测中已知真实滑点时使用）。"""
        if notional < 0:
            raise ValueError(f"notional must be ≥ 0")
        if total_bps < 0:
            raise ValueError(f"total_bps must be ≥ 0")
        return -(notional * total_bps / 10_000)
