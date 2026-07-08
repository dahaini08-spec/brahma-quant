"""
brahma_v6/execution/order_intent.py — OrderIntent前置执行层
设计院 × 顶级评估v6.0建议 2026-07-08

OrderIntent与真实Order完全分离：
  Intent = "我想要" (风控决策产物，不含exchange细节)
  Order  = "我已发" (真实挂单，含orderID/fillPrice等)

执行状态机：
  PENDING → PRE_TRADE_CHECK → SUBMITTED → ACCEPTED →
  PARTIAL_FILL → FILLED
                            → REJECTED
                            → CANCELLED
                            → EXPIRED
                            → UNKNOWN → reconcile
"""
from __future__ import annotations
import time
import uuid
import json
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List
from enum import Enum

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from brahma_v6.schemas.events import (
    OrderIntentEvent, OrderFilledEvent, RiskDecisionEvent,
    make_order_intent, EventSubject
)


# ══════════════════════════════════════════════════════
#  订单生命周期状态
# ══════════════════════════════════════════════════════
class OrderState(str, Enum):
    PENDING         = "PENDING"
    PRE_TRADE_CHECK = "PRE_TRADE_CHECK"
    SUBMITTED       = "SUBMITTED"
    ACCEPTED        = "ACCEPTED"
    PARTIAL_FILL    = "PARTIAL_FILL"
    FILLED          = "FILLED"
    REJECTED        = "REJECTED"        # 交易所拒绝
    LOCAL_DENIED    = "LOCAL_DENIED"    # 本地PreTrade拒绝
    CANCELLED       = "CANCELLED"
    EXPIRED         = "EXPIRED"
    UNKNOWN         = "UNKNOWN"         # 状态不明，需reconcile


# ══════════════════════════════════════════════════════
#  订单执行记录
# ══════════════════════════════════════════════════════
@dataclass
class OrderRecord:
    intent_event_id: str
    trace_id: str
    symbol: str
    direction: str
    quantity: float
    entry_price: Optional[float]
    stop_loss: float
    leverage: int
    order_style: str
    algo: str

    # 状态跟踪
    state: str = OrderState.PENDING
    exchange_order_id: str = ""
    client_order_id: str = field(default_factory=lambda: f"brahma_{uuid.uuid4().hex[:12]}")
    fill_price: float = 0.0
    fill_qty: float = 0.0
    fee_usdt: float = 0.0
    slippage_pct: float = 0.0
    latency_ms: int = 0

    # 时间戳
    ts_created: float = field(default_factory=time.time)
    ts_submitted: float = 0.0
    ts_filled: float = 0.0
    ts_last_update: float = field(default_factory=time.time)

    # 错误信息
    reject_reason: str = ""
    reconcile_needed: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)

    def execution_quality(self) -> Dict:
        """计算执行质量报告"""
        if self.state not in (OrderState.FILLED, OrderState.PARTIAL_FILL):
            return {"status": "not_filled"}
        latency = self.ts_filled - self.ts_submitted if self.ts_submitted else 0
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "fill_price": self.fill_price,
            "intended_price": self.entry_price,
            "slippage_pct": self.slippage_pct,
            "fee_usdt": self.fee_usdt,
            "latency_sec": round(latency, 3),
            "fill_qty": self.fill_qty,
            "intended_qty": self.quantity,
            "fill_rate": round(self.fill_qty / self.quantity, 4) if self.quantity else 0,
            "quality_score": self._quality_score(),
        }

    def _quality_score(self) -> float:
        """执行质量评分（0-100）"""
        score = 100.0
        score -= min(self.slippage_pct * 50, 30)  # 滑点扣分
        score -= min(self.fee_usdt * 2, 10)        # 手续费扣分
        latency = self.ts_filled - self.ts_submitted if self.ts_submitted else 0
        score -= min(latency / 10, 20)              # 延迟扣分
        fill_rate = self.fill_qty / self.quantity if self.quantity else 0
        score -= (1 - fill_rate) * 30               # 未成交率扣分
        return max(round(score, 1), 0.0)


