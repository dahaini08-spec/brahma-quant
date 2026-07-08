"""
brahma_v6/adapters/execution_adapter.py — 统一 ExecutionAdapter 接口
Lean-Inspired 设计院 Phase 2 | 2026-07-08

核心设计：
  同一套 OrderIntent → OrderTicket，在三种模式下行为不同，接口相同。
  Backtest / Paper / Live 可无缝切换。

接口契约（Protocol）：
  submit(ticket)
  cancel(ticket_id)
  query_order(ticket_id)
  open_orders()
  positions()

三种实现：
  BacktestAdapter  — 历史回放，填单用 FillModel
  PaperAdapter     — 实时行情，模拟成交
  LiveBinanceAdapter — 真实交易所，只信 exchange 回报
"""
from __future__ import annotations
import time
import uuid
import json
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable

from brahma_v6.execution.order_ticket import BrahmaOrderTicket, BrahmaOrderEvent

BASE = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════
#  Protocol 接口定义
# ══════════════════════════════════════════════════════
@runtime_checkable
class ExecutionAdapter(Protocol):
    """
    Lean-Inspired ExecutionAdapter Protocol。
    上层代码只调用这个接口，不感知底层是回测/Paper/实盘。
    """
    mode: str  # "backtest" | "paper" | "live"

    def submit(self, ticket: BrahmaOrderTicket) -> Optional[BrahmaOrderEvent]: ...
    def cancel(self, ticket_id: str, reason: str = "") -> Optional[BrahmaOrderEvent]: ...
    def query_order(self, ticket_id: str) -> Optional[BrahmaOrderTicket]: ...
    def open_orders(self) -> List[BrahmaOrderTicket]: ...
    def positions(self) -> Dict[str, Dict]: ...
    def on_tick(self, symbol: str, price: float, ts: float = None) -> None: ...


# ══════════════════════════════════════════════════════
#  BacktestAdapter
# ══════════════════════════════════════════════════════
class BacktestAdapter:
    """
    回测适配器。
    订单立即以 limit_price（含滑点模型）模拟成交。
    严格禁止使用未来数据（由调用方保证 OHLCV 时序）。
    """
    mode = "backtest"

    def __init__(self, slippage_bps: float = 2.0, fee_rate: float = 0.0004):
        self._tickets: Dict[str, BrahmaOrderTicket] = {}
        self._positions: Dict[str, Dict] = {}
        self.slippage_bps = slippage_bps
        self.fee_rate = fee_rate

    def submit(self, ticket: BrahmaOrderTicket) -> Optional[BrahmaOrderEvent]:
        """回测立即模拟成交（如果价格合理）"""
        self._tickets[ticket.ticket_id] = ticket
        ticket.transition("SUBMITTING", source="backtest")
        ticket.transition("SUBMITTED", source="backtest")
        ticket.transition("ACCEPTED", source="backtest")

        # 模拟滑点
        base_price = ticket.limit_price or ticket.stop_price or 0.0
        if base_price <= 0:
            return ticket.transition("REJECTED", reason="no_valid_price", source="backtest")

        slip = base_price * self.slippage_bps / 10000
        fill_price = base_price + slip if ticket.side == "BUY" else base_price - slip
        fee = fill_price * ticket.quantity * self.fee_rate
        slippage_cost = abs(fill_price - base_price) * ticket.quantity

        evt = ticket.fill(ticket.quantity, fill_price, fee=fee,
                          slippage=slippage_cost, source="backtest")
        if evt:
            self._update_position(ticket, fill_price)
        return evt

    def cancel(self, ticket_id: str, reason: str = "") -> Optional[BrahmaOrderEvent]:
        ticket = self._tickets.get(ticket_id)
        if not ticket or ticket.is_terminal:
            return None
        return ticket.transition("CANCELLED", reason=reason, source="backtest")

    def query_order(self, ticket_id: str) -> Optional[BrahmaOrderTicket]:
        return self._tickets.get(ticket_id)

    def open_orders(self) -> List[BrahmaOrderTicket]:
        return [t for t in self._tickets.values() if not t.is_terminal]

    def positions(self) -> Dict[str, Dict]:
        return dict(self._positions)

    def on_tick(self, symbol: str, price: float, ts: float = None) -> None:
        """回测 tick：检查止损/止盈（由外部提供价格）"""
        pass  # 由 BacktestEngine 驱动

    def _update_position(self, ticket: BrahmaOrderTicket, fill_price: float) -> None:
        sym = ticket.symbol
        if sym not in self._positions:
            self._positions[sym] = {"qty": 0.0, "avg_price": 0.0, "side": ""}
        pos = self._positions[sym]
        if ticket.side == "BUY":
            total = pos["qty"] * pos["avg_price"] + ticket.quantity * fill_price
            pos["qty"] += ticket.quantity
            pos["avg_price"] = total / pos["qty"] if pos["qty"] > 0 else 0
            pos["side"] = "LONG"
        else:
            pos["qty"] = max(0, pos["qty"] - ticket.quantity)
            if pos["qty"] == 0:
                pos["avg_price"] = 0
                pos["side"] = ""


