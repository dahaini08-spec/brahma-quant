"""
brahma_v6/execution/algo_selector.py
执行算法选择器 v1.0

设计院 2026-07-10 自主补全（v6.5断点修复）

职责：
  - 根据信号类型、市场流动性、紧急程度，选择最优执行算法
  - LIMIT (maker) → 省手续费，适合普通信号
  - MARKET       → 紧急止损/追价，优先成交
  - TWAP         → 大仓位分批（暂未实现，预留接口）
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketContext:
    """下单时的市场快照"""
    symbol: str
    mid_price: float
    bid: float = 0.0
    ask: float = 0.0
    spread_bps: float = 0.0          # 买卖价差（基点）
    volume_1h_usd: float = 0.0       # 1H成交额（USD）
    depth_1pct_usd: float = 0.0      # 1%深度（USD）
    is_market_hours: bool = True      # 是否流动性高峰时段

    def __post_init__(self):
        if self.spread_bps == 0 and self.bid > 0 and self.ask > 0:
            self.spread_bps = (self.ask - self.bid) / self.mid_price * 10000


@dataclass
class AlgoDecision:
    """算法选择结果"""
    order_type: str          # "LIMIT" | "MARKET"
    time_in_force: str       # "GTC" | "IOC" | "FOK"
    limit_offset_bps: float  # LIMIT单偏移量（负=被动挂单，正=主动穿价）
    reason: str
    urgency: str             # "normal" | "urgent" | "stop_loss"


# ─── 阈值常量 ────────────────────────────────────────────────────────────────

SPREAD_MARKET_BPS      = 8.0    # 价差>8bps → 用MARKET（流动性差）
SPREAD_LIMIT_BPS       = 3.0    # 价差<3bps → 挂LIMIT maker（省fee）
DEFAULT_LIMIT_OFFSET   = -1.0   # 默认LIMIT单：中间价偏移-1bps（被动等待）
STOP_LOSS_MARKET       = True   # 止损单强制使用MARKET
URGENT_SPREAD_LIMIT    = 5.0    # 紧急单仍用LIMIT但穿价5bps


class AlgoSelector:
    """
    执行算法选择器

    逻辑优先级：
      1. 止损单 → MARKET（不允许任何滑点失误）
      2. 流动性极差（spread>8bps）→ MARKET
      3. 正常信号（spread<3bps）→ LIMIT maker被动
      4. 中等流动性（3-8bps）→ LIMIT 轻微穿价
    """

    def __init__(self,
                 spread_market_bps: float = SPREAD_MARKET_BPS,
                 spread_limit_bps: float = SPREAD_LIMIT_BPS):
        self.spread_market_bps = spread_market_bps
        self.spread_limit_bps = spread_limit_bps

    def select(self,
               intent_type: str,           # "OPEN" | "CLOSE" | "STOP_LOSS" | "TAKE_PROFIT"
               ctx: Optional[MarketContext] = None,
               urgency: str = "normal",    # "normal" | "urgent"
               ) -> AlgoDecision:
        """
        选择执行算法

        Args:
            intent_type: 订单意图类型
            ctx:         市场快照（None时使用默认LIMIT）
            urgency:     紧急程度

        Returns:
            AlgoDecision
        """
        is_stop = intent_type.upper() in ('STOP_LOSS', 'STOP', 'SL')
        is_close = intent_type.upper() in ('CLOSE', 'TAKE_PROFIT', 'TP')
        spread = ctx.spread_bps if ctx else 0.0

        # ── 1. 止损单：无条件MARKET ─────────────────────────────────
        if is_stop and STOP_LOSS_MARKET:
            return AlgoDecision(
                order_type='MARKET',
                time_in_force='GTC',
                limit_offset_bps=0.0,
                reason='止损单强制MARKET，不允许挂单失误',
                urgency='stop_loss',
            )

        # ── 2. 流动性极差：MARKET ────────────────────────────────────
        if ctx and spread > self.spread_market_bps:
            return AlgoDecision(
                order_type='MARKET',
                time_in_force='GTC',
                limit_offset_bps=0.0,
                reason=f'价差{spread:.1f}bps>{self.spread_market_bps}bps，流动性差→MARKET',
                urgency=urgency,
            )

        # ── 3. 紧急单：LIMIT穿价 ────────────────────────────────────
        if urgency == 'urgent':
            return AlgoDecision(
                order_type='LIMIT',
                time_in_force='IOC',
                limit_offset_bps=URGENT_SPREAD_LIMIT,
                reason=f'紧急单LIMIT IOC穿价{URGENT_SPREAD_LIMIT}bps',
                urgency='urgent',
            )

        # ── 4. 正常信号：LIMIT被动maker ─────────────────────────────
        if ctx and spread <= self.spread_limit_bps:
            return AlgoDecision(
                order_type='LIMIT',
                time_in_force='GTC',
                limit_offset_bps=DEFAULT_LIMIT_OFFSET,
                reason=f'价差{spread:.1f}bps≤{self.spread_limit_bps}bps，LIMIT maker省fee',
                urgency='normal',
            )

        # ── 5. 中等流动性：LIMIT轻微穿价 ────────────────────────────
        offset = min(spread / 2, 3.0)   # 穿价不超过3bps
        return AlgoDecision(
            order_type='LIMIT',
            time_in_force='GTC',
            limit_offset_bps=offset,
            reason=f'价差{spread:.1f}bps，LIMIT穿价{offset:.1f}bps',
            urgency='normal',
        )

    def calc_limit_price(self, mid_price: float, side: str,
                         decision: AlgoDecision) -> float:
        """
        根据算法决策计算LIMIT价格

        Args:
            mid_price: 中间价
            side:      'BUY' | 'SELL'
            decision:  AlgoDecision

        Returns:
            limit_price
        """
        if decision.order_type == 'MARKET':
            return mid_price  # MARKET单价格无意义，返回参考价

        offset_pct = decision.limit_offset_bps / 10000
        if side.upper() == 'BUY':
            return mid_price * (1 + offset_pct)
        else:
            return mid_price * (1 - offset_pct)


# ─── 自测 ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    selector = AlgoSelector()

    cases = [
        ('STOP_LOSS', None, 'normal'),
        ('OPEN', MarketContext('BTCUSDT', 64000, spread_bps=2.0), 'normal'),
        ('OPEN', MarketContext('BTCUSDT', 64000, spread_bps=5.0), 'normal'),
        ('CLOSE', MarketContext('HYPEUSDT', 10.0, spread_bps=12.0), 'normal'),
        ('OPEN', MarketContext('ETHUSDT', 1780, spread_bps=1.5), 'urgent'),
    ]

    for intent, ctx, urgency in cases:
        d = selector.select(intent, ctx, urgency)
        spread_str = f'spread={ctx.spread_bps:.0f}bps' if ctx else 'no_ctx'
        print(f'{intent:12} {spread_str:15} urgency={urgency:6} → {d.order_type:6} {d.time_in_force:3} [{d.reason[:50]}]')