# ══════════════════════════════════════════════════════
#  执行算法选择器
# ══════════════════════════════════════════════════════
class AlgoSelector:
    """根据信号特征自动选择执行算法"""

    RULES = {
        # (symbol_type, regime, is_breakout) → algo
        "high_liq_trend": "POST_ONLY_LIMIT",   # BTC/ETH趋势
        "breakout":        "CHASE_LIMIT",       # 突破信号
        "pump_hunter":     "LIMIT_ONLY",        # Pump Hunter信号
        "meme_small":      "LIMIT_ONLY",        # 小币/meme
        "emergency":       "MARKET",            # 紧急止损
        "_default":        "CHASE_LIMIT",
    }

    HIGH_LIQ_SYMS = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
    PUMP_SIGNAL_REGIMES = {"BEAR_RECOVERY", "CHOP_LOW"}

    @classmethod
    def select(cls, intent: OrderIntentEvent, regime: str = "", is_pump: bool = False) -> str:
        sym = intent.symbol
        if is_pump:
            return cls.RULES["pump_hunter"]
        if intent.reduce_only:
            return cls.RULES["emergency"]
        if sym in cls.HIGH_LIQ_SYMS:
            return cls.RULES["high_liq_trend"]
        if regime in cls.PUMP_SIGNAL_REGIMES:
            return cls.RULES["breakout"]
        return cls.RULES["_default"]


# ══════════════════════════════════════════════════════
#  PreTrade本地风控
# ══════════════════════════════════════════════════════
class PreTradeRisk:
    """
    提交前最后一道本地检查，防止在L1-L12之后出现边缘情况
    """

    @staticmethod
    def check(record: OrderRecord, current_price: float = 0.0) -> Tuple[bool, str]:
        """返回 (ok, reason)"""
        # 数量合法性
        if record.quantity <= 0:
            return False, f"数量非法: {record.quantity}"

        # 止损合法性
        if record.stop_loss <= 0:
            return False, "止损价格未设置"

        if record.direction == "LONG" and record.stop_loss >= (current_price or record.entry_price or 999999):
            return False, f"LONG止损{record.stop_loss}>=入场价，方向错误"

        if record.direction == "SHORT" and record.entry_price and record.stop_loss <= record.entry_price:
            return False, f"SHORT止损{record.stop_loss}<=入场价，方向错误"

        # 杠杆合法性
        if record.leverage > 20:
            return False, f"杠杆过高: {record.leverage}x"

        # client_order_id重复检查（实际应查本地订单簿）
        if not record.client_order_id.startswith("brahma_"):
            return False, "client_order_id格式错误"

        return True, "OK"


