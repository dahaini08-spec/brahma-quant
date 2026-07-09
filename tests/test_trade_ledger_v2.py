"""
tests/test_trade_ledger_v2.py
TradeLedger 三道校验完整测试 — v2
设计院 · 2026-07-09

覆盖：
  - 正常写入路径
  - PnL 不平衡拦截（第一道）
  - chain_integrity 每个必填字段单独缺失（第二道）
  - order_event_ids 为空拦截
  - 重复 trade_id 拦截（第三道）
  - 文件持久化内容校验
  - 查询辅助方法
  - summary 正确性
"""
import json
import sys
import os
import uuid
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from brahma_v6.dharma2.models import TradeRecord, PnLAttribution
from brahma_v6.dharma2.trade_ledger import TradeLedger


# ─── helpers ──────────────────────────────────────────────────────────────────

def _attr(net_override=None) -> PnLAttribution:
    g, f, s, fu, i = 1.50, -0.05, -0.02, -0.01, -0.005
    net = g + f + s + fu + i
    return PnLAttribution(
        gross_pnl=g, fee_drag=f, slippage_drag=s,
        funding_drag=fu, impact_drag=i,
        net_pnl=net if net_override is None else net_override,
    )


def _rec(**overrides) -> TradeRecord:
    now = datetime.now(timezone.utc)
    defaults = dict(
        trade_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        signal_id=str(uuid.uuid4()),
        risk_id=str(uuid.uuid4()),
        intent_id=str(uuid.uuid4()),
        ticket_id=str(uuid.uuid4()),
        order_event_ids=[str(uuid.uuid4())],
        symbol="BTCUSDT",
        direction="LONG",
        regime="BEAR_RECOVERY",
        score=162.0,
        entry_price=107_000.0,
        exit_price=108_500.0,
        quantity=0.001,
        attribution=_attr(),
        mae=-200.0, mfe=1600.0,
        holding_time_seconds=3600,
        opened_at=now, closed_at=now, created_at=now,
    )
    # frozen dataclass → build fresh with merged fields
    merged = {**defaults, **overrides}
    return TradeRecord(**merged)


# ─── 1. 正常路径 ───────────────────────────────────────────────────────────────

def test_append_valid_in_memory():
    ledger = TradeLedger()
    ledger.append(_rec())
    assert len(ledger) == 1


def test_append_valid_persists_to_file():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        ledger = TradeLedger(_storage_path=path)
        r = _rec()
        ledger.append(r)
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["trade_id"] == r.trade_id
        assert row["_schema"] == "dharma2.v6.strict"


def test_append_five_records():
    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(_storage_path=Path(d) / "l.jsonl")
        for _ in range(5):
            ledger.append(_rec())
        assert len(ledger) == 5
        assert len(Path(d, "l.jsonl").read_text().splitlines()) == 5


# ─── 2. 第一道：PnL 数学校验 ──────────────────────────────────────────────────

def test_pnl_mismatch_raises_before_write():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "l.jsonl"
        ledger = TradeLedger(_storage_path=path)
        r = _rec()
        # 强制注入不平衡 attribution（绕过 frozen __post_init__）
        bad_attr = PnLAttribution(1.5, -0.05, -0.02, -0.01, -0.005, net_pnl=9999.0)
        object.__setattr__(r, "attribution", bad_attr)
        with pytest.raises(ValueError, match="PnLAttribution mismatch"):
            ledger.append(r)
        assert len(ledger) == 0
        assert not path.exists() or path.read_text().strip() == ""


# ─── 3. 第二道：chain_integrity 每字段单独缺失 ────────────────────────────────

CHAIN_FIELDS = [
    "trade_id", "trace_id", "signal_id", "risk_id",
    "intent_id", "ticket_id", "symbol", "direction", "regime",
]


@pytest.mark.parametrize("field_name", CHAIN_FIELDS)
def test_chain_integrity_missing_field(field_name):
    """Each required chain field missing alone must raise ValueError."""
    r = _rec(**{field_name: ""})
    ledger = TradeLedger()
    with pytest.raises(ValueError, match="chain integrity failed"):
        ledger.append(r)
    assert len(ledger) == 0


def test_chain_integrity_empty_order_event_ids():
    r = _rec(order_event_ids=[])
    ledger = TradeLedger()
    with pytest.raises(ValueError, match="chain integrity failed"):
        ledger.append(r)


def test_chain_integrity_none_trace_id():
    r = _rec(trace_id=None)
    ledger = TradeLedger()
    with pytest.raises(ValueError, match="chain integrity failed"):
        ledger.append(r)


# ─── 4. 第三道：重复 trade_id 保护 ────────────────────────────────────────────

def test_duplicate_trade_id_raises():
    ledger = TradeLedger()
    r = _rec()
    ledger.append(r)
    with pytest.raises(ValueError, match="Duplicate trade_id"):
        ledger.append(r)
    assert len(ledger) == 1  # 第二次没写入


def test_different_trade_ids_accepted():
    ledger = TradeLedger()
    ledger.append(_rec())
    ledger.append(_rec())
    assert len(ledger) == 2


# ─── 5. 持久化内容完整性 ──────────────────────────────────────────────────────

def test_persisted_attribution_fields():
    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(_storage_path=Path(d) / "l.jsonl")
        ledger.append(_rec())
        row = json.loads(Path(d, "l.jsonl").read_text())
        attr = row["attribution"]
        for key in ["gross_pnl", "fee_drag", "slippage_drag",
                    "funding_drag", "impact_drag", "net_pnl"]:
            assert key in attr


def test_persisted_chain_ids_present():
    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(_storage_path=Path(d) / "l.jsonl")
        r = _rec()
        ledger.append(r)
        row = json.loads(Path(d, "l.jsonl").read_text())
        for fld in ["trade_id", "trace_id", "signal_id",
                    "risk_id", "intent_id", "ticket_id"]:
            assert row[fld] == getattr(r, fld)


# ─── 6. 查询辅助 ───────────────────────────────────────────────────────────────

def test_get_by_trade_id():
    ledger = TradeLedger()
    r = _rec()
    ledger.append(r)
    assert ledger.get_by_trade_id(r.trade_id) is r
    assert ledger.get_by_trade_id("nonexistent") is None


def test_get_by_symbol():
    ledger = TradeLedger()
    ledger.append(_rec(symbol="BTCUSDT"))
    ledger.append(_rec(symbol="ETHUSDT"))
    assert len(ledger.get_by_symbol("BTCUSDT")) == 1
    assert len(ledger.get_by_symbol("ETHUSDT")) == 1
    assert len(ledger.get_by_symbol("SOLUSDT")) == 0


def test_get_by_regime():
    ledger = TradeLedger()
    ledger.append(_rec(regime="BEAR_RECOVERY"))
    ledger.append(_rec(regime="CHOP_MID"))
    assert len(ledger.get_by_regime("BEAR_RECOVERY")) == 1
    assert len(ledger.get_by_regime("CHOP_MID")) == 1


# ─── 7. summary ───────────────────────────────────────────────────────────────

def test_summary_empty():
    assert TradeLedger().summary() == {"total": 0}


def test_summary_counts():
    ledger = TradeLedger()
    for _ in range(3):
        ledger.append(_rec())
    s = ledger.summary()
    assert s["total"] == 3
    assert s["closed"] == 3   # all have closed_at set by _rec()
    assert s["open"] == 0
    assert s["win_rate"] is not None
