"""
brahma_v6/execution/order_manager.py — OrderManager 订单生命周期管理器
Lean-Inspired 设计院 Phase 1 | 2026-07-08

借鉴 Lean OrderManager 思想：
  - 统一管理所有活跃 OrderTicket
  - 提供 OnOrderEvent 回调钩子（算法感知每次状态变化）
  - UNKNOWN 订单自动进入 reconciliation 队列
  - 硬规则：Fill 无 Ticket = 0 容忍
"""
from __future__ import annotations
import time
import uuid
import json
from pathlib import Path
from typing import Dict, List, Optional, Callable
from brahma_v6.execution.order_ticket import BrahmaOrderTicket, BrahmaOrderEvent
from brahma_v6.schemas.events import OrderIntentEvent, RiskDecisionEvent

BASE = Path(__file__).resolve().parents[2]
ORDERS_DIR = BASE / "data" / "orders"
ORDERS_DIR.mkdir(parents=True, exist_ok=True)


class OrderManager:
    """
    Lean-inspired OrderManager：
      - 所有 Ticket 的唯一管理者
      - submit_intent() 从 OrderIntentEvent 创建 Ticket
      - on_fill() / on_cancel() / on_reject() 推进生命周期
      - reconcile_unknowns() 定期处理 UNKNOWN 订单
    """

    def __init__(self, persist: bool = True):
        self._tickets: Dict[str, BrahmaOrderTicket] = {}   # ticket_id → Ticket
        self._intent_index: Dict[str, str] = {}            # intent_id → ticket_id
        self._trace_index: Dict[str, List[str]] = {}       # trace_id → [ticket_ids]
        self._callbacks: List[Callable[[BrahmaOrderEvent], None]] = []
        self._persist = persist
        self._reconcile_queue: List[str] = []              # ticket_ids 待对账

    # ── 创建 ──────────────────────────────────────────────
    def submit_intent(
        self,
        intent: OrderIntentEvent,
        risk: RiskDecisionEvent,
        source: str = "system",
    ) -> Optional[BrahmaOrderTicket]:
        """
        从 v6 OrderIntentEvent + RiskDecisionEvent 创建 BrahmaOrderTicket。
        硬规则：risk.decision 必须为 APPROVE/REDUCE，否则返回 None。
        """
        if risk.decision not in ("APPROVE", "REDUCE"):
            return None

        # 方向映射
        side = "BUY" if intent.direction == "LONG" else "SELL"

        ticket = BrahmaOrderTicket(
            trace_id=intent.trace_id,
            intent_id=intent.event_id,
            risk_id=risk.event_id,
            signal_id=intent.signal_trace_id or "",
            symbol=intent.symbol,
            side=side,
            order_type=risk.order_style if risk.order_style != "NONE" else "POST_ONLY_LIMIT",
            quantity=intent.quantity or 0.0,
            limit_price=intent.entry_price,
            leverage=intent.leverage,
            time_in_force=intent.time_in_force,
            reduce_only=intent.reduce_only,
        )
        # 推进到 RISK_APPROVED
        evt = ticket.transition("RISK_APPROVED", reason=f"risk={risk.decision}", source=source)
        self._register(ticket)
        if evt:
            self._fire_callback(evt)
        return ticket

    def create_ticket(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "POST_ONLY_LIMIT",
        limit_price: float = None,
        leverage: int = 3,
        trace_id: str = "",
        intent_id: str = "",
        risk_id: str = "",
        source: str = "manual",
    ) -> BrahmaOrderTicket:
        """直接创建 Ticket（兼容旧接口或手动开单）"""
        ticket = BrahmaOrderTicket(
            trace_id=trace_id or str(uuid.uuid4()),
            intent_id=intent_id,
            risk_id=risk_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            leverage=leverage,
        )
        evt = ticket.transition("RISK_APPROVED", source=source)
        self._register(ticket)
        if evt:
            self._fire_callback(evt)
        return ticket

    # ── 生命周期推进 ──────────────────────────────────────
    def on_submit(self, ticket_id: str, exchange_order_id: str = "", source: str = "system") -> Optional[BrahmaOrderEvent]:
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            return None
        ticket.exchange_order_id = exchange_order_id
        # RISK_APPROVED → SUBMITTING → SUBMITTED（自动经过中间态）
        if ticket.status == "RISK_APPROVED":
            e0 = ticket.transition("SUBMITTING", source=source)
            if e0:
                self._fire_callback(e0)
        evt = ticket.transition("SUBMITTED", reason=f"oid={exchange_order_id}", source=source)
        if evt:
            self._fire_callback(evt)
            self._persist_ticket(ticket)
        return evt

    def on_accept(self, ticket_id: str, source: str = "system") -> Optional[BrahmaOrderEvent]:
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            return None
        # SUBMITTED → ACCEPTED
        if ticket.status in ("SUBMITTING", "RISK_APPROVED"):
            ticket.transition("SUBMITTED", source=source)
        evt = ticket.transition("ACCEPTED", source=source)
        if evt:
            self._fire_callback(evt)
        return evt

    def on_fill(
        self, ticket_id: str, qty: float, price: float,
        fee: float = 0.0, slippage: float = 0.0, source: str = "exchange"
    ) -> Optional[BrahmaOrderEvent]:
        """成交回报。硬规则：无 Ticket 的 Fill 不被接受。"""
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            # 硬规则：Fill without Ticket = violation，记录到 DLQ
            self._log_dlq("FILL_WITHOUT_TICKET", ticket_id, qty, price)
            return None
        evt = ticket.fill(qty, price, fee, slippage, source)
        if evt:
            self._fire_callback(evt)
            self._persist_ticket(ticket)
        return evt

    def on_cancel(self, ticket_id: str, reason: str = "") -> Optional[BrahmaOrderEvent]:
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            return None
        if ticket.status not in ("SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED"):
            evt = ticket.transition("CANCEL_PENDING", reason=reason)
            if evt:
                self._fire_callback(evt)
        evt = ticket.transition("CANCELLED", reason=reason)
        if evt:
            self._fire_callback(evt)
            self._persist_ticket(ticket)
        return evt

    def on_reject(self, ticket_id: str, reason: str = "") -> Optional[BrahmaOrderEvent]:
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            return None
        evt = ticket.transition("REJECTED", reason=reason)
        if evt:
            self._fire_callback(evt)
            self._persist_ticket(ticket)
        return evt

    def on_unknown(self, ticket_id: str, reason: str = "exchange_timeout") -> Optional[BrahmaOrderEvent]:
        """订单状态未知 → 加入对账队列"""
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            return None
        evt = ticket.transition("UNKNOWN", reason=reason)
        if evt:
            self._reconcile_queue.append(ticket_id)
            self._fire_callback(evt)
        return evt

    def reconcile(self, ticket_id: str, final_status: str, reason: str = "") -> Optional[BrahmaOrderEvent]:
        """人工/自动对账：将 UNKNOWN 推进到确定状态"""
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            return None
        evt = ticket.transition(final_status, reason=f"reconcile: {reason}")
        if ticket_id in self._reconcile_queue:
            self._reconcile_queue.remove(ticket_id)
        if evt:
            self._fire_callback(evt)
            self._persist_ticket(ticket)
        return evt

    # ── 回调注册 ──────────────────────────────────────────
    def add_callback(self, fn: Callable[[BrahmaOrderEvent], None]) -> None:
        """注册 OnOrderEvent 回调（Lean 风格）"""
        self._callbacks.append(fn)

    # ── 查询 ──────────────────────────────────────────────
    def get_ticket(self, ticket_id: str) -> Optional[BrahmaOrderTicket]:
        return self._tickets.get(ticket_id)

    def get_by_intent(self, intent_id: str) -> Optional[BrahmaOrderTicket]:
        tid = self._intent_index.get(intent_id)
        return self._tickets.get(tid) if tid else None

    def get_by_trace(self, trace_id: str) -> List[BrahmaOrderTicket]:
        tids = self._trace_index.get(trace_id, [])
        return [self._tickets[t] for t in tids if t in self._tickets]

    def active_tickets(self) -> List[BrahmaOrderTicket]:
        return [t for t in self._tickets.values() if not t.is_terminal]

    def filled_tickets(self) -> List[BrahmaOrderTicket]:
        return [t for t in self._tickets.values() if t.status == "FILLED"]

    def pending_reconciliation(self) -> List[BrahmaOrderTicket]:
        return [self._tickets[tid] for tid in self._reconcile_queue if tid in self._tickets]

    def stats(self) -> Dict:
        tickets = list(self._tickets.values())
        status_counts: Dict[str, int] = {}
        for t in tickets:
            status_counts[t.status] = status_counts.get(t.status, 0) + 1
        total_violations = sum(len(t.violations) for t in tickets)
        return {
            "total_tickets": len(tickets),
            "active": len(self.active_tickets()),
            "filled": status_counts.get("FILLED", 0),
            "cancelled": status_counts.get("CANCELLED", 0),
            "rejected": status_counts.get("REJECTED", 0),
            "unknown": status_counts.get("UNKNOWN", 0),
            "pending_reconcile": len(self._reconcile_queue),
            "total_violations": total_violations,
            "by_status": status_counts,
        }

    # ── 内部 ──────────────────────────────────────────────
    def _register(self, ticket: BrahmaOrderTicket) -> None:
        self._tickets[ticket.ticket_id] = ticket
        if ticket.intent_id:
            self._intent_index[ticket.intent_id] = ticket.ticket_id
        if ticket.trace_id:
            self._trace_index.setdefault(ticket.trace_id, []).append(ticket.ticket_id)

    def _fire_callback(self, event: BrahmaOrderEvent) -> None:
        for fn in self._callbacks:
            try:
                fn(event)
            except Exception:
                pass

    def _persist_ticket(self, ticket: BrahmaOrderTicket) -> None:
        if not self._persist:
            return
        try:
            f = ORDERS_DIR / f"{ticket.ticket_id}.json"
            f.write_text(json.dumps(ticket.to_dict(), ensure_ascii=False, default=str))
        except Exception:
            pass

    def _log_dlq(self, violation: str, ticket_id: str, qty: float, price: float) -> None:
        try:
            dlq = ORDERS_DIR / "order_dlq.jsonl"
            entry = {"ts": time.time(), "violation": violation,
                     "ticket_id": ticket_id, "qty": qty, "price": price}
            with open(str(dlq), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass


if __name__ == "__main__":
    print("=== OrderManager 自检 ===\n")
    import time as _t

    om = OrderManager(persist=False)
    events_received = []
    om.add_callback(lambda e: events_received.append(e.status))

    # 模拟完整流程
    from brahma_v6.schemas.events import make_signal_event, OrderIntentEvent, RiskDecisionEvent
    import uuid as _u
    trace = str(_u.uuid4())

    sig = make_signal_event("BTCUSDT", "LONG", 145.0, 162.0, "BEAR_RECOVERY", "A", False)
    sig.ts_event = _t.time()
    risk = RiskDecisionEvent(
        symbol="BTCUSDT", trace_id=sig.trace_id, parent_event_id=sig.event_id,
        decision="APPROVE", final_size_nav=0.012, max_leverage=5,
    )
    intent = OrderIntentEvent(
        symbol="BTCUSDT", direction="LONG",
        trace_id=risk.trace_id, parent_event_id=risk.event_id,
        size_nav=0.012, quantity=0.001, leverage=5,
        entry_price=107000.0, stop_loss=103800.0,
    )

    ticket = om.submit_intent(intent, risk)
    print(f"Ticket创建: {ticket.ticket_id[:8]} status={ticket.status}")
    print(f"  intent_id追溯: {ticket.intent_id == intent.event_id}")
    print(f"  trace_id一致: {ticket.trace_id == intent.trace_id}")

    om.on_submit(ticket.ticket_id, "BN_ORDER_12345")
    om.on_accept(ticket.ticket_id)
    om.on_fill(ticket.ticket_id, 0.0005, 107010.0, fee=0.02, slippage=0.005)
    om.on_fill(ticket.ticket_id, 0.0005, 107020.0, fee=0.02, slippage=0.005)
    print(f"  成交后: status={ticket.status} filled={ticket.filled_qty:.4f}")

    # UNKNOWN → reconcile
    t2 = om.create_ticket("ETHUSDT", "SELL", 0.01, trace_id=trace, intent_id=str(_u.uuid4()))
    om.on_submit(t2.ticket_id)
    om.on_unknown(t2.ticket_id)
    print(f"\nUNKNOWN订单: {t2.ticket_id[:8]} pending_reconcile={len(om.pending_reconciliation())}")
    om.reconcile(t2.ticket_id, "CANCELLED", "exchange_confirmed_cancel")
    print(f"  对账后: status={t2.status} pending={len(om.pending_reconciliation())}")

    # Fill without Ticket
    om.on_fill("nonexistent_ticket", 0.01, 107000.0)  # 应进入 DLQ

    print(f"\n事件回调序列: {events_received}")
    print(f"Stats: {om.stats()}")
    print("\n✅ OrderManager 自检完成")
