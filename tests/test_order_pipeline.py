"""
tests/test_order_pipeline.py — OrderPipeline integration tests
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import sys
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest
from unittest.mock import MagicMock, patch

from brahma_v6.runtime.signal_consumer import SignalConsumer, RawSignal
from brahma_v6.runtime.order_intent_factory import OrderIntentFactory
from brahma_v6.runtime.order_pipeline import OrderPipeline
from brahma_v6.risk.risk_kernel import RiskKernel, AccountState
from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.adapters.live_binance_adapter import LiveBinanceAdapter, ModePolicyLive, AdapterOrderEvent
from brahma_v6.adapters.binance_filters import SymbolFilters


ETHUSDT_FILTERS = SymbolFilters(
    symbol="ETHUSDT", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001
)


def make_signal(**kwargs):
    defaults = dict(
        symbol="ETHUSDT", side="BUY", score=0.8,
        price=2000.0, quantity=0.01, regime="NEUTRAL",
        ev_bucket_action="ALLOW", order_type="LIMIT",
    )
    defaults.update(kwargs)
    return RawSignal(**defaults)


def make_pipeline(account_state=None, test_order=True):
    ks = KillSwitch()
    mock_client = MagicMock()
    mock_client.new_order_test.return_value = {}
    mock_client.new_order.return_value = {
        "orderId": 99999,
        "clientOrderId": "brahma_test",
        "symbol": "ETHUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "price": "2000.00",
        "origQty": "0.010",
        "executedQty": "0.0",
        "avgPrice": "0.0",
        "status": "NEW",
    }

    adapter = LiveBinanceAdapter(
        client=mock_client,
        filter_provider=lambda s: ETHUSDT_FILTERS,
        mode_policy=ModePolicyLive(),
        kill_switch=ks,
        test_order=test_order,
    )

    rk = RiskKernel(
        kill_switch=ks,
        symbol_allowlist=["ETHUSDT"],
        min_score=0.5,
        min_notional=5.0,
    )

    consumer = SignalConsumer(min_score=0.5, symbol_allowlist=["ETHUSDT"])
    factory = OrderIntentFactory()

    _state = account_state or AccountState()
    pipeline = OrderPipeline(
        signal_consumer=consumer,
        risk_kernel=rk,
        intent_factory=factory,
        adapter=adapter,
        account_state_provider=lambda: _state,
    )
    return pipeline, ks, mock_client


# ── Full pipeline success ──────────────────────────────────────────────────
def test_full_pipeline_success():
    pipeline, ks, client = make_pipeline()
    result = pipeline.process(make_signal())
    assert result.stage == "SUBMITTED"
    assert result.risk_action == "APPROVE"
    client.new_order_test.assert_called_once()


# ── Signal filtered → adapter not called ─────────────────────────────────
def test_low_score_filtered_no_adapter():
    pipeline, ks, client = make_pipeline()
    result = pipeline.process(make_signal(score=0.1))
    assert result.stage == "FILTERED"
    client.new_order_test.assert_not_called()
    client.new_order.assert_not_called()


# ── Blocked regime → adapter not called ───────────────────────────────────
def test_blocked_regime_not_forwarded():
    pipeline, ks, client = make_pipeline()
    result = pipeline.process(make_signal(regime="BEAR_TREND"))
    assert result.stage == "FILTERED"
    client.new_order_test.assert_not_called()


# ── Risk blocks → adapter not called ─────────────────────────────────────
def test_risk_block_no_adapter_call():
    # max_positions=1, account already has 1 position → BLOCK
    state = AccountState(open_positions=1)
    pipeline, ks, client = make_pipeline(account_state=state)
    result = pipeline.process(make_signal())
    assert result.stage == "BLOCKED"
    assert result.risk_action == "BLOCK"
    client.new_order_test.assert_not_called()


# ── Kill switch active → blocked at risk level ────────────────────────────
def test_kill_switch_blocks_pipeline():
    pipeline, ks, client = make_pipeline()
    ks.activate("test_kill")
    result = pipeline.process(make_signal())
    assert result.stage == "BLOCKED"
    client.new_order_test.assert_not_called()


# ── EV block propagates through pipeline ──────────────────────────────────
def test_ev_block_filtered_at_consumer():
    pipeline, ks, client = make_pipeline()
    result = pipeline.process(make_signal(ev_bucket_action="WATCHLIST"))
    # Filtered at consumer level
    assert result.stage == "FILTERED"
    client.new_order_test.assert_not_called()


# ── Real order mode ────────────────────────────────────────────────────────
def test_real_order_mode():
    pipeline, ks, client = make_pipeline(test_order=False)
    result = pipeline.process(make_signal())
    assert result.stage == "SUBMITTED"
    client.new_order.assert_called_once()
    client.new_order_test.assert_not_called()


# ── Trace ID propagates ────────────────────────────────────────────────────
def test_trace_id_set():
    pipeline, ks, client = make_pipeline()
    result = pipeline.process(make_signal())
    assert result.trace_id is not None
    assert len(result.trace_id) > 0