# ══════════════════════════════════════════════════════
#  PaperAdapter
# ══════════════════════════════════════════════════════
class PaperAdapter:
    """
    Paper 适配器。
    使用实时 tick 驱动成交模拟，比 Backtest 更真实（排队、部分成交）。
    """
    mode = "paper"

    def __init__(self,
                 slippage_bps: float = 3.0,
                 fee_rate: float = 0.0004,
                 partial_fill_prob: float = 0.15):
        self._tickets: Dict[str, BrahmaOrderTicket] = {}
        self._positions: Dict[str, Dict] = {}
        self._pending: List[str] = []          # 待成交的 ticket_ids
        self._last_prices: Dict[str, float] = {}
        self.slippage_bps = slippage_bps
        self.fee_rate = fee_rate
        self.partial_fill_prob = partial_fill_prob

    def submit(self, ticket: BrahmaOrderTicket) -> Optional[BrahmaOrderEvent]:
        """Paper 提交：进入待成交队列"""
        self._tickets[ticket.ticket_id] = ticket
        ticket.transition("SUBMITTING", source="paper")
        ticket.transition("SUBMITTED", source="paper")
        ticket.transition("ACCEPTED", source="paper")
        self._pending.append(ticket.ticket_id)
        # 如果有最新 tick，立即尝试成交
        last_price = self._last_prices.get(ticket.symbol)
        if last_price:
            return self._try_fill(ticket, last_price)
        return None

    def cancel(self, ticket_id: str, reason: str = "") -> Optional[BrahmaOrderEvent]:
        ticket = self._tickets.get(ticket_id)
        if not ticket or ticket.is_terminal:
            return None
        if ticket_id in self._pending:
            self._pending.remove(ticket_id)
        return ticket.transition("CANCELLED", reason=reason, source="paper")

    def query_order(self, ticket_id: str) -> Optional[BrahmaOrderTicket]:
        return self._tickets.get(ticket_id)

    def open_orders(self) -> List[BrahmaOrderTicket]:
        return [t for t in self._tickets.values() if not t.is_terminal]

    def positions(self) -> Dict[str, Dict]:
        return dict(self._positions)

    def on_tick(self, symbol: str, price: float, ts: float = None) -> None:
        """实时 tick 驱动成交"""
        self._last_prices[symbol] = price
        for tid in list(self._pending):
            ticket = self._tickets.get(tid)
            if not ticket or ticket.is_terminal:
                self._pending.remove(tid)
                continue
            if ticket.symbol == symbol:
                evt = self._try_fill(ticket, price)
                if ticket.is_terminal and tid in self._pending:
                    self._pending.remove(tid)

    def _try_fill(self, ticket: BrahmaOrderTicket, market_price: float) -> Optional[BrahmaOrderEvent]:
        base = ticket.limit_price or market_price
        # LIMIT: 只有市价优于限价才成交
        if ticket.order_type in ("LIMIT", "POST_ONLY_LIMIT"):
            if ticket.side == "BUY" and market_price > base * 1.001:
                return None  # 未到价
            if ticket.side == "SELL" and market_price < base * 0.999:
                return None

        slip = base * self.slippage_bps / 10000 * random.uniform(0.7, 1.3)
        fill_price = base + slip if ticket.side == "BUY" else base - slip

        # 部分成交模拟
        remaining = ticket.quantity - ticket.filled_qty
        if random.random() < self.partial_fill_prob and remaining > 0.002:
            fill_qty = remaining * random.uniform(0.3, 0.7)
        else:
            fill_qty = remaining

        fee = fill_price * fill_qty * self.fee_rate
        slip_cost = abs(fill_price - base) * fill_qty

        evt = ticket.fill(fill_qty, fill_price, fee=fee, slippage=slip_cost, source="paper")
        if evt and ticket.status == "FILLED":
            self._update_position(ticket, fill_price)
        return evt

    def _update_position(self, ticket: BrahmaOrderTicket, fill_price: float) -> None:
        sym = ticket.symbol
        pos = self._positions.setdefault(sym, {"qty": 0.0, "avg_price": 0.0, "side": ""})
        if ticket.side == "BUY":
            total = pos["qty"] * pos["avg_price"] + ticket.quantity * fill_price
            pos["qty"] += ticket.quantity
            pos["avg_price"] = total / pos["qty"] if pos["qty"] > 0 else 0
            pos["side"] = "LONG"
        else:
            pos["qty"] = max(0.0, pos["qty"] - ticket.quantity)
            pos["side"] = "SHORT" if pos["qty"] > 0 else ""


