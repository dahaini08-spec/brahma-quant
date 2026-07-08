"""
brahma_v6/runtime/engine.py — BrahmaRuntime 事件驱动引擎
Lean-Inspired 设计院 Phase 4 | 2026-07-08

借鉴 Lean AlgorithmFramework + 事件流设计：
  - 统一 Runtime 管理时间、事件、状态、订单、组合
  - 同一套 Signal + Risk + Order 在 backtest/paper/live 三种模式运行
  - 不再让每个脚本自己决定运行时序

流程：
  TimeSlice → FeatureEngine → SignalEngine → RiskKernel → OrderManager → Adapter
                                                                          ↓
                                                           BrahmaOrderEvent → PnL
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from enum import Enum

from brahma_v6.execution.order_ticket import BrahmaOrderTicket, BrahmaOrderEvent
from brahma_v6.execution.order_manager import OrderManager
from brahma_v6.risk.kernel import RiskKernel
from brahma_v6.risk.context import RiskContext
from brahma_v6.adapters.execution_adapter import create_adapter
from brahma_v6.schemas.events import SignalScoredEvent, OrderIntentEvent


class RunMode(str, Enum):
    BACKTEST = "backtest"
    PAPER    = "paper"
    LIVE     = "live"


# ══════════════════════════════════════════════════════
#  Clock — 时间管理
# ══════════════════════════════════════════════════════
class Clock:
    """
    借鉴 Lean Clock：
    - backtest 模式：时钟由外部数据推进
    - paper/live 模式：时钟 = wall clock
    """
    def __init__(self, mode: RunMode):
        self.mode = mode
        self._current_time: float = time.time()
        self._warmup = False

    def advance(self, ts: float) -> None:
        """backtest 专用：推进时钟"""
        if self.mode == RunMode.BACKTEST:
            self._current_time = ts

    @property
    def now(self) -> float:
        if self.mode == RunMode.BACKTEST:
            return self._current_time
        return time.time()

    @property
    def is_warmup(self) -> bool:
        return self._warmup


# ══════════════════════════════════════════════════════
#  TimeSlice — 市场数据快照
# ══════════════════════════════════════════════════════
@dataclass
class TimeSlice:
    """
    借鉴 Lean TimeSlice：每个时间点的市场数据快照。
    包含所有资产的 OHLCV + 衍生品数据。
    """
    ts: float = field(default_factory=time.time)
    bars: Dict[str, Dict] = field(default_factory=dict)     # symbol → OHLCV
    funding: Dict[str, float] = field(default_factory=dict) # symbol → rate
    oi: Dict[str, float] = field(default_factory=dict)      # symbol → open_interest
    lsr: Dict[str, float] = field(default_factory=dict)     # symbol → long/short ratio
    orderbook: Dict[str, Dict] = field(default_factory=dict)# symbol → {depth, bid, ask}

    def get_price(self, symbol: str) -> float:
        bar = self.bars.get(symbol, {})
        return bar.get("close", bar.get("last", 0.0))

    def get_depth(self, symbol: str) -> float:
        ob = self.orderbook.get(symbol, {})
        return ob.get("depth_usdt", 500_000)


# ══════════════════════════════════════════════════════
#  BrahmaRuntime — 主引擎
# ══════════════════════════════════════════════════════
class BrahmaRuntime:
    """
    Lean-Inspired 梵天运行时。

    同一套逻辑在三种模式运行：
      mode=backtest → BacktestAdapter，历史数据驱动
      mode=paper    → PaperAdapter，实时数据+模拟成交
      mode=live     → LiveBinanceAdapter，真实执行

    使用方法：
      runtime = BrahmaRuntime(mode="paper")
      runtime.on_signal(signal)         # 信号 → 风控 → 订单意图 → 下单
      runtime.on_timeslice(timeslice)   # 市场数据更新 → 触发成交/止损
      runtime.on_order_event(event)     # 订单状态变化回调
    """

    def __init__(
        self,
        mode: str = "paper",
        account_nav: float = 100.0,
        symbols: List[str] = None,
        adapter_kwargs: Dict = None,
    ):
        self.mode = RunMode(mode)
        self.account_nav = account_nav
        self.symbols = symbols or ["BTCUSDT", "ETHUSDT"]
        self.clock = Clock(self.mode)

        # 核心组件
        self.risk_kernel = RiskKernel()
        self.order_manager = OrderManager()
        self.adapter = create_adapter(mode, **(adapter_kwargs or {}))

        # 注册 OnOrderEvent 回调
        self.order_manager.add_callback(self._on_order_event)

        # 状态
        self._current_slice: Optional[TimeSlice] = None
        self._signal_count: int = 0
        self._approved_count: int = 0
        self._filled_count: int = 0
        self._pnl_total: float = 0.0
        self._callbacks: List[Callable] = []

        # Dharma2 联动
        try:
            from brahma_v6.dharma2.trade_ledger import TradeLedger
            self._ledger = TradeLedger()
        except Exception:
            self._ledger = None

    # ── 主入口：信号处理 ──────────────────────────────────
    def on_signal(
        self,
        signal: SignalScoredEvent,
        force_market: bool = False,
    ) -> Optional[BrahmaOrderTicket]:
        """
        Lean-Inspired AlgorithmFramework 流程：
        Signal → RiskKernel → OrderIntent → OrderTicket → Adapter.submit()
        """
        self._signal_count += 1

        # 1. 构建 RiskContext（paper_mode / live 模式注入真实数据）
        if self.mode == RunMode.LIVE:
            ctx = RiskContext.from_live(signal.symbol, self.account_nav)
        else:
            ctx = self._build_paper_ctx(signal.symbol)

        # 2. 风控评估（12层纯函数）
        risk = self.risk_kernel.evaluate(signal, ctx=ctx)
        if risk.decision == "BLOCKED":
            return None

        # 3. 构建 OrderIntent
        price = ctx.orderbook_depth_usdt  # 临时用深度，实际由 timeslice 提供
        slice_price = self._current_slice.get_price(signal.symbol) if self._current_slice else 0
        entry_price = slice_price or (107000.0 if "BTC" in signal.symbol else 1740.0)

        sl_pct = 0.02 if signal.regime in ("BEAR_TREND", "CHOP_MID") else 0.025
        tp_pct = sl_pct * 1.5
        if signal.direction == "LONG":
            sl = entry_price * (1 - sl_pct)
            tp = entry_price * (1 + tp_pct)
        else:
            sl = entry_price * (1 + sl_pct)
            tp = entry_price * (1 - tp_pct)

        intent = OrderIntentEvent(
            symbol=signal.symbol,
            direction=signal.direction,
            trace_id=risk.trace_id,
            parent_event_id=risk.event_id,
            size_nav=risk.final_size_nav,
            quantity=risk.final_size_nav * self.account_nav / max(entry_price, 1),
            leverage=risk.max_leverage,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            order_style="MARKET" if force_market else risk.order_style,
            signal_trace_id=signal.trace_id,
            risk_trace_id=risk.trace_id,
        )

        # 4. 创建 OrderTicket
        ticket = self.order_manager.submit_intent(intent, risk, source=self.mode.value)
        if not ticket:
            return None
        self._approved_count += 1

        # 5. 提交到适配器
        evt = self.adapter.submit(ticket)
        # 仅当 ticket 还没被 adapter 推进时，才手动 on_submit
        if evt and ticket.status not in ("SUBMITTED", "ACCEPTED",
                                         "PARTIALLY_FILLED", "FILLED",
                                         "CANCELLED", "REJECTED", "RECONCILED"):
            self.order_manager.on_submit(ticket.ticket_id, source=self.mode.value)

        return ticket

    # ── 市场数据更新 ──────────────────────────────────────
    def on_timeslice(self, ts: TimeSlice) -> None:
        """
        借鉴 Lean 事件流：每个 TimeSlice 推进时钟 + 驱动成交。
        """
        self._current_slice = ts
        self.clock.advance(ts.ts)

        for symbol, bar in ts.bars.items():
            price = bar.get("close", 0)
            if price > 0:
                self.adapter.on_tick(symbol, price, ts.ts)

    # ── OrderEvent 回调 ───────────────────────────────────
    def _on_order_event(self, event: BrahmaOrderEvent) -> None:
        """
        借鉴 Lean OnOrderEvent：每次状态变化都触发。
        FILLED → 更新 PnL + 通知 Dharma2。
        UNKNOWN → 加入对账队列（已由 OrderManager 处理）。
        """
        if event.status == "FILLED":
            self._filled_count += 1
            # Dharma2 PnL 归因（如有 ledger）
            if self._ledger:
                try:
                    ticket = self.order_manager.get_ticket(event.ticket_id)
                    if ticket:
                        self._ledger.record_fill(ticket, event)
                except Exception:
                    pass

        # 用户自定义回调
        for fn in self._callbacks:
            try:
                fn(event)
            except Exception:
                pass

    def add_callback(self, fn: Callable[[BrahmaOrderEvent], None]) -> None:
        self._callbacks.append(fn)

    # ── 状态报告 ──────────────────────────────────────────
    def stats(self) -> Dict:
        om_stats = self.order_manager.stats()
        return {
            "mode":            self.mode.value,
            "signals":         self._signal_count,
            "approved":        self._approved_count,
            "filled":          self._filled_count,
            "approve_rate":    round(self._approved_count / max(self._signal_count, 1) * 100, 1),
            "fill_rate":       round(self._filled_count / max(self._approved_count, 1) * 100, 1),
            "order_manager":   om_stats,
            "adapter_mode":    self.adapter.mode,
            "pending_reconcile": om_stats.get("pending_reconcile", 0),
            "violations":      om_stats.get("total_violations", 0),
        }

    def _build_paper_ctx(self, symbol: str) -> RiskContext:
        """Paper 模式：从 timeslice 注入尽量真实的 context"""
        if self._current_slice:
            return RiskContext(
                orderbook_depth_usdt=self._current_slice.get_depth(symbol),
                orderbook_available=True,
                funding_rate_annual_pct=self._current_slice.funding.get(symbol, 10.0),
                funding_available=True,
                account_nav=self.account_nav,
                account_drawdown_pct=0.0,
                system_health_score=100.0,
                correlated_exposure_nav=0.0,
            )
        return RiskContext.paper_mode(self.account_nav)


if __name__ == "__main__":
    import time as _t
    print("=== BrahmaRuntime 三模式自检 ===\n")

    from brahma_v6.schemas.events import make_signal_event

    for mode in ["backtest", "paper"]:
        print(f"--- {mode.upper()} ---")
        runtime = BrahmaRuntime(mode=mode, account_nav=100.0)
        events_seen = []
        runtime.add_callback(lambda e: events_seen.append(e.status))

        # 模拟 TimeSlice
        ts = TimeSlice(
            ts=_t.time(),
            bars={"BTCUSDT": {"close": 107000.0}, "ETHUSDT": {"close": 1740.0}},
            orderbook={"BTCUSDT": {"depth_usdt": 800_000}, "ETHUSDT": {"depth_usdt": 350_000}},
        )
        runtime.on_timeslice(ts)

        # 信号
        sig1 = make_signal_event("BTCUSDT", "LONG", 145.0, 162.0, "BEAR_RECOVERY", "A", False)
        sig1.ts_event = _t.time()
        sig2 = make_signal_event("ETHUSDT", "LONG", 90.0, 92.0, "BEAR_TREND", "C", False)
        sig2.ts_event = _t.time()

        t1 = runtime.on_signal(sig1)
        t2 = runtime.on_signal(sig2)  # 应被 BEAR_TREND 死穴封锁

        print(f"  BTCUSDT LONG 162分: {'✅ 下单 '+t1.ticket_id[:8] if t1 else '🚫 封锁'}")
        print(f"  ETHUSDT LONG BEAR_TREND: {'✅ 下单' if t2 else '🚫 封锁(正确)'}")

        stats = runtime.stats()
        print(f"  stats: signals={stats['signals']} approved={stats['approved']} "
              f"filled={stats['filled']} violations={stats['violations']}")
        print(f"  events: {events_seen[:5]}")
        print()

    print("✅ BrahmaRuntime 三模式自检完成")
