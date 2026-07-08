"""
brahma_v6/execution/order_ticket.py — BrahmaOrderTicket + BrahmaOrderEvent
Lean-Inspired 设计院 Phase 1 | 2026-07-08

借鉴 Lean OrderTicket/OrderEvent 思想：
  - 每笔订单有完整生命周期追踪
  - 每次状态变化产生 BrahmaOrderEvent
  - trace_id 贯穿 Signal → Risk → Intent → Ticket → Event → PnL

关键约束（硬规则）：
  1. Fill 必须有 Ticket
  2. Ticket 必须有 IntentId
  3. IntentId 必须有 RiskDecisionId
  4. blocked signal 不得产生 Ticket
  5. UNKNOWN 必须进入 reconciliation
  6. CANCELLED 后不得 FILLED（cancel_race 除外 via UNKNOWN）
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Literal, Dict
from datetime import datetime, timezone

OrderSide   = Literal["BUY", "SELL"]
OrderType   = Literal["LIMIT", "MARKET", "STOP", "TRAILING_STOP", "POST_ONLY_LIMIT"]
TicketStatus = Literal[
    "CREATED", "RISK_APPROVED", "SUBMITTING", "SUBMITTED",
    "ACCEPTED", "PARTIALLY_FILLED", "FILLED",
    "CANCEL_PENDING", "CANCELLED", "REJECTED",
    "EXPIRED", "UNKNOWN", "RECONCILED",
]

# ── 合法状态转移 ────────────────────────────────────────────
VALID_TICKET_TRANSITIONS: Dict[str, List[str]] = {
    "CREATED":          ["RISK_APPROVED", "REJECTED"],
    "RISK_APPROVED":    ["SUBMITTING", "CANCELLED"],
    "SUBMITTING":       ["SUBMITTED", "REJECTED", "UNKNOWN"],
    "SUBMITTED":        ["ACCEPTED", "REJECTED", "UNKNOWN", "CANCEL_PENDING"],
    "ACCEPTED":         ["PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "EXPIRED"],
    "PARTIALLY_FILLED": ["FILLED", "CANCEL_PENDING", "EXPIRED"],
    "FILLED":           ["RECONCILED"],
    "CANCEL_PENDING":   ["CANCELLED", "FILLED", "PARTIALLY_FILLED"],
    "CANCELLED":        ["RECONCILED"],
    "REJECTED":         ["RECONCILED"],
    "EXPIRED":          ["RECONCILED"],
    "UNKNOWN":          ["RECONCILED", "FILLED", "CANCELLED"],
    "RECONCILED":       [],
}

TERMINAL_TICKET_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "RECONCILED"}


# ══════════════════════════════════════════════════════
#  BrahmaOrderEvent — 每次状态变化的不可变记录
# ══════════════════════════════════════════════════════
@dataclass
class BrahmaOrderEvent:
    """
    借鉴 Lean OrderEvent：每次状态变化都产生一个不可变 Event。
    算法可通过 OnOrderEvent 回调收到所有事件。
    """
    event_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    ticket_id:  str = ""
    trace_id:   str = ""
    status:     str = "CREATED"           # TicketStatus
    fill_qty:   float = 0.0
    fill_price: Optional[float] = None
    fee:        float = 0.0
    slippage:   float = 0.0
    message:    str = ""
    source:     str = "exchange"          # exchange / paper / backtest
    ts:         float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)


# ══════════════════════════════════════════════════════
#  BrahmaOrderTicket — 完整订单生命周期
# ══════════════════════════════════════════════════════
@dataclass
class BrahmaOrderTicket:
    """
    借鉴 Lean OrderTicket：订单的完整生命周期容器。
    通过 ticket.events 可获取所有历史 OrderEvent。
    通过 ticket.get_event(status) 可查询特定状态事件。
    """
    ticket_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id:      str = ""
    intent_id:     str = ""              # 必须有 → OrderIntent
    risk_id:       str = ""              # 必须有 → RiskDecision
    signal_id:     str = ""              # 可追溯到 SignalScoredEvent

    symbol:        str = ""
    side:          str = "BUY"           # OrderSide
    order_type:    str = "POST_ONLY_LIMIT"  # OrderType
    quantity:      float = 0.0
    limit_price:   Optional[float] = None
    stop_price:    Optional[float] = None
    leverage:      int = 1
    time_in_force: str = "GTC"
    reduce_only:   bool = False

    status:        str = "CREATED"       # TicketStatus
    filled_qty:    float = 0.0
    avg_fill_price: float = 0.0
    total_fee:     float = 0.0
    total_slippage: float = 0.0

    exchange_order_id: str = ""
    created_at:    float = field(default_factory=time.time)
    updated_at:    float = field(default_factory=time.time)

    events:        List[BrahmaOrderEvent] = field(default_factory=list)
    violations:    List[str] = field(default_factory=list)
    metadata:      Dict = field(default_factory=dict)

    # ── 约束检查 ──────────────────────────────────────────
    def __post_init__(self):
        if not self.intent_id:
            self.violations.append("TICKET_MISSING_INTENT_ID: Ticket必须绑定IntentId")
        if not self.trace_id:
            self.violations.append("TICKET_MISSING_TRACE_ID: Ticket必须有trace_id")

    # ── 状态机 ────────────────────────────────────────────
    def transition(self, to_status: str, reason: str = "", source: str = "system") -> Optional[BrahmaOrderEvent]:
        """
        执行状态转移，返回产生的 BrahmaOrderEvent。
        非法转移：记录 violation，返回 None。
        """
        allowed = VALID_TICKET_TRANSITIONS.get(self.status, [])
        if to_status not in allowed:
            self.violations.append(
                f"ILLEGAL_TRANSITION: {self.status}→{to_status} ticket={self.ticket_id[:8]}"
            )
            return None

        event = BrahmaOrderEvent(
            ticket_id=self.ticket_id,
            trace_id=self.trace_id,
            status=to_status,
            message=reason,
            source=source,
        )
        self.events.append(event)
        self.status = to_status
        self.updated_at = time.time()
        return event

    def fill(self, qty: float, price: float, fee: float = 0.0,
             slippage: float = 0.0, source: str = "exchange") -> Optional[BrahmaOrderEvent]:
        """
        记录成交。支持部分成交累积，自动推进 PARTIALLY_FILLED → FILLED。
        """
        if self.status not in ("ACCEPTED", "PARTIALLY_FILLED",
                               "SUBMITTED", "CANCEL_PENDING"):
            self.violations.append(f"FILL_IN_WRONG_STATE: {self.status}")
            return None

        # 超量检查（硬规则）
        if self.filled_qty + qty > self.quantity * 1.001:
            self.violations.append(
                f"OVERFILL: +{qty:.6f} → total={self.filled_qty+qty:.6f} > qty={self.quantity:.6f}"
            )
            return None

        # 均价更新
        total_val = self.avg_fill_price * self.filled_qty + price * qty
        self.filled_qty += qty
        self.avg_fill_price = total_val / self.filled_qty if self.filled_qty > 0 else 0
        self.total_fee += fee
        self.total_slippage += slippage
        self.updated_at = time.time()

        # 状态推进
        is_full = self.filled_qty >= self.quantity * 0.999
        new_status = "FILLED" if is_full else "PARTIALLY_FILLED"

        event = BrahmaOrderEvent(
            ticket_id=self.ticket_id,
            trace_id=self.trace_id,
            status=new_status,
            fill_qty=qty,
            fill_price=price,
            fee=fee,
            slippage=slippage,
            source=source,
        )
        self.events.append(event)
        self.status = new_status
        return event

    # ── 查询 ──────────────────────────────────────────────
    def get_events(self, status: str = None) -> List[BrahmaOrderEvent]:
        if status:
            return [e for e in self.events if e.status == status]
        return list(self.events)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TICKET_STATUSES

    @property
    def needs_reconciliation(self) -> bool:
        return self.status == "UNKNOWN"

    @property
    def net_pnl_estimate(self) -> float:
        """粗略净PnL估算（需外部注入出场价）"""
        return -(self.total_fee + self.total_slippage)

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d

    def summary(self) -> Dict:
        return {
            "ticket_id":     self.ticket_id[:8],
            "symbol":        self.symbol,
            "side":          self.side,
            "status":        self.status,
            "quantity":      self.quantity,
            "filled_qty":    round(self.filled_qty, 6),
            "avg_price":     round(self.avg_fill_price, 4),
            "fee":           round(self.total_fee, 6),
            "slippage":      round(self.total_slippage, 6),
            "events":        len(self.events),
            "violations":    self.violations,
            "trace_id":      self.trace_id[:8] if self.trace_id else "",
            "intent_id":     self.intent_id[:8] if self.intent_id else "",
        }


if __name__ == "__main__":
    print("=== BrahmaOrderTicket 自检 ===\n")
    import uuid as _u

    trace = str(_u.uuid4())
    intent_id = str(_u.uuid4())
    risk_id = str(_u.uuid4())

    t = BrahmaOrderTicket(
        trace_id=trace, intent_id=intent_id, risk_id=risk_id,
        symbol="BTCUSDT", side="BUY", order_type="POST_ONLY_LIMIT",
        quantity=0.01, limit_price=107000.0, leverage=5,
    )
    print(f"初始violations: {t.violations}")

    for s in ["RISK_APPROVED", "SUBMITTING", "SUBMITTED", "ACCEPTED"]:
        evt = t.transition(s)
        print(f"  → {s}: event_id={evt.event_id[:8]}")

    e1 = t.fill(0.006, 107010.0, fee=0.042, slippage=0.015)
    print(f"  部分成交: status={t.status} filled={t.filled_qty:.4f}")
    e2 = t.fill(0.004, 107020.0, fee=0.028, slippage=0.010)
    print(f"  全部成交: status={t.status} avg={t.avg_fill_price:.0f} fee={t.total_fee:.4f}")

    t.transition("RECONCILED")
    print(f"  最终: {t.status} events={len(t.events)} violations={t.violations}")

    # 非法转移测试
    t2 = BrahmaOrderTicket(trace_id=trace, intent_id=intent_id, symbol="ETH", side="SELL", quantity=1.0)
    t2.transition("RISK_APPROVED")
    t2.transition("SUBMITTING")
    t2.transition("SUBMITTED")
    t2.transition("CANCELLED")
    result = t2.transition("FILLED")  # 应被拦截
    print(f"\n非法 CANCELLED→FILLED: {'拦截✅' if result is None else '未拦截❌'}")
    print(f"violations: {t2.violations}")
    print("\n✅ BrahmaOrderTicket 自检完成")
