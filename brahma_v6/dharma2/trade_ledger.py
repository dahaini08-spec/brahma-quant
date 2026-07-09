"""
brahma_v6/dharma2/trade_ledger.py
Dharma2 交易证据链账本 — v6 最终版
设计院 · 2026-07-09

三道前置校验（顺序不可调换）：
  1. attribution.validate()       — PnL 数学守恒
  2. _check_chain_integrity()     — 全链路 ID 完整性
  3. _check_duplicate()           — 重复写入保护
  4. 内存追加 + 持久化
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from brahma_v6.dharma2.models import TradeRecord


# 链路完整性必填字段（任一为空即拒写）
_CHAIN_REQUIRED = (
    "trade_id",
    "trace_id",
    "signal_id",
    "risk_id",
    "intent_id",
    "ticket_id",
    "symbol",
    "direction",
    "regime",
)


@dataclass
class TradeLedger:
    """
    Dharma2 交易证据链账本（硬依赖）。

    append() 调用顺序（不可绕过）：
      attribution.validate()        ← PnL 数学守恒
      _check_chain_integrity()      ← 全链路 ID 完整性
      _check_duplicate()            ← 重复写入保护
      内存追加 + _persist()
    """
    _records: List[TradeRecord] = field(default_factory=list)
    _storage_path: Optional[Path] = None

    # ── 公开写入口（唯一入口，不可绕过）────────────────────────
    def append(self, record: TradeRecord) -> None:
        """
        写入一条 TradeRecord。

        三道前置校验全部通过后才写入内存和磁盘。
        任一失败均抛出 ValueError，文件不产生任何写入。
        """
        # 第一道：PnL 数学校验
        record.attribution.validate()

        # 第二道：链路完整性校验
        self._check_chain_integrity(record)

        # 第三道：重复写入保护
        self._check_duplicate(record)

        # 通过全部校验 → 写入
        self._records.append(record)
        if self._storage_path:
            self._persist(record)

    # ── 校验层 ──────────────────────────────────────────────────
    def _check_chain_integrity(self, record: TradeRecord) -> None:
        """
        验证全链路 ID 完整性。
        任何必填字段为空/None → 拒写并抛出 ValueError。
        """
        missing = [
            name for name in _CHAIN_REQUIRED
            if not getattr(record, name, None)
        ]
        if not record.order_event_ids:
            missing.append("order_event_ids")

        if missing:
            raise ValueError(
                f"TradeRecord chain integrity failed; "
                f"missing={missing}; "
                f"trade_id={record.trade_id!r}, "
                f"trace_id={record.trace_id!r}, "
                f"symbol={record.symbol!r}"
            )

    def _check_duplicate(self, record: TradeRecord) -> None:
        """
        简单重复保护（O(n)，可后续升级为布隆过滤器或 DB 唯一约束）。
        """
        if any(r.trade_id == record.trade_id for r in self._records):
            raise ValueError(
                f"Duplicate trade_id detected: {record.trade_id}"
            )

    # ── 持久化 ───────────────────────────────────────────────────
    def _persist(self, record: TradeRecord) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "trade_id":            record.trade_id,
            "trace_id":            record.trace_id,
            "signal_id":           record.signal_id,
            "risk_id":             record.risk_id,
            "intent_id":           record.intent_id,
            "ticket_id":           record.ticket_id,
            "order_event_ids":     list(record.order_event_ids),
            "symbol":              record.symbol,
            "direction":           record.direction,
            "regime":              record.regime,
            "score":               record.score,
            "entry_price":         record.entry_price,
            "exit_price":          record.exit_price,
            "quantity":            record.quantity,
            "attribution": {
                "gross_pnl":     record.attribution.gross_pnl,
                "fee_drag":      record.attribution.fee_drag,
                "slippage_drag": record.attribution.slippage_drag,
                "funding_drag":  record.attribution.funding_drag,
                "impact_drag":   record.attribution.impact_drag,
                "net_pnl":       record.attribution.net_pnl,
            },
            "mae":                    record.mae,
            "mfe":                    record.mfe,
            "holding_time_seconds":   record.holding_time_seconds,
            "opened_at":  record.opened_at.isoformat(),
            "closed_at":  record.closed_at.isoformat() if record.closed_at else None,
            "created_at": record.created_at.isoformat(),
            "_schema":    "dharma2.v6.strict",
        }
        with self._storage_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── 查询辅助 ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._records)

    def get_by_trade_id(self, trade_id: str) -> Optional[TradeRecord]:
        return next((r for r in self._records if r.trade_id == trade_id), None)

    def get_by_symbol(self, symbol: str) -> List[TradeRecord]:
        return [r for r in self._records if r.symbol == symbol]

    def get_by_regime(self, regime: str) -> List[TradeRecord]:
        return [r for r in self._records if r.regime == regime]

    def summary(self) -> Dict:
        total = len(self._records)
        if total == 0:
            return {"total": 0}
        closed = [r for r in self._records if r.closed_at is not None]
        net_pnls = [r.attribution.net_pnl for r in closed]
        wins = sum(1 for p in net_pnls if p > 0)
        return {
            "total":      total,
            "closed":     len(closed),
            "open":       total - len(closed),
            "win_rate":   round(wins / len(closed), 3) if closed else None,
            "net_pnl":    round(sum(net_pnls), 6) if closed else None,
            "avg_pnl":    round(sum(net_pnls) / len(closed), 6) if closed else None,
        }
