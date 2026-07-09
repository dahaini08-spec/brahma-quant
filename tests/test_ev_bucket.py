"""
tests/test_ev_bucket.py
EV Bucket Governance 全覆盖测试
设计院 · 2026-07-09
"""
from __future__ import annotations
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
import pytest

from brahma_v6.dharma2.models import TradeRecord, PnLAttribution
from brahma_v6.dharma2.ev_bucket import (
    BucketAction, BucketKey, BucketStats, EVBucketRegistry,
    MIN_SAMPLE_FOR_BLOCK,
    WIN_RATE_BLOCK_THRESHOLD, EXPECTANCY_BLOCK_THRESHOLD,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _attr(net: float) -> PnLAttribution:
    fee = -abs(net) * 0.05
    slip = -abs(net) * 0.03
    fund = -abs(net) * 0.01
    impact = -abs(net) * 0.02
    gross = net - fee - slip - fund - impact
    return PnLAttribution(
        gross_pnl=gross, fee_drag=fee, slippage_drag=slip,
        funding_drag=fund, impact_drag=impact, net_pnl=net,
    )


def _rec(net_pnl: float, closed: bool = True, symbol="BTCUSDT",
         direction="LONG", regime="BEAR_RECOVERY", score=155.0) -> TradeRecord:
    now = datetime.now(timezone.utc)
    return TradeRecord(
        trade_id=str(uuid.uuid4()), trace_id=str(uuid.uuid4()),
        signal_id=str(uuid.uuid4()), risk_id=str(uuid.uuid4()),
        intent_id=str(uuid.uuid4()), ticket_id=str(uuid.uuid4()),
        order_event_ids=[str(uuid.uuid4())],
        symbol=symbol, direction=direction, regime=regime, score=score,
        entry_price=100_000.0, exit_price=101_000.0 if closed else None,
        quantity=0.01, attribution=_attr(net_pnl),
        mae=-50.0, mfe=120.0, holding_time_seconds=3600,
        opened_at=now, closed_at=now if closed else None, created_at=now,
    )


# ─── 1. BucketKey ─────────────────────────────────────────────────────────────

def test_bucket_key_score_low():
    r = _rec(10.0, score=120.0)
    key = BucketKey.from_record(r)
    assert key.score_bucket == "LOW"

def test_bucket_key_score_mid():
    r = _rec(10.0, score=140.0)
    assert BucketKey.from_record(r).score_bucket == "MID"

def test_bucket_key_score_high():
    r = _rec(10.0, score=160.0)
    assert BucketKey.from_record(r).score_bucket == "HIGH"


# ─── 2. BucketStats 核心：使用 net_pnl ───────────────────────────────────────

def test_bucket_uses_net_pnl_not_gross():
    """EV Bucket 必须使用 net_pnl，不使用 gross_pnl。"""
    reg = EVBucketRegistry()
    r = _rec(net_pnl=90.0)   # net=90, gross 更大
    key = reg.update(r)
    stats = reg.get(key)
    # net_pnls 存储的是 net_pnl=90，不是 gross_pnl
    assert stats.net_pnls[0] == pytest.approx(90.0)
    assert stats.avg_net_pnl == pytest.approx(90.0)


# ─── 3. 只接受 closed trade ───────────────────────────────────────────────────

def test_bucket_rejects_open_trade():
    """EV Bucket 只能用 closed trade 更新。"""
    reg = EVBucketRegistry()
    r = _rec(net_pnl=50.0, closed=False)
    with pytest.raises(ValueError, match="closed trades"):
        reg.update(r)


def test_batch_update_skips_open_trades():
    reg = EVBucketRegistry()
    records = [_rec(10.0, closed=True), _rec(20.0, closed=False), _rec(30.0, closed=True)]
    accepted, skipped = reg.update_from_ledger(records)
    assert accepted == 2
    assert skipped == 1


# ─── 4. 治理动作阈值 ──────────────────────────────────────────────────────────

def test_allow_when_positive_expectancy():
    reg = EVBucketRegistry()
    for _ in range(MIN_SAMPLE_FOR_BLOCK + 2):
        reg.update(_rec(net_pnl=50.0))
    stats = reg.get(BucketKey.from_record(_rec(50.0)))
    assert stats.governance_action() == BucketAction.ALLOW

def test_block_after_sufficient_samples_low_winrate():
    """n ≥ MIN_SAMPLE_FOR_BLOCK + 胜率 < 阈值 → BLOCK。"""
    reg = EVBucketRegistry()
    losses = int(MIN_SAMPLE_FOR_BLOCK * (1 - WIN_RATE_BLOCK_THRESHOLD + 0.05))
    wins   = MIN_SAMPLE_FOR_BLOCK - losses
    for _ in range(wins):
        reg.update(_rec(net_pnl=10.0))
    for _ in range(losses):
        reg.update(_rec(net_pnl=-50.0))
    key = BucketKey.from_record(_rec(10.0))
    action = reg.get(key).governance_action()
    assert action == BucketAction.BLOCK

def test_low_sample_cannot_block():
    """n < MIN_SAMPLE_FOR_BLOCK → 不得 BLOCK，最多 DOWN_WEIGHT 或 WATCHLIST。"""
    reg = EVBucketRegistry()
    for _ in range(MIN_SAMPLE_FOR_BLOCK - 1):
        reg.update(_rec(net_pnl=-100.0))   # 全负
    key = BucketKey.from_record(_rec(-100.0))
    action = reg.get(key).governance_action()
    assert action != BucketAction.BLOCK

def test_down_weight_medium_win_rate():
    """胜率 < DOWN_WEIGHT 阈值但样本不足 BLOCK → DOWN_WEIGHT。"""
    reg = EVBucketRegistry()
    # 5 胜 8 负（样本 13，胜率 ~38% < 45%）
    for _ in range(5):
        reg.update(_rec(net_pnl=10.0))
    for _ in range(8):
        reg.update(_rec(net_pnl=-10.0))
    key = BucketKey.from_record(_rec(10.0))
    action = reg.get(key).governance_action()
    assert action in (BucketAction.DOWN_WEIGHT, BucketAction.BLOCK)

def test_unknown_bucket_defaults_allow():
    reg = EVBucketRegistry()
    key = BucketKey("ETHUSDT", "SHORT", "BULL_TREND", "HIGH", "OB", "4H")
    assert reg.action_for(key) == BucketAction.ALLOW

def test_negative_expectancy_triggers_block_with_enough_samples():
    """期望值 < EXPECTANCY_BLOCK_THRESHOLD + 样本充足 → BLOCK。"""
    reg = EVBucketRegistry()
    for _ in range(MIN_SAMPLE_FOR_BLOCK + 2):
        reg.update(_rec(net_pnl=-100.0))
    key = BucketKey.from_record(_rec(-100.0))
    assert reg.get(key).governance_action() == BucketAction.BLOCK


# ─── 5. 统计指标正确性 ────────────────────────────────────────────────────────

def test_win_rate_calculation():
    reg = EVBucketRegistry()
    for _ in range(7):
        reg.update(_rec(net_pnl=10.0))
    for _ in range(3):
        reg.update(_rec(net_pnl=-10.0))
    key = BucketKey.from_record(_rec(10.0))
    assert reg.get(key).win_rate == pytest.approx(0.7)

def test_avg_net_pnl():
    reg = EVBucketRegistry()
    reg.update(_rec(net_pnl=100.0))
    reg.update(_rec(net_pnl=200.0))
    key = BucketKey.from_record(_rec(100.0))
    assert reg.get(key).avg_net_pnl == pytest.approx(150.0)

def test_empty_bucket_stats():
    stats = BucketStats(key=BucketKey("X", "LONG", "BEAR", "HIGH", "OB", "1H"))
    assert stats.n == 0
    assert stats.win_rate is None
    assert stats.avg_net_pnl is None
    assert stats.governance_action() == BucketAction.ALLOW


# ─── 6. registry 查询 ─────────────────────────────────────────────────────────

def test_summary():
    reg = EVBucketRegistry()
    for _ in range(3):
        reg.update(_rec(net_pnl=10.0))
    s = reg.summary()
    assert s["total_buckets"] == 1
    assert "ALLOW" in s["actions"]

def test_all_stats_returns_list():
    reg = EVBucketRegistry()
    reg.update(_rec(net_pnl=10.0, symbol="BTCUSDT"))
    reg.update(_rec(net_pnl=10.0, symbol="ETHUSDT"))
    assert len(reg.all_stats()) == 2

def test_blocked_buckets_filter():
    reg = EVBucketRegistry()
    # 写入足量负样本触发 BLOCK
    for _ in range(MIN_SAMPLE_FOR_BLOCK + 2):
        reg.update(_rec(net_pnl=-200.0))
    assert len(reg.blocked_buckets()) >= 1
