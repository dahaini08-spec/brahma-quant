"""
brahma_v6/schemas/events.py — 统一事件Schema封印
设计院 × 量化工程师 × 顶级评估v6.0建议 2026-07-08

所有事件必须经此Schema，无例外。
trace_id贯穿全链：signal → risk → order → fill → pnl
"""
from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from enum import Enum


# ══════════════════════════════════════════════════════
#  事件类型枚举
# ══════════════════════════════════════════════════════
class EventSubject(str, Enum):
    # 行情层
    MARKET_KLINE        = "market.kline"
    MARKET_TRADE        = "market.trade"
    MARKET_ORDERBOOK    = "market.orderbook.l2"
    MARKET_FUNDING      = "market.funding"
    MARKET_OI           = "market.open_interest"
    MARKET_LIQUIDATION  = "market.liquidation"

    # 特征层
    FEATURE_MICRO       = "feature.microstructure"
    FEATURE_DERIVATIVES = "feature.derivatives"

    # 信号层
    SIGNAL_RAW          = "signal.raw"
    SIGNAL_SCORED       = "signal.scored"
    SIGNAL_EXPIRED      = "signal.expired"
    SIGNAL_CANCELLED    = "signal.cancelled"

    # 风控层
    RISK_DECISION       = "risk.decision"
    RISK_BLOCKED        = "risk.blocked"

    # 订单层
    ORDER_INTENT        = "order.intent"
    ORDER_SUBMITTED     = "order.submitted"
    ORDER_ACCEPTED      = "order.accepted"
    ORDER_REJECTED      = "order.rejected"
    ORDER_PARTIAL_FILL  = "order.partial_fill"
    ORDER_FILLED        = "order.filled"
    ORDER_CANCELLED     = "order.cancelled"
    ORDER_EXPIRED       = "order.expired"
    ORDER_UNKNOWN       = "order.unknown"

    # 持仓/PnL层
    POSITION_OPEN       = "position.open"
    POSITION_CLOSE      = "position.close"
    POSITION_UPDATE     = "position.update"
    PNL_TRADE           = "pnl.trade"
    ATTRIBUTION_TRADE   = "attribution.trade"

    # 体制层
    REGIME_CHANGE       = "regime.change"

    # 系统层
    SYSTEM_HEALTH       = "system.health"
    SYSTEM_ALERT        = "system.alert"


# ══════════════════════════════════════════════════════
#  基础事件包（所有事件的父类）
# ══════════════════════════════════════════════════════
@dataclass
class BrahmaEvent:
    subject: str = ""                     # EventSubject值
    symbol: str = ""                      # 标的
    exchange: str = "BINANCE_FUTURES"
    ts_event: float = field(default_factory=time.time)
    ts_ingest: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_event_id: Optional[str] = None
    source: str = "brahma"
    schema_version: str = "1.0"
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)

    def child(self, subject: str, source: str = "brahma", **payload_kw) -> "BrahmaEvent":
        """创建子事件，继承trace_id，parent_event_id=当前event_id"""
        return BrahmaEvent(
            subject=subject,
            symbol=self.symbol,
            exchange=self.exchange,
            trace_id=self.trace_id,
            parent_event_id=self.event_id,
            source=source,
            payload=payload_kw,
        )


# ══════════════════════════════════════════════════════
#  信号事件
# ══════════════════════════════════════════════════════
@dataclass
class SignalScoredEvent(BrahmaEvent):
    subject: str = EventSubject.SIGNAL_SCORED

    # 直接展开核心字段（方便快速访问，payload保留完整）
    direction: str = ""
    raw_score: float = 0.0
    regime_bonus: float = 0.0
    event_bonus: float = 0.0
    noise_penalty: float = 0.0
    final_score: float = 0.0
    regime: str = ""
    grade: str = ""
    valid_before_adj: bool = False
    valid_after_adj: bool = False
    blocked: bool = True
    block_reason: str = ""
    adjustment_trace: List[str] = field(default_factory=list)
    confidence: float = 0.0

    # 特征归因（顶级评估v6.0要求）
    top_positive_features: List[List] = field(default_factory=list)
    top_negative_features: List[List] = field(default_factory=list)


# ══════════════════════════════════════════════════════
#  风控决策事件
# ══════════════════════════════════════════════════════
@dataclass
class RiskDecisionEvent(BrahmaEvent):
    subject: str = EventSubject.RISK_DECISION

    decision: str = "BLOCKED"            # APPROVE / REDUCE / BLOCKED
    final_size_nav: float = 0.0
    max_leverage: int = 1
    order_style: str = "LIMIT"           # POST_ONLY_LIMIT / LIMIT / MARKET
    blocked_layers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    reason: str = ""
    signal_score: float = 0.0
    regime: str = ""