# ══════════════════════════════════════════════════════
#  LiveBinanceAdapter (骨架)
# ══════════════════════════════════════════════════════
class LiveBinanceAdapter:
    """
    实盘 Binance 适配器。
    核心原则：只信交易所回报，不自行假设成交。
    submit() → 发送真实订单到 Binance API。
    on_tick() → 由 WebSocket 推送，驱动止损/平仓逻辑。
    """
    mode = "live"

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = True):
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._tickets: Dict[str, BrahmaOrderTicket] = {}
        self._eid_to_tid: Dict[str, str] = {}  # exchange_order_id → ticket_id

    def submit(self, ticket: BrahmaOrderTicket) -> Optional[BrahmaOrderEvent]:
        """
        发送真实订单到 Binance Futures API。
        返回 SUBMITTED event（不是 FILLED，填单由 WebSocket 回报）。
        """
        # TODO: 接入 binance-cli / papi 下单
        # 当前返回 SUBMITTED 骨架，等待 on_order_update() 推进
        self._tickets[ticket.ticket_id] = ticket
        ticket.transition("SUBMITTING", source="live")
        evt = ticket.transition("SUBMITTED", reason="sent_to_exchange", source="live")
        return evt

    def cancel(self, ticket_id: str, reason: str = "") -> Optional[BrahmaOrderEvent]:
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            return None
        # TODO: 发送 cancel 到交易所
        return ticket.transition("CANCEL_PENDING", reason=reason, source="live")

    def query_order(self, ticket_id: str) -> Optional[BrahmaOrderTicket]:
        return self._tickets.get(ticket_id)

    def open_orders(self) -> List[BrahmaOrderTicket]:
        return [t for t in self._tickets.values() if not t.is_terminal]

    def positions(self) -> Dict[str, Dict]:
        # TODO: 从 binance-cli 读取真实持仓
        return {}

    def on_tick(self, symbol: str, price: float, ts: float = None) -> None:
        # 实盘 tick 来自 WebSocket，用于监控止损
        pass

    def on_order_update(self, exchange_event: Dict) -> Optional[BrahmaOrderEvent]:
        """
        处理交易所 WebSocket 订单更新。
        这是 Live 模式唯一允许推进订单状态的入口。
        """
        eid = str(exchange_event.get("orderId", ""))
        tid = self._eid_to_tid.get(eid)
        ticket = self._tickets.get(tid) if tid else None
        if not ticket:
            return None

        status = exchange_event.get("X", exchange_event.get("status", ""))
        qty = float(exchange_event.get("l", exchange_event.get("lastFilledQty", 0)))
        price = float(exchange_event.get("L", exchange_event.get("lastFillPrice", 0)))
        fee = float(exchange_event.get("n", exchange_event.get("commission", 0)))

        if status in ("PARTIALLY_FILLED", "FILLED") and qty > 0:
            return ticket.fill(qty, price, fee=fee, source="live")
        elif status == "CANCELED":
            return ticket.transition("CANCELLED", source="live")
        elif status == "REJECTED":
            return ticket.transition("REJECTED", source="live")
        elif status == "EXPIRED":
            return ticket.transition("EXPIRED", source="live")
        return None


# ── 工厂函数 ────────────────────────────────────────────────
def create_adapter(mode: str, **kwargs) -> ExecutionAdapter:
    """
    统一工厂：根据 mode 返回对应适配器。
    上层代码不直接 import 具体适配器，通过此函数获取。
    """
    if mode == "backtest":
        return BacktestAdapter(**kwargs)
    elif mode == "paper":
        return PaperAdapter(**kwargs)
    elif mode == "live":
        return LiveBinanceAdapter(**kwargs)
    raise ValueError(f"未知 mode: {mode}，支持 backtest/paper/live")


if __name__ == "__main__":
    print("=== ExecutionAdapter 三模式自检 ===\n")
    import uuid as _u

    def _make_ticket(sym, side, qty, price, mode_label):
        return BrahmaOrderTicket(
            trace_id=str(_u.uuid4()),
            intent_id=str(_u.uuid4()),
            symbol=sym, side=side, quantity=qty,
            limit_price=price, leverage=5,
        )

    for mode in ["backtest", "paper"]:
        adapter = create_adapter(mode)
        ticket = _make_ticket("BTCUSDT", "BUY", 0.001, 107000.0, mode)
        adapter.submit(ticket)
        if mode == "paper":
            adapter.on_tick("BTCUSDT", 107000.0)
        print(f"[{mode}] status={ticket.status} filled={ticket.filled_qty:.4f} "
              f"fee={ticket.total_fee:.5f} violations={ticket.violations}")

    print("\n✅ ExecutionAdapter 三模式自检完成")
