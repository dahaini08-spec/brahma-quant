"""
tests/test_trade_ledger.py
TradeLedger 官方验收测试套件
设计院 · 2026-07-09
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from pathlib import Path

import pytest

from brahma_v6.dharma2.models import PnLAttribution, TradeRecord
from brahma_v6.dharma2.trade_ledger import LedgerWriteError, TradeLedger


# ─── factory ──────────────────────────────────────────────────────────────────

def make_record(**overrides) -> TradeRecord:
    now = datetime.now(timezone.utc)
    attribution = PnLAttribution(
        gross_pnl     = 100.0,
        fee_drag      = -2.0,
        slippage_drag = -3.0,
        funding_drag  = -1.0,
        impact_drag   = -4.0,
        net_pnl       = 90.0,
    )
    base = dict(
        trade_id          = "trade_1",
        trace_id          = "trace_1",
        signal_id         = "sig_1",
        risk_id           = "risk_1",
        intent_id         = "intent_1",
        ticket_id         = "ticket_1",
        order_event_ids   = ["evt_1"],
        symbol            = "BTCUSDT",
        direction         = "LONG",
        regime            = "BULL_TREND",
        score             = 160.0,
        entry_price       = 100_000.0,
        exit_price        = 101_000.0,
        quantity          = 0.01,
        attribution       = attribution,
        mae               = -50.0,
        mfe               = 120.0,
        holding_time_seconds = 3600,
        opened_at         = now,
        closed_at         = now,
        created_at        = now,
    )
    base.update(overrides)
    return TradeRecord(**base)


# ─── 1. 正常写入 ───────────────────────────────────────────────────────────────

def test_append_valid_record():
    ledger = TradeLedger()
    record = make_record()
    ledger.append(record)
    assert ledger.count == 1
    assert ledger.get_by_trade_id("trade_1") is record
    assert ledger.get_by_ticket("ticket_1") is record


def test_append_returns_correct_count():
    ledger = TradeLedger()
    for i in range(5):
        ledger.append(make_record(trade_id=f"t{i}", ticket_id=f"tk{i}"))
    assert ledger.count == 5
    assert len(ledger) == 5


# ─── 2. 重复保护 ───────────────────────────────────────────────────────────────

def test_append_rejects_duplicate_trade_id():
    ledger = TradeLedger()
    record = make_record()
    ledger.append(record)
    with pytest.raises(ValueError, match="Duplicate trade_id"):
        ledger.append(record)
    assert ledger.count == 1


# ─── 3. chain_integrity — 每个必填字段 ────────────────────────────────────────

@pytest.mark.parametrize("fld", [
    "trade_id", "trace_id", "signal_id", "risk_id",
    "intent_id", "ticket_id", "symbol", "direction", "regime",
])
def test_append_rejects_missing_required_chain_field(fld):
    ledger = TradeLedger()
    record = make_record(**{fld: ""})
    with pytest.raises(ValueError, match="chain integrity"):
        ledger.append(record)


def test_append_rejects_missing_order_event_ids():
    ledger = TradeLedger()
    record = make_record(order_event_ids=[])
    with pytest.raises(ValueError, match="order_event_ids"):
        ledger.append(record)


def test_append_rejects_non_positive_quantity():
    ledger = TradeLedger()
    record = make_record(quantity=0)
    with pytest.raises(ValueError, match="quantity"):
        ledger.append(record)


def test_append_rejects_negative_quantity():
    ledger = TradeLedger()
    record = make_record(quantity=-0.01)
    with pytest.raises(ValueError, match="quantity"):
        ledger.append(record)


# ─── 4. PnL 数学校验 ───────────────────────────────────────────────────────────

def test_append_rejects_pnl_mismatch():
    ledger = TradeLedger()
    bad_attr = PnLAttribution(
        gross_pnl=100.0, fee_drag=-2.0, slippage_drag=-3.0,
        funding_drag=-1.0, impact_drag=-4.0,
        net_pnl=999.0,   # 故意不平衡
    )
    with pytest.raises(ValueError):
        make_record(attribution=bad_attr)  # __post_init__ 先拦截


def test_append_rejects_injected_pnl_mismatch():
    """绕过 __post_init__ 强制注入后，append 二次拦截。"""
    ledger = TradeLedger()
    rec = make_record()
    bad_attr = PnLAttribution(
        gross_pnl=100.0, fee_drag=-2.0, slippage_drag=-3.0,
        funding_drag=-1.0, impact_drag=-4.0, net_pnl=999.0,
    )
    object.__setattr__(rec, "attribution", bad_attr)
    with pytest.raises(ValueError, match="PnLAttribution mismatch"):
        ledger.append(rec)
    assert ledger.count == 0


# ─── 5. 持久化 ─────────────────────────────────────────────────────────────────

def test_persist_jsonl(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    ledger = TradeLedger(storage_path=path)
    ledger.append(make_record())
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "trade_1" in content
    assert "BTCUSDT" in content
    assert "net_pnl" in content


def test_persist_multiple_lines(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    ledger = TradeLedger(storage_path=path)
    for i in range(3):
        ledger.append(make_record(trade_id=f"t{i}", ticket_id=f"tk{i}"))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 3


def test_failed_validation_does_not_write_file(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    ledger = TradeLedger(storage_path=path)
    record = make_record(trace_id="")
    with pytest.raises(ValueError):
        ledger.append(record)
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


# ─── 6. 查询 ───────────────────────────────────────────────────────────────────

def test_get_by_trade_id_miss():
    ledger = TradeLedger()
    assert ledger.get_by_trade_id("nonexistent") is None


def test_get_by_symbol():
    ledger = TradeLedger()
    ledger.append(make_record(symbol="BTCUSDT", trade_id="t1", ticket_id="tk1"))
    ledger.append(make_record(symbol="ETHUSDT", trade_id="t2", ticket_id="tk2"))
    assert len(ledger.get_by_symbol("BTCUSDT")) == 1
    assert len(ledger.get_by_symbol("SOLUSDT")) == 0


def test_get_by_regime():
    ledger = TradeLedger()
    ledger.append(make_record(regime="BEAR_RECOVERY", trade_id="t1", ticket_id="tk1"))
    assert len(ledger.get_by_regime("BEAR_RECOVERY")) == 1
    assert len(ledger.get_by_regime("CHOP_MID")) == 0


def test_all_returns_copy():
    ledger = TradeLedger()
    ledger.append(make_record())
    result = ledger.all()
    assert len(result) == 1
    result.clear()
    assert ledger.count == 1  # 内部不受影响


# ─── 7. summary ────────────────────────────────────────────────────────────────

def test_summary():
    ledger = TradeLedger()
    ledger.append(make_record())
    s = ledger.summary()
    assert s["total_trades"] == 1
    assert s["symbols"] == ["BTCUSDT"]
    assert s["total_net_pnl"] == pytest.approx(90.0)


def test_summary_empty():
    s = TradeLedger().summary()
    assert s["total_trades"] == 0
    assert s["total_net_pnl"] == 0.0
