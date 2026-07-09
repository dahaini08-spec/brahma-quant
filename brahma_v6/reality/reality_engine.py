"""
brahma_v6/reality/reality_engine.py
RealityEngine — 统一成本估算入口
设计院 · 2026-07-09

Runtime / Backtest / Paper 统一调用：
    reality_cost = engine.estimate(order, fill, market_context)

然后构造 PnLAttribution：
    PnLAttribution(
        gross_pnl     = gross_pnl,
        fee_drag      = reality_cost.fee_drag,
        slippage_drag = reality_cost.slippage_drag,
        funding_drag  = reality_cost.funding_drag,
        impact_drag   = reality_cost.impact_drag,
        net_pnl       = gross_pnl + reality_cost.total_drag,
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from brahma_v6.reality.models import RealityCost
from brahma_v6.reality.fee_model import FeeModel, OrderSide
from brahma_v6.reality.slippage_model import SlippageModel
from brahma_v6.reality.funding_model import FundingModel, Direction
from brahma_v6.reality.impact_model import ImpactModel
from brahma_v6.dharma2.models import PnLAttribution


@dataclass(frozen=True)
class MarketContext:
    """
    估算成本所需的市场快照。

    Args:
        avg_daily_volume_usd: 日均成交量（USD），用于冲击和滑点
        funding_rate:         最近一期资金费率（None = 使用默认值）
        order_side:           TAKER / MAKER
        direction:            LONG / SHORT（用于资金费率方向）
        holding_hours:        预期持仓小时数
    """
    avg_daily_volume_usd: float        = 500_000_000.0
    funding_rate:         Optional[float] = None
    order_side:           OrderSide    = OrderSide.TAKER
    direction:            Direction    = Direction.LONG
    holding_hours:        float        = 8.0


@dataclass
class RealityEngine:
    """
    统一成本估算引擎。

    组合四个子模型，所有调用路径均通过此入口。
    子模型可独立替换（注入）以适配不同账户/交易所/VIP等级。
    """
    fee_model:      FeeModel      = field(default_factory=FeeModel)
    slippage_model: SlippageModel = field(default_factory=SlippageModel)
    funding_model:  FundingModel  = field(default_factory=FundingModel)
    impact_model:   ImpactModel   = field(default_factory=ImpactModel)

    def estimate(
        self,
        notional: float,
        context: MarketContext = None,
    ) -> RealityCost:
        """
        估算一笔交易的全部成本。

        Args:
            notional: 名义价值（entry_price × quantity，USD）
            context:  市场快照；None = 使用全部默认值

        Returns:
            RealityCost（可直接接入 PnLAttribution）
        """
        if notional < 0:
            raise ValueError(f"notional must be ≥ 0, got {notional}")

        ctx = context or MarketContext()

        fee_drag = self.fee_model.estimate(
            notional=notional,
            side=ctx.order_side,
        )
        slippage_drag = self.slippage_model.estimate(
            notional=notional,
            avg_daily_volume_usd=ctx.avg_daily_volume_usd,
        )
        funding_drag = self.funding_model.estimate(
            notional=notional,
            direction=ctx.direction,
            holding_hours=ctx.holding_hours,
            funding_rate=ctx.funding_rate,
        )
        impact_drag = self.impact_model.estimate(
            notional=notional,
            avg_daily_volume_usd=ctx.avg_daily_volume_usd,
        )

        cost = RealityCost(
            fee_drag      = fee_drag,
            slippage_drag = slippage_drag,
            funding_drag  = funding_drag,
            impact_drag   = impact_drag,
        )
        cost.validate()
        return cost

    def build_attribution(
        self,
        gross_pnl: float,
        notional: float,
        context: MarketContext = None,
    ) -> PnLAttribution:
        """
        直接构造平衡的 PnLAttribution（跳过手动计算）。

        Args:
            gross_pnl: 毛利润（exit_price - entry_price）× quantity
            notional:  名义价值
            context:   市场快照

        Returns:
            PnLAttribution（已通过 validate()）
        """
        cost = self.estimate(notional=notional, context=context)
        net_pnl = gross_pnl + cost.total_drag
        attr = PnLAttribution(
            gross_pnl     = gross_pnl,
            fee_drag      = cost.fee_drag,
            slippage_drag = cost.slippage_drag,
            funding_drag  = cost.funding_drag,
            impact_drag   = cost.impact_drag,
            net_pnl       = net_pnl,
        )
        attr.validate()   # 双重确认
        return attr