# ══════════════════════════════════════════════════════
#  订单意图（与真实Order分离）
# ══════════════════════════════════════════════════════
@dataclass
class OrderIntentEvent(BrahmaEvent):
    subject: str = EventSubject.ORDER_INTENT

    direction: str = ""                  # LONG / SHORT
    size_nav: float = 0.0               # NAV占比
    quantity: float = 0.0               # 合约数量
    entry_price: Optional[float] = None  # 期望入场价（None=市价）
    stop_loss: float = 0.0
    take_profit: Optional[float] = None
    leverage: int = 3
    order_style: str = "POST_ONLY_LIMIT"
    reduce_only: bool = False
    time_in_force: str = "GTC"
    max_slippage_pct: float = 0.3
    algo: str = "CHASE_LIMIT"           # CHASE_LIMIT / TWAP / MARKET

    # 来源追踪
    signal_trace_id: str = ""
    risk_trace_id: str = ""


# ══════════════════════════════════════════════════════
#  订单状态事件
# ══════════════════════════════════════════════════════
@dataclass
class OrderFilledEvent(BrahmaEvent):
    subject: str = EventSubject.ORDER_FILLED

    order_id: str = ""
    client_order_id: str = ""
    direction: str = ""
    fill_price: float = 0.0
    fill_qty: float = 0.0
    fee_usdt: float = 0.0
    slippage_pct: float = 0.0
    latency_ms: int = 0
    intent_trace_id: str = ""           # 追溯到OrderIntent


# ══════════════════════════════════════════════════════
#  PnL归因事件
# ══════════════════════════════════════════════════════
@dataclass
class PnLAttributionEvent(BrahmaEvent):
    subject: str = EventSubject.ATTRIBUTION_TRADE

    gross_pnl: float = 0.0
    fee_drag: float = 0.0
    funding_drag: float = 0.0
    slippage_drag: float = 0.0
    net_pnl: float = 0.0
    holding_hours: float = 0.0
    regime_at_entry: str = ""
    regime_at_exit: str = ""
    signal_score: float = 0.0
    direction: str = ""
    feature_attribution: Dict[str, float] = field(default_factory=dict)
    intent_trace_id: str = ""


# ══════════════════════════════════════════════════════
#  工厂函数
# ══════════════════════════════════════════════════════
def make_signal_event(
    symbol: str,
    direction: str,
    raw_score: float,
    final_score: float,
    regime: str,
    grade: str,
    blocked: bool,
    block_reason: str = "",
    regime_bonus: float = 0.0,
    noise_penalty: float = 0.0,
    confidence: float = 0.0,
    top_pos: list = None,
    top_neg: list = None,
    adjustment_trace: list = None,
    source: str = "brahma_analysis_runner",
) -> SignalScoredEvent:
    return SignalScoredEvent(
        symbol=symbol,
        direction=direction,
        raw_score=raw_score,
        regime_bonus=regime_bonus,
        final_score=final_score,
        regime=regime,
        grade=grade,
        blocked=blocked,
        block_reason=block_reason,
        noise_penalty=noise_penalty,
        valid_after_adj=not blocked and final_score >= 155,
        confidence=confidence,
        top_positive_features=top_pos or [],
        top_negative_features=top_neg or [],
        adjustment_trace=adjustment_trace or [],
        source=source,
    )


def make_risk_decision(
    signal_event: SignalScoredEvent,
    decision: str,
    size_nav: float,
    max_leverage: int,
    order_style: str = "POST_ONLY_LIMIT",
    blocked_layers: list = None,
    warnings: list = None,
    reason: str = "",
) -> RiskDecisionEvent:
    """从信号事件派生风控决策，继承trace_id"""
    evt = signal_event.child(
        subject=EventSubject.RISK_DECISION,
        source="risk_kernel",
    )
    return RiskDecisionEvent(
        **{k: v for k, v in evt.to_dict().items()
           if k in RiskDecisionEvent.__dataclass_fields__},
        decision=decision,
        final_size_nav=size_nav,
        max_leverage=max_leverage,
        order_style=order_style,
        blocked_layers=blocked_layers or [],
        warnings=warnings or [],
        reason=reason,
        signal_score=signal_event.final_score,
        regime=signal_event.regime,
    )


def make_order_intent(
    risk_event: RiskDecisionEvent,
    quantity: float,
    entry_price: Optional[float],
    stop_loss: float,
    take_profit: Optional[float] = None,
    leverage: int = 3,
    algo: str = "CHASE_LIMIT",
) -> OrderIntentEvent:
    """从风控决策派生订单意图，继承trace_id"""
    evt = risk_event.child(
        subject=EventSubject.ORDER_INTENT,
        source="order_intent_factory",
    )
    return OrderIntentEvent(
        **{k: v for k, v in evt.to_dict().items()
           if k in OrderIntentEvent.__dataclass_fields__},
        size_nav=risk_event.final_size_nav,
        quantity=quantity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        leverage=leverage,
        order_style=risk_event.order_style,
        algo=algo,
        signal_trace_id=risk_event.trace_id,
        risk_trace_id=risk_event.event_id,
    )
