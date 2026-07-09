"""
brahma_v6/dharma2/trade_ledger.py — 完整交易证据链
Lean-Inspired 设计院 Phase 5 | 2026-07-08

每笔交易必须记录：
  trace_id / signal_id / risk_id / intent_id / ticket_id
  symbol / direction / regime / score
  entry / exit / gross_pnl / fee / slippage / funding / impact / net_pnl
  MAE / MFE / holding_hours / execution_mode
"""
from __future__ import annotations
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

# v6 强类型 TradeRecord（带 PnLAttribution 自动校验）
try:
    from brahma_v6.dharma2.models import (
        TradeRecord as StrictTradeRecord,
        PnLAttribution,
    )
    _STRICT_MODELS_AVAILABLE = True
except ImportError:
    _STRICT_MODELS_AVAILABLE = False

BASE = Path(__file__).resolve().parents[2]
LEDGER_DIR = BASE / "data" / "dharma2"
LEDGER_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TradeRecord:
    """
    单笔交易完整证据链。
    设计原则：每个字段都可追溯到上游事件。
    """
    # ── 追踪链（全部必填）────────────────────────────────
    trade_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id:     str = ""          # 贯穿全链路
    signal_id:    str = ""          # SignalScoredEvent.event_id
    risk_id:      str = ""          # RiskDecisionEvent.event_id
    intent_id:    str = ""          # OrderIntentEvent.event_id
    ticket_id:    str = ""          # BrahmaOrderTicket.ticket_id
    order_event_ids: List[str] = field(default_factory=list)

    # ── 信号属性 ─────────────────────────────────────────
    symbol:       str = ""
    direction:    str = ""          # LONG / SHORT
    regime:       str = ""
    score:        float = 0.0
    confidence:   float = 0.0
    top_features: List = field(default_factory=list)

    # ── 执行属性 ─────────────────────────────────────────
    entry_price:  float = 0.0
    exit_price:   float = 0.0
    quantity:     float = 0.0
    leverage:     int = 1
    order_type:   str = ""
    execution_mode: str = "paper"   # backtest / paper / live

    # ── PnL 归因（Lean Reality Modeling 思想）────────────
    gross_pnl:    float = 0.0
    fee_drag:     float = 0.0       # 手续费（负数=成本）
    slippage_drag: float = 0.0      # 滑点损失
    spread_drag:  float = 0.0       # 价差成本
    funding_drag: float = 0.0       # 资金费率成本
    impact_drag:  float = 0.0       # 市场冲击
    net_pnl:      float = 0.0       # = gross - all drags

    # ── 执行质量 ─────────────────────────────────────────
    mae:          float = 0.0       # Maximum Adverse Excursion
    mfe:          float = 0.0       # Maximum Favorable Excursion
    holding_hours: float = 0.0
    fill_ratio:   float = 1.0

    # ── 时间 ─────────────────────────────────────────────
    entry_ts:     float = field(default_factory=time.time)
    exit_ts:      float = 0.0
    created_at:   float = field(default_factory=time.time)

    # ── 状态 ─────────────────────────────────────────────
    status:       str = "OPEN"      # OPEN / CLOSED / CANCELLED
    exit_reason:  str = ""          # SL / TP / MANUAL / FORCED

    def close(
        self,
        exit_price: float,
        fee_drag: float,
        slippage_drag: float,
        funding_drag: float,
        spread_drag: float = 0.0,
        impact_drag: float = 0.0,
        exit_reason: str = "MANUAL",
    ) -> None:
        """平仓并计算完整 PnL 归因"""
        self.exit_price = exit_price
        self.exit_ts = time.time()
        self.holding_hours = (self.exit_ts - self.entry_ts) / 3600

        # Gross PnL
        qty_signed = self.quantity if self.direction == "LONG" else -self.quantity
        self.gross_pnl = (exit_price - self.entry_price) * abs(self.quantity)
        if self.direction == "SHORT":
            self.gross_pnl = -self.gross_pnl

        # 成本（均为正数，代表损耗）
        self.fee_drag      = abs(fee_drag)
        self.slippage_drag = abs(slippage_drag)
        self.funding_drag  = abs(funding_drag) if funding_drag > 0 else 0
        self.spread_drag   = abs(spread_drag)
        self.impact_drag   = abs(impact_drag)
        self.net_pnl = self.gross_pnl - self.fee_drag - self.slippage_drag - \
                       self.funding_drag - self.spread_drag - self.impact_drag

        self.status = "CLOSED"
        self.exit_reason = exit_reason

    @property
    def pnl_breakdown(self) -> Dict:
        return {
            "gross_pnl":     round(self.gross_pnl, 6),
            "fee_drag":      round(-self.fee_drag, 6),
            "slippage_drag": round(-self.slippage_drag, 6),
            "spread_drag":   round(-self.spread_drag, 6),
            "funding_drag":  round(-self.funding_drag, 6),
            "impact_drag":   round(-self.impact_drag, 6),
            "net_pnl":       round(self.net_pnl, 6),
            "cost_pct":      round((self.fee_drag + self.slippage_drag +
                                    self.spread_drag + self.funding_drag +
                                    self.impact_drag) / max(abs(self.gross_pnl), 1e-10) * 100, 2),
        }

    def chain_integrity(self) -> Dict:
        """验证追踪链完整性"""
        issues = []
        if not self.trace_id:  issues.append("MISSING_TRACE_ID")
        if not self.signal_id: issues.append("MISSING_SIGNAL_ID")
        if not self.risk_id:   issues.append("MISSING_RISK_ID")
        if not self.intent_id: issues.append("MISSING_INTENT_ID")
        if not self.ticket_id: issues.append("MISSING_TICKET_ID")
        return {"complete": len(issues) == 0, "issues": issues}

    def to_dict(self) -> Dict:
        return asdict(self)

    def summary(self) -> Dict:
        return {
            "trade_id":    self.trade_id[:8],
            "symbol":      self.symbol,
            "direction":   self.direction,
            "regime":      self.regime,
            "score":       round(self.score, 1),
            "entry":       round(self.entry_price, 4),
            "exit":        round(self.exit_price, 4),
            "net_pnl":     round(self.net_pnl, 6),
            "holding_h":   round(self.holding_hours, 2),
            "status":      self.status,
            "exit_reason": self.exit_reason,
            "chain_ok":    self.chain_integrity()["complete"],
        }


