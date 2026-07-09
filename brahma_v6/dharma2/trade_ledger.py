"""
brahma_v6/dharma2/trade_ledger.py
Dharma2 交易证据链账本 — 最小生产版
设计院 · 2026-07-09

append() 调用顺序（不可绕过）：
  1. attribution.validate()      — PnL 数学守恒
  2. _check_chain_integrity()    — 全链路 ID 完整性 + quantity > 0
  3. _check_duplicate()          — 重复 trade_id 保护
  4. _persist()                  — 磁盘写入（失败立即 raise，不污染内存）
  5. self._records.append()      — 内存追加
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from brahma_v6.dharma2.models import TradeRecord


class LedgerWriteError(RuntimeError):
    """Raised when persisting a TradeRecord to disk fails."""


@dataclass
class TradeLedger:
    """
    Dharma2 交易证据链账本。

    使用方式：
        ledger = TradeLedger(storage_path=Path("data/ledger.jsonl"))
        ledger.append(record)   # 三道校验 + 持久化 + 内存追加
    """
    storage_path: Optional[Path] = None
    _records: List[TradeRecord] = field(default_factory=list)
    _id_set:  set               = field(default_factory=set)   # O(1) 去重

    # ── 唯一写入口 ────────────────────────────────────────────────
    def append(self, record: TradeRecord) -> None:
        """
        写入一条 TradeRecord。
        三道前置校验全部通过 + 磁盘落盘成功后才写入内存。
        任一步骤失败均抛异常，内存和文件保持一致。
        """
        # 第一道：PnL 数学守恒
        record.attribution.validate()

        # 第二道：链路完整性 + quantity
        self._check_chain_integrity(record)

        # 第三道：重复 trade_id
        self._check_duplicate(record)

        # 持久化（先落盘，避免内存写入后磁盘失败导致不一致）
        if self.storage_path is not None:
            self._persist(record)

        # 全部通过 → 写入内存
        self._records.append(record)
        self._id_set.add(record.trade_id)

    # ── 校验层 ───────────────────────────────────────────────────
    def _check_chain_integrity(self, record: TradeRecord) -> None:
        required = {
            "trade_id":  record.trade_id,
            "trace_id":  record.trace_id,
            "signal_id": record.signal_id,
            "risk_id":   record.risk_id,
            "intent_id": record.intent_id,
            "ticket_id": record.ticket_id,
            "symbol":    record.symbol,
            "direction": record.direction,
            "regime":    record.regime,
        }
        missing = [k for k, v in required.items() if not v]
        if not record.order_event_ids:
            missing.append("order_event_ids")
        if missing:
            raise ValueError(
                f"TradeRecord chain integrity failed; missing={missing}; "
                f"trade_id={getattr(record, 'trade_id', None)!r}, "
                f"trace_id={getattr(record, 'trace_id', None)!r}, "
                f"symbol={getattr(record, 'symbol', None)!r}"
            )
        if record.quantity <= 0:
            raise ValueError(
                f"TradeRecord quantity must be positive: {record.quantity}"
            )

    def _check_duplicate(self, record: TradeRecord) -> None:
        if record.trade_id in self._id_set:
            raise ValueError(f"Duplicate trade_id: {record.trade_id}")

    # ── 持久化 ───────────────────────────────────────────────────
    def _persist(self, record: TradeRecord) -> None:
        assert self.storage_path is not None
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = asdict(record)
            with self.storage_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
        except Exception as exc:
            raise LedgerWriteError(
                f"Failed to persist TradeRecord {record.trade_id!r}: {exc}"
            ) from exc

    # ── 查询 ─────────────────────────────────────────────────────
    def get_by_trade_id(self, trade_id: str) -> Optional[TradeRecord]:
        return next((r for r in self._records if r.trade_id == trade_id), None)

    def get_by_ticket(self, ticket_id: str) -> Optional[TradeRecord]:
        return next((r for r in self._records if r.ticket_id == ticket_id), None)

    def get_by_symbol(self, symbol: str) -> List[TradeRecord]:
        return [r for r in self._records if r.symbol == symbol]

    def get_by_regime(self, regime: str) -> List[TradeRecord]:
        return [r for r in self._records if r.regime == regime]

    def all(self) -> List[TradeRecord]:
        return list(self._records)

    # ── 统计 ─────────────────────────────────────────────────────
    @property
    def count(self) -> int:
        return len(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def summary(self) -> Dict:
        return {
            "total_trades":       self.count,
            "symbols":            sorted({r.symbol for r in self._records}),
            "total_gross_pnl":    sum(r.attribution.gross_pnl    for r in self._records),
            "total_fee_drag":     sum(r.attribution.fee_drag      for r in self._records),
            "total_slippage_drag":sum(r.attribution.slippage_drag for r in self._records),
            "total_funding_drag": sum(r.attribution.funding_drag  for r in self._records),
            "total_impact_drag":  sum(r.attribution.impact_drag   for r in self._records),
            "total_net_pnl":      sum(r.attribution.net_pnl       for r in self._records),
        }
