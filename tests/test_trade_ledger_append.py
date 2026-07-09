"""
tests/test_trade_ledger_append.py
TradeLedger.append() — 强制 PnL 校验 + 持久化测试
设计院 · 2026-07-09
"""
import sys, os, uuid, tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from brahma_v6.dharma2.models import TradeRecord, PnLAttribution
from brahma_v6.dharma2.trade_ledger import TradeLedger


# ─── helpers ──────────────────────────────────────────────────────────────────

def make_attribution(net_override=None) -> PnLAttribution:
    """Return a balanced PnLAttribution. net_override injects a mismatch."""
    gross   =  1.50
    fee     = -0.05
    slip    = -0.02
    funding = -0.01
    impact  = -0.005
    net     = gross + fee + slip + funding + impact   # 1.415
    return PnLAttribution(
        gross_pnl     = gross,
        fee_drag      = fee,
        slippage_drag = slip,
        funding_drag  = funding,
        impact_drag   = impact,
        net_pnl       = net if net_override is None else net_override,
    )


def make_record(attribution=None) -> TradeRecord:
    now = datetime.now(timezone.utc)
    attr = attribution or make_attribution()
    return TradeRecord(
        trade_id           = str(uuid.uuid4()),
        trace_id           = str(uuid.uuid4()),
        signal_id          = str(uuid.uuid4()),
        risk_id            = str(uuid.uuid4()),
        intent_id          = str(uuid.uuid4()),
        ticket_id          = str(uuid.uuid4()),
        order_event_ids    = [str(uuid.uuid4())],
        symbol             = "BTCUSDT",
        direction          = "LONG",
        regime             = "BEAR_RECOVERY",
        score              = 162.0,
        entry_price        = 107_000.0,
        exit_price         = 108_500.0,
        quantity           = 0.001,
        attribution        = attr,
        mae                = -200.0,
        mfe                =  1600.0,
        holding_time_seconds = 3600,
        opened_at          = now,
        closed_at          = now,
        created_at         = now,
    )


# ─── 1. 正常路径 ────────────────────────────────────────────────────────────────

def test_append_valid_record_persists():
    """append() with a balanced record should write one line to ledger file."""
    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(storage_path=Path(d) / "test.jsonl")
        rec = make_record()
        ledger.append(rec)

        lines = Path(d, "test.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        import json
        row = json.loads(lines[0])
        assert row["trade_id"] == rec.trade_id
        assert "trade_id" in row


def test_append_multiple_records():
    """Multiple appends should produce multiple lines."""
    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(storage_path=Path(d) / "test.jsonl")
        for _ in range(5):
            ledger.append(make_record())
        lines = Path(d, "test.jsonl").read_text().strip().splitlines()
        assert len(lines) == 5


# ─── 2. 二次 validate 拦截（attribution 不平衡） ───────────────────────────────

def test_append_rejects_mismatched_net_pnl():
    """
    append() must raise ValueError when net_pnl doesn't balance,
    even if __post_init__ was somehow bypassed (simulate by object.__setattr__).
    """
    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(Path(d) / "test.jsonl")
        rec = make_record()

        # 模拟绕过 __post_init__ 的恶意篡改
        tampered_attr = PnLAttribution(
            gross_pnl     =  1.50,
            fee_drag      = -0.05,
            slippage_drag = -0.02,
            funding_drag  = -0.01,
            impact_drag   = -0.005,
            net_pnl       =  9999.0,   # 故意不平衡
        )
        # frozen=True → 用 object.__setattr__ 强制注入
        object.__setattr__(rec, 'attribution', tampered_attr)

        with pytest.raises(ValueError, match="PnLAttribution mismatch"):
            ledger.append(rec)

        # 确认文件里没有任何内容（回滚语义）
        f = Path(d, "test.jsonl")
        assert not f.exists() or f.read_text().strip() == ""


# ─── 3. 构造时已校验（__post_init__）────────────────────────────────────────────

def test_construction_rejects_mismatched_attribution():
    """TradeRecord.__post_init__ validates attribution at construction time."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="PnLAttribution mismatch"):
        make_record(attribution=make_attribution(net_override=0.0))


# ─── 4. PnLAttribution.validate() 直接测试 ─────────────────────────────────────

def test_attribution_validate_pass():
    make_attribution().validate()   # must not raise


def test_attribution_validate_fail():
    bad = PnLAttribution(1.0, -0.1, -0.05, 0.0, 0.0, net_pnl=99.0)
    with pytest.raises(ValueError):
        bad.validate()


def test_attribution_validate_precision():
    """Delta within 1e-8 tolerance should pass."""
    attr = PnLAttribution(
        gross_pnl=1.0, fee_drag=-0.1, slippage_drag=0.0,
        funding_drag=0.0, impact_drag=0.0,
        net_pnl=0.9 + 1e-10,   # inside tolerance
    )
    attr.validate()   # must not raise


# ─── 5. append 写入内容校验 ─────────────────────────────────────────────────────

def test_append_serialises_attribution_fields():
    """Persisted JSON must include all attribution sub-fields."""
    import json
    with tempfile.TemporaryDirectory() as d:
        ledger = TradeLedger(storage_path=Path(d) / "test.jsonl")
        rec = make_record()
        ledger.append(rec)
        row = json.loads(Path(d, "test.jsonl").read_text())
        attr = row["attribution"]
        for key in ["gross_pnl", "fee_drag", "slippage_drag",
                    "funding_drag", "impact_drag", "net_pnl"]:
            assert key in attr, f"attribution.{key} missing from persisted row"