class TradeLedger:
    """
    Dharma2 交易账本。
    维护所有交易记录，提供 PnL 归因报告。
    """
    def __init__(self, ledger_file: Path = None):
        self._file = ledger_file or LEDGER_DIR / "trade_ledger.jsonl"
        self._trades: Dict[str, TradeRecord] = {}
        self._load()

    def open_trade(
        self,
        symbol: str,
        direction: str,
        regime: str,
        score: float,
        entry_price: float,
        quantity: float,
        leverage: int = 1,
        execution_mode: str = "paper",
        trace_id: str = "",
        signal_id: str = "",
        risk_id: str = "",
        intent_id: str = "",
        ticket_id: str = "",
    ) -> TradeRecord:
        record = TradeRecord(
            trace_id=trace_id, signal_id=signal_id, risk_id=risk_id,
            intent_id=intent_id, ticket_id=ticket_id,
            symbol=symbol, direction=direction, regime=regime,
            score=score, entry_price=entry_price,
            quantity=quantity, leverage=leverage,
            execution_mode=execution_mode,
        )
        self._trades[record.trade_id] = record
        self._append(record)
        return record

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        fee_drag: float,
        slippage_drag: float,
        funding_drag: float,
        exit_reason: str = "MANUAL",
        spread_drag: float = 0.0,
        impact_drag: float = 0.0,
    ) -> Optional[TradeRecord]:
        record = self._trades.get(trade_id)
        if not record or record.status != "OPEN":
            return None
        record.close(exit_price, fee_drag, slippage_drag, funding_drag,
                     spread_drag, impact_drag, exit_reason)
        self._append(record)
        return record

    def record_fill(self, ticket, order_event) -> Optional[TradeRecord]:
        """从 BrahmaOrderTicket + BrahmaOrderEvent 自动创建交易记录"""
        return self.open_trade(
            symbol=ticket.symbol,
            direction="LONG" if ticket.side == "BUY" else "SHORT",
            regime="UNKNOWN",
            score=0.0,
            entry_price=ticket.avg_fill_price,
            quantity=ticket.filled_qty,
            leverage=ticket.leverage,
            execution_mode=order_event.source,
            trace_id=ticket.trace_id,
            ticket_id=ticket.ticket_id,
        )

    def open_trades(self) -> List[TradeRecord]:
        return [t for t in self._trades.values() if t.status == "OPEN"]

    def closed_trades(self) -> List[TradeRecord]:
        return [t for t in self._trades.values() if t.status == "CLOSED"]

    def pnl_report(self) -> Dict:
        closed = self.closed_trades()
        if not closed:
            return {"total_trades": 0}
        net_pnls = [t.net_pnl for t in closed]
        wins = [p for p in net_pnls if p > 0]
        chain_ok = sum(1 for t in closed if t.chain_integrity()["complete"])
        total_fee = sum(t.fee_drag for t in closed)
        total_slip = sum(t.slippage_drag for t in closed)
        total_fund = sum(t.funding_drag for t in closed)
        return {
            "total_trades":     len(closed),
            "win_rate":         round(len(wins) / len(closed), 3),
            "net_pnl":          round(sum(net_pnls), 4),
            "avg_net_pnl":      round(sum(net_pnls) / len(closed), 6),
            "total_fee_drag":   round(-total_fee, 4),
            "total_slip_drag":  round(-total_slip, 4),
            "total_fund_drag":  round(-total_fund, 4),
            "chain_integrity":  f"{chain_ok}/{len(closed)}",
            "by_regime":        self._breakdown("regime", closed),
            "by_direction":     self._breakdown("direction", closed),
        }

    def _breakdown(self, field_name: str, records: List[TradeRecord]) -> Dict:
        result: Dict[str, Dict] = {}
        for r in records:
            key = getattr(r, field_name, "UNKNOWN")
            if key not in result:
                result[key] = {"n": 0, "wins": 0, "net_pnl": 0.0}
            result[key]["n"] += 1
            if r.net_pnl > 0:
                result[key]["wins"] += 1
            result[key]["net_pnl"] += r.net_pnl
        for k in result:
            n = result[k]["n"]
            result[k]["wr"] = round(result[k]["wins"] / max(n, 1), 3)
            result[k]["net_pnl"] = round(result[k]["net_pnl"], 4)
        return result

    # ── 公开 append：接收 v6 StrictTradeRecord，含二次 PnL 校验 ────────
    def append(self, record: "StrictTradeRecord") -> None:
        """
        Append 强类型 TradeRecord（来自 brahma_v6.dharma2.models）。
        强制执行二次 attribution.validate() 保险：
          - 第一次在 TradeRecord.__post_init__ 已触发
          - 这里再触发一次，防止反序列化/手动构造绕过
        """
        record.attribution.validate()   # 二次保险 — 不可绕过
        try:
            d = {
                "trade_id":   record.trade_id,
                "trace_id":   record.trace_id,
                "signal_id":  record.signal_id,
                "risk_id":    record.risk_id,
                "intent_id":  record.intent_id,
                "ticket_id":  record.ticket_id,
                "order_event_ids": list(record.order_event_ids),
                "symbol":     record.symbol,
                "direction":  record.direction,
                "regime":     record.regime,
                "score":      record.score,
                "entry_price":  record.entry_price,
                "exit_price":   record.exit_price,
                "quantity":     record.quantity,
                "attribution":  {
                    "gross_pnl":     record.attribution.gross_pnl,
                    "fee_drag":      record.attribution.fee_drag,
                    "slippage_drag": record.attribution.slippage_drag,
                    "funding_drag":  record.attribution.funding_drag,
                    "impact_drag":   record.attribution.impact_drag,
                    "net_pnl":       record.attribution.net_pnl,
                },
                "mae":                  record.mae,
                "mfe":                  record.mfe,
                "holding_time_seconds": record.holding_time_seconds,
                "opened_at":  record.opened_at.isoformat(),
                "closed_at":  record.closed_at.isoformat() if record.closed_at else None,
                "created_at": record.created_at.isoformat(),
                "_schema": "dharma2.v6.strict",
            }
            with self._file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        except Exception as exc:
            raise RuntimeError(f"TradeLedger.append persist failed: {exc}") from exc

    def _append(self, record: TradeRecord) -> None:
        """Legacy internal persist for old-style TradeRecord dataclass."""
        try:
            with self._file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            for line in self._file.read_text().splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                tid = d.get("trade_id", "")
                if tid:
                    # 最新状态覆盖（CLOSED 覆盖 OPEN）
                    existing = self._trades.get(tid)
                    if existing is None or existing.status == "OPEN":
                        self._trades[tid] = TradeRecord(**{
                            k: v for k, v in d.items()
                            if k in TradeRecord.__dataclass_fields__
                        })
        except Exception:
            pass


