"""
brahma_v6/execution/reconciler.py — OMS Reconciler
设计院 9.2 升级 | 2026-07-08

UNKNOWN 订单自动对账机制：
  1. 定期扫描 UNKNOWN tickets
  2. 向 Binance API 查询真实状态
  3. 自动推进到 FILLED / CANCELLED / RECONCILED
  4. 无法确认的超时订单 → 强制 CANCELLED + 告警

架构原则：
  - Reconciler 是 OrderManager 的扩展，不替代它
  - 所有状态变更仍通过 OrderManager.reconcile() 执行
  - 支持 paper/backtest 模式（mock 对账）
"""
from __future__ import annotations
import time
import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

BASE = Path(__file__).resolve().parents[2]


@dataclass
class ReconcileResult:
    ticket_id: str
    from_status: str
    to_status: str
    method: str          # "api_query" | "timeout_force" | "mock" | "manual"
    exchange_status: str = ""
    fill_qty: float = 0.0
    fill_price: float = 0.0
    fee: float = 0.0
    reason: str = ""
    ts: float = field(default_factory=time.time)
    success: bool = True


class OMSReconciler:
    """
    UNKNOWN 订单自动对账器。

    工作流程：
      poll_interval=60s 定期扫描 OrderManager.pending_reconciliation()
      → 每个 UNKNOWN ticket：
          1. 查 Binance API (live) 或 mock (paper/backtest)
          2. 根据 exchange 回报推进状态
          3. 超时 timeout_s=300 → 强制 CANCELLED
      → 结果写入 reconcile_log.jsonl
    """

    def __init__(
        self,
        order_manager,
        mode: str = "paper",
        poll_interval: float = 60.0,
        timeout_s: float = 300.0,
        api_key: str = "",
        api_secret: str = "",
        on_reconcile: Optional[Callable[[ReconcileResult], None]] = None,
    ):
        from brahma_v6.execution.order_manager import OrderManager
        self._om: OrderManager = order_manager
        self._mode = mode
        self._poll_interval = poll_interval
        self._timeout_s = timeout_s
        self._api_key = api_key
        self._api_secret = api_secret
        self._on_reconcile = on_reconcile
        self._log_file = BASE / "data" / "orders" / "reconcile_log.jsonl"
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._results: List[ReconcileResult] = []

    # ── 启动/停止 ──────────────────────────────────────────
    def start(self) -> None:
        """启动后台对账线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="OMSReconciler")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    # ── 单次对账（可手动调用）────────────────────────────────
    def run_once(self) -> List[ReconcileResult]:
        """立即执行一次对账扫描，返回本次结果列表"""
        pending = self._om.pending_reconciliation()
        results = []
        for ticket in pending:
            result = self._reconcile_ticket(ticket)
            if result:
                results.append(result)
                self._results.append(result)
                self._log(result)
                if self._on_reconcile:
                    try:
                        self._on_reconcile(result)
                    except Exception:
                        pass
        return results

    # ── 内部 ──────────────────────────────────────────────
    def _loop(self) -> None:
        while self._running:
            try:
                self.run_once()
            except Exception:
                pass
            time.sleep(self._poll_interval)

    def _reconcile_ticket(self, ticket) -> Optional[ReconcileResult]:
        from brahma_v6.execution.order_ticket import BrahmaOrderTicket
        if ticket.status != "UNKNOWN":
            return None

        age = time.time() - ticket.updated_at

        # 1. 超时强制处理
        if age > self._timeout_s:
            self._om.reconcile(ticket.ticket_id, "CANCELLED",
                               reason=f"reconciler_timeout_{int(age)}s")
            r = ReconcileResult(
                ticket_id=ticket.ticket_id,
                from_status="UNKNOWN",
                to_status="CANCELLED",
                method="timeout_force",
                reason=f"超时{int(age)}s → 强制CANCELLED",
            )
            return r

        # 2. Live 模式：查 Binance API
        if self._mode == "live" and self._api_key:
            return self._reconcile_via_api(ticket)

        # 3. Paper/Backtest 模式：mock 对账（按概率模拟）
        return self._reconcile_mock(ticket)

    def _reconcile_via_api(self, ticket) -> Optional[ReconcileResult]:
        """真实 Binance API 查询（Live 模式）"""
        try:
            import hmac, hashlib, time as _t
            import urllib.request, urllib.parse

            base_url = "https://fapi.binance.com"
            ts = int(_t.time() * 1000)
            params = f"symbol={ticket.symbol}&orderId={ticket.exchange_order_id}&timestamp={ts}"
            sig = hmac.new(
                self._api_secret.encode(), params.encode(), hashlib.sha256
            ).hexdigest()
            url = f"{base_url}/fapi/v1/order?{params}&signature={sig}"
            req = urllib.request.Request(url, headers={"X-MBX-APIKEY": self._api_key})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())

            status = data.get("status", "UNKNOWN")
            filled = float(data.get("executedQty", 0))
            price = float(data.get("avgPrice", 0))
            fee = 0.0

            if status in ("FILLED", "PARTIALLY_FILLED"):
                if filled > 0 and price > 0:
                    self._om.on_fill(ticket.ticket_id, filled, price, fee=fee, source="reconciler")
                final = "FILLED" if status == "FILLED" else "PARTIALLY_FILLED"
            elif status in ("CANCELED", "CANCELLED", "EXPIRED"):
                final = "CANCELLED"
                self._om.reconcile(ticket.ticket_id, "CANCELLED", "api_confirmed_cancel")
            else:
                final = "UNKNOWN"  # 仍未知，等下轮

            return ReconcileResult(
                ticket_id=ticket.ticket_id,
                from_status="UNKNOWN", to_status=final,
                method="api_query", exchange_status=status,
                fill_qty=filled, fill_price=price,
            )
        except Exception as e:
            return ReconcileResult(
                ticket_id=ticket.ticket_id,
                from_status="UNKNOWN", to_status="UNKNOWN",
                method="api_query", reason=str(e), success=False,
            )

    def _reconcile_mock(self, ticket) -> ReconcileResult:
        """Paper/Backtest 模式的 mock 对账（80% FILLED / 20% CANCELLED）"""
        import random
        if random.random() < 0.8:
            # 模拟已成交
            fill_price = (ticket.limit_price or 107000.0) * 1.001
            self._om.on_accept(ticket.ticket_id, source="reconciler")
            self._om.on_fill(ticket.ticket_id, ticket.quantity, fill_price,
                             fee=fill_price * ticket.quantity * 0.0004,
                             source="reconciler")
            return ReconcileResult(
                ticket_id=ticket.ticket_id, from_status="UNKNOWN",
                to_status="FILLED", method="mock",
                fill_qty=ticket.quantity, fill_price=fill_price,
            )
        else:
            self._om.reconcile(ticket.ticket_id, "CANCELLED", "mock_cancel")
            return ReconcileResult(
                ticket_id=ticket.ticket_id, from_status="UNKNOWN",
                to_status="CANCELLED", method="mock",
            )

    def _log(self, result: ReconcileResult) -> None:
        try:
            entry = {
                "ts": result.ts, "ticket_id": result.ticket_id,
                "from": result.from_status, "to": result.to_status,
                "method": result.method, "success": result.success,
                "reason": result.reason, "fill_qty": result.fill_qty,
                "fill_price": result.fill_price,
            }
            with self._log_file.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def stats(self) -> Dict:
        return {
            "total_reconciled": len(self._results),
            "filled":    sum(1 for r in self._results if r.to_status == "FILLED"),
            "cancelled": sum(1 for r in self._results if r.to_status == "CANCELLED"),
            "failed":    sum(1 for r in self._results if not r.success),
            "by_method": {
                m: sum(1 for r in self._results if r.method == m)
                for m in ("api_query", "mock", "timeout_force", "manual")
            },
        }


if __name__ == "__main__":
    import sys; sys.path.insert(0, str(BASE))
    from brahma_v6.execution.order_manager import OrderManager
    from brahma_v6.execution.order_ticket import BrahmaOrderTicket
    import uuid, random

    print("=== OMSReconciler 自检 ===\n")
    random.seed(42)

    om = OrderManager(persist=False)
    reconciler = OMSReconciler(om, mode="paper", timeout_s=0.001)

    # 制造5个 UNKNOWN 订单
    for i in range(5):
        t = BrahmaOrderTicket(
            trace_id=str(uuid.uuid4()), intent_id=str(uuid.uuid4()),
            symbol="BTCUSDT", side="BUY", quantity=0.001,
            limit_price=107000.0 + i * 100, leverage=5,
        )
        t.transition("RISK_APPROVED")
        t.transition("SUBMITTING")
        t.transition("SUBMITTED")
        t.transition("UNKNOWN", reason="exchange_timeout")
        om._register(t)
        om._reconcile_queue.append(t.ticket_id)

    print(f"UNKNOWN订单: {len(om.pending_reconciliation())}")
    results = reconciler.run_once()
    print(f"对账结果: {len(results)}条")
    for r in results:
        print(f"  {r.ticket_id[:8]} → {r.to_status} via {r.method}")

    print(f"\nStats: {reconciler.stats()}")
    print(f"OM Stats: {om.stats()}")
    print("\n✅ OMSReconciler 自检完成")