# ══════════════════════════════════════════════════════
#  订单意图处理器
# ══════════════════════════════════════════════════════
class OrderIntentProcessor:
    """
    把RiskDecisionEvent转化为OrderRecord，
    执行PreTrade检查，然后交给执行路由器提交。
    """

    INTENT_LOG = BASE / "data" / "order_intent_log.jsonl"

    def __init__(self):
        self.INTENT_LOG.parent.mkdir(parents=True, exist_ok=True)

    def from_risk_decision(
        self,
        risk: RiskDecisionEvent,
        entry_price: Optional[float],
        stop_loss: float,
        quantity: float,
        take_profit: Optional[float] = None,
        regime: str = "",
        is_pump: bool = False,
    ) -> Optional[OrderRecord]:
        """从风控决策构建OrderRecord"""

        if risk.decision == "BLOCKED":
            return None

        intent = make_order_intent(
            risk_event=risk,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=risk.max_leverage,
        )

        # 执行算法选择
        algo = AlgoSelector.select(intent, regime=regime, is_pump=is_pump)
        intent.algo = algo

        record = OrderRecord(
            intent_event_id=intent.event_id,
            trace_id=intent.trace_id,
            symbol=intent.symbol,
            direction=risk.payload.get("direction", ""),
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            leverage=risk.max_leverage,
            order_style=risk.order_style,
            algo=algo,
            state=OrderState.PENDING,
        )

        # 写入意图日志
        self._log_intent(intent, record)
        return record

    def pre_trade_check(
        self,
        record: OrderRecord,
        current_price: float = 0.0,
    ) -> bool:
        """PreTrade本地检查，失败→LOCAL_DENIED"""
        record.state = OrderState.PRE_TRADE_CHECK
        ok, reason = PreTradeRisk.check(record, current_price)
        if not ok:
            record.state = OrderState.LOCAL_DENIED
            record.reject_reason = reason
            print(f"[OrderIntent] ⛔ LOCAL_DENIED {record.symbol}: {reason}")
            return False
        return True

    def mark_submitted(self, record: OrderRecord, exchange_order_id: str = "") -> None:
        record.state = OrderState.SUBMITTED
        record.ts_submitted = time.time()
        record.exchange_order_id = exchange_order_id
        record.ts_last_update = time.time()

    def mark_filled(
        self,
        record: OrderRecord,
        fill_price: float,
        fill_qty: float,
        fee_usdt: float,
        exchange_order_id: str = "",
    ) -> OrderFilledEvent:
        record.state = OrderState.FILLED
        record.fill_price = fill_price
        record.fill_qty = fill_qty
        record.fee_usdt = fee_usdt
        record.ts_filled = time.time()
        record.ts_last_update = time.time()
        if exchange_order_id:
            record.exchange_order_id = exchange_order_id

        # 计算滑点
        if record.entry_price and record.entry_price > 0:
            if record.direction == "LONG":
                record.slippage_pct = (fill_price - record.entry_price) / record.entry_price * 100
            else:
                record.slippage_pct = (record.entry_price - fill_price) / record.entry_price * 100

        # 构建FilledEvent
        return OrderFilledEvent(
            symbol=record.symbol,
            trace_id=record.trace_id,
            parent_event_id=record.intent_event_id,
            order_id=record.exchange_order_id,
            client_order_id=record.client_order_id,
            direction=record.direction,
            fill_price=fill_price,
            fill_qty=fill_qty,
            fee_usdt=fee_usdt,
            slippage_pct=record.slippage_pct,
            latency_ms=int((record.ts_filled - record.ts_submitted) * 1000) if record.ts_submitted else 0,
            intent_trace_id=record.trace_id,
        )

    def mark_rejected(self, record: OrderRecord, reason: str) -> None:
        record.state = OrderState.REJECTED
        record.reject_reason = reason
        record.ts_last_update = time.time()
        print(f"[OrderIntent] ❌ EXCHANGE_REJECTED {record.symbol}: {reason}")

    def mark_unknown(self, record: OrderRecord) -> None:
        record.state = OrderState.UNKNOWN
        record.reconcile_needed = True
        record.ts_last_update = time.time()
        print(f"[OrderIntent] ⚠️  UNKNOWN_STATE {record.symbol} — reconcile needed")

    def _log_intent(self, intent: OrderIntentEvent, record: OrderRecord) -> None:
        try:
            entry = {
                "ts": time.time(),
                "trace_id": intent.trace_id,
                "event_id": intent.event_id,
                "symbol": intent.symbol,
                "algo": record.algo,
                "order_style": record.order_style,
                "size_nav": intent.size_nav,
                "quantity": record.quantity,
                "entry_price": record.entry_price,
                "stop_loss": record.stop_loss,
                "leverage": record.leverage,
            }
            with self.INTENT_LOG.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass


# ── 全局处理器单例 ───────────────────────────────────────
_processor: Optional[OrderIntentProcessor] = None


def get_processor() -> OrderIntentProcessor:
    global _processor
    if _processor is None:
        _processor = OrderIntentProcessor()
    return _processor


if __name__ == "__main__":
    from brahma_v6.schemas.events import make_signal_event
    from brahma_v6.risk.kernel import RiskKernel

    print("=== OrderIntent前置执行层 自检 ===\n")

    # 构建信号
    sig = make_signal_event(
        symbol="BTCUSDT",
        direction="LONG",
        raw_score=145.0,
        final_score=162.0,
        regime="BULL_TREND",
        grade="🔴神级",
        blocked=False,
        confidence=0.80,
    )

    # 12层风控
    kernel = RiskKernel()
    risk = kernel.evaluate(sig, skip_live_checks=True)
    print(f"风控决策: {risk.decision}  size={risk.final_size_nav*100:.2f}%NAV")

    # 构建OrderRecord
    processor = OrderIntentProcessor()
    record = processor.from_risk_decision(
        risk=risk,
        entry_price=62000.0,
        stop_loss=60800.0,
        quantity=0.001,
        regime="BULL_TREND",
    )

    if record:
        # PreTrade检查
        ok = processor.pre_trade_check(record, current_price=62000.0)
        print(f"PreTrade: {'PASS ✅' if ok else '❌ DENIED'}")
        print(f"State: {record.state}")
        print(f"Algo: {record.algo}")
        print(f"trace_id: {record.trace_id[:8]}...")

        # 模拟成交
        processor.mark_submitted(record, exchange_order_id="EX123456")
        fill_event = processor.mark_filled(record, 62050.0, 0.001, 0.062)
        quality = record.execution_quality()
        print(f"\n执行质量: slippage={quality['slippage_pct']:.3f}%  quality_score={quality['quality_score']}")
    else:
        print("⛔ BLOCKED — 无法创建OrderRecord")

    print("\n✅ OrderIntent前置执行层 自检完成")