if __name__ == "__main__":
    import tempfile
    print("=== Dharma2 TradeLedger 自检 ===\n")

    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(Path(d) / "test.jsonl")
        trace = str(uuid.uuid4())

        # 开仓
        r1 = ledger.open_trade(
            symbol="BTCUSDT", direction="LONG", regime="BEAR_RECOVERY",
            score=162.0, entry_price=107000.0, quantity=0.001,
            leverage=5, execution_mode="paper",
            trace_id=trace, signal_id=str(uuid.uuid4()),
            risk_id=str(uuid.uuid4()), intent_id=str(uuid.uuid4()),
            ticket_id=str(uuid.uuid4()),
        )
        print(f"开仓: {r1.symbol} {r1.direction} entry={r1.entry_price}")

        # 平仓（含完整成本分解）
        ledger.close_trade(
            r1.trade_id,
            exit_price=108500.0,
            fee_drag=0.085,
            slippage_drag=0.025,
            funding_drag=0.012,
            spread_drag=0.008,
            impact_drag=0.003,
            exit_reason="TP1",
        )
        print(f"平仓: {r1.summary()}")
        print(f"PnL分解: {r1.pnl_breakdown}")
        print(f"追踪链: {r1.chain_integrity()}")

        # 报告
        report = ledger.pnl_report()
        print(f"\nPnL报告: {report}")
        print("\n✅ Dharma2 TradeLedger 自检完成")
