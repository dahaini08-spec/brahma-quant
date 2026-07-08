"""
brahma_v6/execution/order_state.py — 订单生命周期状态机
设计院 P1 | 2026-07-08

13态完整订单状态机：
CREATED → RISK_APPROVED → INTENT_CREATED → SUBMITTING → SUBMITTED
→ ACCEPTED → PARTIALLY_FILLED → FILLED
→ CANCEL_PENDING → CANCELLED
→ REJECTED / EXPIRED / UNKNOWN → RECONCILED
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum


class OrderStatus(str, Enum):
    CREATED          = "CREATED"
    RISK_APPROVED    = "RISK_APPROVED"
    INTENT_CREATED   = "INTENT_CREATED"
    SUBMITTING       = "SUBMITTING"
    SUBMITTED        = "SUBMITTED"
    ACCEPTED         = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED           = "FILLED"
    CANCEL_PENDING   = "CANCEL_PENDING"
    CANCELLED        = "CANCELLED"
    REJECTED         = "REJECTED"
    EXPIRED          = "EXPIRED"
    UNKNOWN          = "UNKNOWN"
    RECONCILED       = "RECONCILED"


# 合法状态转移矩阵
VALID_TRANSITIONS: Dict[OrderStatus, List[OrderStatus]] = {
    OrderStatus.CREATED:          [OrderStatus.RISK_APPROVED, OrderStatus.REJECTED],
    OrderStatus.RISK_APPROVED:    [OrderStatus.INTENT_CREATED, OrderStatus.CANCELLED],
    OrderStatus.INTENT_CREATED:   [OrderStatus.SUBMITTING, OrderStatus.CANCELLED],
    OrderStatus.SUBMITTING:       [OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.UNKNOWN],
    OrderStatus.SUBMITTED:        [OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.UNKNOWN, OrderStatus.CANCEL_PENDING],
    OrderStatus.ACCEPTED:         [OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.EXPIRED],
    OrderStatus.PARTIALLY_FILLED: [OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.EXPIRED],
    OrderStatus.FILLED:           [OrderStatus.RECONCILED],
    OrderStatus.CANCEL_PENDING:   [OrderStatus.CANCELLED, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED],
    OrderStatus.CANCELLED:        [OrderStatus.RECONCILED],
    OrderStatus.REJECTED:         [OrderStatus.RECONCILED],
    OrderStatus.EXPIRED:          [OrderStatus.RECONCILED],
    OrderStatus.UNKNOWN:          [OrderStatus.RECONCILED, OrderStatus.FILLED, OrderStatus.CANCELLED],
    OrderStatus.RECONCILED:       [],
}

TERMINAL_STATES = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
                   OrderStatus.EXPIRED, OrderStatus.RECONCILED}


@dataclass
class OrderTransition:
    from_status: OrderStatus
    to_status: OrderStatus
    ts: float = field(default_factory=time.time)
    reason: str = ""


@dataclass
class OrderLifecycle:
    """单笔订单完整生命周期追踪"""
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    symbol: str = ""
    direction: str = ""
    quantity: float = 0.0
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.CREATED
    transitions: List[OrderTransition] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    violations: List[str] = field(default_factory=list)

    def transition(self, to: OrderStatus, reason: str = "") -> bool:
        """
        执行状态转移。非法转移记录 violation 并返回 False。
        约束：CANCELLED 后不得再 FILLED（cancel race 除外由 UNKNOWN 处理）。
        """
        allowed = VALID_TRANSITIONS.get(self.status, [])
        if to not in allowed:
            self.violations.append(
                f"ILLEGAL_TRANSITION: {self.status} → {to} (order={self.order_id[:8]})"
            )
            return False

        # 特殊约束：filled_qty 不得超过 quantity
        if to == OrderStatus.FILLED and self.filled_qty > self.quantity * 1.001:
            self.violations.append(
                f"OVERFILL: filled={self.filled_qty:.6f} > qty={self.quantity:.6f}"
            )

        self.transitions.append(OrderTransition(self.status, to, reason=reason))
        self.status = to
        return True

    def fill(self, qty: float, price: float) -> None:
        """记录成交（支持部分成交累积）"""
        if self.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED,
                               OrderStatus.SUBMITTED, OrderStatus.CANCEL_PENDING):
            self.violations.append(f"FILL_IN_WRONG_STATE: {self.status}")
            return
        prev_filled = self.filled_qty
        self.filled_qty = min(self.filled_qty + qty, self.quantity)
        # 均价加权
        if self.avg_fill_price == 0:
            self.avg_fill_price = price
        else:
            total_val = self.avg_fill_price * prev_filled + price * qty
            self.avg_fill_price = total_val / max(self.filled_qty, 1e-10)

        if self.filled_qty >= self.quantity * 0.999:
            self.transition(OrderStatus.FILLED, reason=f"full fill @{price:.2f}")
        else:
            if self.status != OrderStatus.PARTIALLY_FILLED:
                self.transition(OrderStatus.PARTIALLY_FILLED, reason=f"partial fill {qty:.6f}@{price:.2f}")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def needs_reconciliation(self) -> bool:
        return self.status == OrderStatus.UNKNOWN

    def summary(self) -> Dict:
        return {
            "order_id":       self.order_id[:8],
            "status":         self.status.value,
            "symbol":         self.symbol,
            "direction":      self.direction,
            "quantity":       self.quantity,
            "filled_qty":     self.filled_qty,
            "avg_fill_price": round(self.avg_fill_price, 4),
            "transition_count": len(self.transitions),
            "violations":     self.violations,
            "trace_id":       self.trace_id[:8] if self.trace_id else "",
        }


if __name__ == "__main__":
    print("=== OrderLifecycle 状态机自检 ===\n")
    o = OrderLifecycle(symbol="BTCUSDT", direction="LONG", quantity=0.01, trace_id=str(uuid.uuid4()))

    # 正常流程
    for step in [OrderStatus.RISK_APPROVED, OrderStatus.INTENT_CREATED,
                 OrderStatus.SUBMITTING, OrderStatus.SUBMITTED, OrderStatus.ACCEPTED]:
        ok = o.transition(step)
        print(f"  {step.value}: {'✅' if ok else '❌'}")

    o.fill(0.005, 107000.0)
    print(f"  部分成交: status={o.status.value} filled={o.filled_qty:.4f}")
    o.fill(0.005, 107050.0)
    print(f"  全部成交: status={o.status.value} filled={o.filled_qty:.4f} avg={o.avg_fill_price:.0f}")

    o.transition(OrderStatus.RECONCILED)
    print(f"  最终: {o.status.value} violations={o.violations}")

    # 非法转移测试
    o2 = OrderLifecycle(symbol="ETHUSDT", direction="SHORT", quantity=1.0)
    o2.transition(OrderStatus.RISK_APPROVED)
    o2.transition(OrderStatus.CANCELLED)
    # 尝试在CANCELLED后FILLED → 应被拦截
    ok = o2.transition(OrderStatus.FILLED)
    print(f"\n  非法转移 CANCELLED→FILLED: {'拦截✅' if not ok else '未拦截❌'}")
    print(f"  violations: {o2.violations}")
    print("\n✅ OrderLifecycle 状态机自检完成")
