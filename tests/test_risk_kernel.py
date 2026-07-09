"""
tests/test_risk_kernel.py — RiskKernel unit tests (>= 15 tests)
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import sys
import os
import time
from pathlib import Path

# Path setup
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest
from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.risk.risk_kernel import RiskKernel, Signal, AccountState
from brahma_v6.risk.models import RiskAction


def make_kernel(**kwargs):
    ks = KillSwitch()
    params = dict(
        symbol_allowlist=["ETHUSDT"],
        max_open_positions=1,
        max_open_orders=1,
        max_trades_per_day=20,
        max_daily_loss_pct=0.10,
        min_score=0.5,
        min_notional=5.0,
        cooldown_after_loss_minutes=20.0,
        cooldown_after_reject_minutes=10.0,
        max_consecutive_losses=3,
    )
    params.update(kwargs)
    return ks, RiskKernel(kill_switch=ks, **params)


def make_signal(**kwargs):
    defaults = dict(
        symbol="ETHUSDT",
        side="BUY",
        score=0.8,
        order_type="LIMIT",
        price=2000.0,
        quantity=0.1,
        ev_bucket_action="ALLOW",
    )
    defaults.update(kwargs)
    return Signal(**defaults)


def make_account(**kwargs):
    defaults = dict(
        nav=10000.0,
        daily_pnl=0.0,
        open_positions=0,
        open_orders=0,
        trades_today=0,
        consecutive_losses=0,
        leverage=5,
    )
    defaults.update(kwargs)
    return AccountState(**defaults)


# ── Test 1: Kill switch blocks all ───────────────────────────────────────
def test_kill_switch_blocks_all():
    ks, kernel = make_kernel()
    ks.activate("test_reason")
    decision = kernel.evaluate(make_signal(), make_account())
    assert decision.action == RiskAction.BLOCK
    assert "kill_switch" in decision.reason


# ── Test 2: Daily loss 10% activates kill switch ──────────────────────────
def test_daily_loss_activates_kill_switch():
    ks, kernel = make_kernel()
    account = make_account(nav=10000.0, daily_pnl=-1000.0)  # exactly 10%
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.BLOCK
    assert ks.is_active()
    assert "daily_loss" in ks.reason


def test_daily_loss_just_under_limit_ok():
    ks, kernel = make_kernel()
    account = make_account(nav=10000.0, daily_pnl=-999.0)  # 9.99%
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.APPROVE


def test_daily_loss_over_limit_blocks():
    ks, kernel = make_kernel()
    account = make_account(nav=10000.0, daily_pnl=-1100.0)  # 11%
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.BLOCK
    assert ks.is_active()


# ── Test 3: Max positions blocks ──────────────────────────────────────────
def test_max_positions_blocks():
    ks, kernel = make_kernel()
    account = make_account(open_positions=1)
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.BLOCK
    assert "open_positions" in decision.reason


# ── Test 4: Max orders blocks ─────────────────────────────────────────────
def test_max_orders_blocks():
    ks, kernel = make_kernel()
    account = make_account(open_orders=1)
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.BLOCK
    assert "open_orders" in decision.reason


# ── Test 5: Max trades blocks ─────────────────────────────────────────────
def test_max_trades_blocks():
    ks, kernel = make_kernel()
    account = make_account(trades_today=20)
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.BLOCK
    assert "trades_today" in decision.reason


# ── Test 6: EV block propagates ───────────────────────────────────────────
def test_ev_watchlist_blocks():
    ks, kernel = make_kernel()
    signal = make_signal(ev_bucket_action="WATCHLIST")
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.BLOCK
    assert "ev_bucket_action" in decision.reason


def test_ev_block_blocks():
    ks, kernel = make_kernel()
    signal = make_signal(ev_bucket_action="BLOCK")
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.BLOCK


def test_ev_allow_passes():
    ks, kernel = make_kernel()
    signal = make_signal(ev_bucket_action="ALLOW")
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.APPROVE


# ── Test 7: Score gate ────────────────────────────────────────────────────
def test_score_below_min_blocks():
    ks, kernel = make_kernel(min_score=0.7)
    signal = make_signal(score=0.65)
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.BLOCK
    assert "score" in decision.reason


def test_score_at_threshold_passes():
    ks, kernel = make_kernel(min_score=0.7)
    signal = make_signal(score=0.7)
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.APPROVE


# ── Test 8: Symbol allowlist ──────────────────────────────────────────────
def test_symbol_not_in_allowlist_blocks():
    ks, kernel = make_kernel(symbol_allowlist=["ETHUSDT"])
    signal = make_signal(symbol="BTCUSDT")
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.BLOCK
    assert "allowlist" in decision.reason


def test_symbol_in_allowlist_passes():
    ks, kernel = make_kernel(symbol_allowlist=["ETHUSDT", "BTCUSDT"])
    signal = make_signal(symbol="BTCUSDT")
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.APPROVE


# ── Test 9: APPROVE path ──────────────────────────────────────────────────
def test_approve_path_returns_proposed_order():
    ks, kernel = make_kernel()
    signal = make_signal(symbol="ETHUSDT", side="BUY", price=2000.0, quantity=0.1)
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.APPROVE
    assert decision.proposed_order is not None
    assert decision.proposed_order.symbol == "ETHUSDT"
    assert decision.proposed_order.side == "BUY"
    assert decision.proposed_order.price == 2000.0
    assert decision.proposed_order.quantity == 0.1


# ── Test 10: Consecutive loss cooldown ───────────────────────────────────
def test_consecutive_loss_cooldown_blocks():
    ks, kernel = make_kernel(max_consecutive_losses=3, cooldown_after_loss_minutes=20.0)
    account = make_account(
        consecutive_losses=3,
        last_loss_ts=time.time() - 5 * 60,  # 5 min ago, cooldown = 20 min
    )
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.BLOCK
    assert "cooldown" in decision.reason


def test_consecutive_loss_cooldown_expired_passes():
    ks, kernel = make_kernel(max_consecutive_losses=3, cooldown_after_loss_minutes=1.0)
    account = make_account(
        consecutive_losses=3,
        last_loss_ts=time.time() - 90,  # 90s ago, cooldown = 60s
    )
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.APPROVE


# ── Test 11: Notional below min blocks ───────────────────────────────────
def test_notional_below_min_blocks():
    ks, kernel = make_kernel(min_notional=10.0)
    signal = make_signal(price=1.0, quantity=0.001)  # notional = 0.001 USDT
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.BLOCK
    assert "notional" in decision.reason


# ── Test 12: Market order blocked ────────────────────────────────────────
def test_market_order_blocked():
    ks, kernel = make_kernel()
    signal = make_signal(order_type="MARKET")
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.BLOCK
    assert "order_type" in decision.reason


# ── Test 13: LIMIT_MAKER approved ────────────────────────────────────────
def test_limit_maker_approved():
    ks, kernel = make_kernel()
    signal = make_signal(order_type="LIMIT_MAKER")
    decision = kernel.evaluate(signal, make_account())
    assert decision.action == RiskAction.APPROVE


# ── Test 14: Kill switch persists after reset and re-activate ────────────
def test_kill_switch_reset_and_reactivate():
    ks, kernel = make_kernel()
    ks.activate("first_reason")
    assert ks.is_active()
    ks.reset()
    assert not ks.is_active()
    # Now should work normally
    decision = kernel.evaluate(make_signal(), make_account())
    assert decision.action == RiskAction.APPROVE


# ── Test 15: Reject cooldown blocks ──────────────────────────────────────
def test_reject_cooldown_blocks():
    ks, kernel = make_kernel(cooldown_after_reject_minutes=10.0)
    account = make_account(
        last_reject_ts=time.time() - 60,  # 1 min ago, cooldown = 10 min
    )
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.BLOCK
    assert "reject_cooldown" in decision.reason


def test_reject_cooldown_expired_passes():
    ks, kernel = make_kernel(cooldown_after_reject_minutes=1.0)
    account = make_account(
        last_reject_ts=time.time() - 90,  # 90s ago, cooldown = 60s
    )
    decision = kernel.evaluate(make_signal(), account)
    assert decision.action == RiskAction.APPROVE
