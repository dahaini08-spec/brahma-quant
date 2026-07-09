"""
tests/test_live_binance_adapter.py — LiveBinanceAdapter tests (10 tests)
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import sys
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest
from unittest.mock import MagicMock, patch

from brahma_v6.adapters.live_binance_adapter import (
    LiveBinanceAdapter, ModePolicyLive, AdapterOrderEvent
)
from brahma_v6.adapters.binance_client import BinanceAPIError
from brahma_v6.adapters.binance_filters import SymbolFilters
from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.risk.models import ProposedOrder


ETHUSDT_FILTERS = SymbolFilters(
    symbol="ETHUSDT",
    tick_size=0.01,
    step_size=0.001,
    min_notional=5.0,
    min_qty=0.001,
)


def make_intent(**kwargs):
    defaults = dict(
        symbol="ETHUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity=0.01,
        price=2000.0,
        client_order_id="test_coid_123",
        reduce_only=False,
    )
    defaults.update(kwargs)
    return ProposedOrder(**defaults)


def make_adapter(test_order=True, mock_client=None):
    ks = KillSwitch()
    client = mock_client or MagicMock()
    client.new_order_test.return_value = {}
    client.new_order.return_value = {
        "orderId": 12345,
        "clientOrderId": "brahma_abc123",
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
        client=client,
        filter_provider=lambda sym: ETHUSDT_FILTERS,
        mode_policy=ModePolicyLive(),
        kill_switch=ks,
        test_order=test_order,
    )
    return adapter, ks, client


# ── Test 1: test_order=True calls test endpoint only ──────────────────────
def test_test_order_true_calls_test_endpoint():
    adapter, ks, client = make_adapter(test_order=True)
    result = adapter.submit(make_intent())
    client.new_order_test.assert_called_once()
    client.new_order.assert_not_called()
    assert result.event_type == "SUBMITTED"
    assert result.synthetic_fill is False


# ── Test 2: test_order=False calls real endpoint ──────────────────────────
def test_test_order_false_calls_real_endpoint():
    adapter, ks, client = make_adapter(test_order=False)
    result = adapter.submit(make_intent())
    client.new_order.assert_called_once()
    client.new_order_test.assert_not_called()
    assert result.event_type == "SUBMITTED"
    assert result.synthetic_fill is False


# ── Test 3: Kill switch active → no exchange call ─────────────────────────
def test_kill_switch_active_no_exchange_call():
    adapter, ks, client = make_adapter()
    ks.activate("test_kill")
    result = adapter.submit(make_intent())
    client.new_order_test.assert_not_called()
    client.new_order.assert_not_called()
    assert result.event_type == "REJECTED"
    assert "kill_switch" in (result.error or "")
    assert result.synthetic_fill is False


# ── Test 4: Price adjusted to tickSize ────────────────────────────────────
def test_price_adjusted_to_tick_size():
    adapter, ks, client = make_adapter(test_order=False)
    # Price with extra decimals that need rounding down
    intent = make_intent(price=2000.123456, quantity=0.01)
    result = adapter.submit(intent)
    call_args = client.new_order.call_args
    params = call_args[0][0]
    price = float(params["price"])
    # Should be floored to tick_size=0.01 → 2000.12
    assert price == pytest.approx(2000.12, abs=0.001)


# ── Test 5: Qty adjusted to stepSize ─────────────────────────────────────
def test_qty_adjusted_to_step_size():
    adapter, ks, client = make_adapter(test_order=False)
    intent = make_intent(price=2000.0, quantity=0.0123456)
    result = adapter.submit(intent)
    call_args = client.new_order.call_args
    params = call_args[0][0]
    qty = float(params["quantity"])
    # Floored to step_size=0.001 → 0.012
    assert qty == pytest.approx(0.012, abs=0.0001)


# ── Test 6: Notional < MIN_NOTIONAL → reject ─────────────────────────────
def test_notional_below_min_rejected():
    adapter, ks, client = make_adapter(test_order=False)
    intent = make_intent(price=1.0, quantity=0.001)  # notional = 0.001 USDT
    result = adapter.submit(intent)
    client.new_order.assert_not_called()
    assert result.event_type == "REJECTED"
    assert result.synthetic_fill is False


# ── Test 7: Client order ID is unique ────────────────────────────────────
def test_client_order_id_unique():
    adapter, ks, client = make_adapter(test_order=False)
    results = []
    for i in range(5):
        result = adapter.submit(make_intent())
        results.append(result)
    # Check all generated IDs are unique
    coids = set()
    for call in client.new_order.call_args_list:
        params = call[0][0]
        coids.add(params.get("newClientOrderId"))
    assert len(coids) == 5


# ── Test 8: API exception → UNKNOWN + no crash ───────────────────────────
def test_api_exception_returns_unknown():
    adapter, ks, client = make_adapter(test_order=False)
    client.new_order.side_effect = BinanceAPIError(code=-1001, message="API error")
    result = adapter.submit(make_intent())
    assert result.event_type == "UNKNOWN"
    assert result.synthetic_fill is False
    assert result.error is not None


# ── Test 9: synthetic_fill always False ───────────────────────────────────
def test_synthetic_fill_always_false():
    adapter, ks, client = make_adapter(test_order=True)
    result = adapter.submit(make_intent())
    assert result.synthetic_fill is False

    # Also test that constructing with synthetic_fill=True raises
    with pytest.raises(ValueError, match="INVARIANT VIOLATED"):
        AdapterOrderEvent(
            event_type="FILLED",
            client_order_id="abc",
            synthetic_fill=True,
        )


# ── Test 10: reduce_only only for close orders ────────────────────────────
def test_reduce_only_only_for_close():
    adapter, ks, client = make_adapter(test_order=False)

    # Close order: reduce_only=True, skip notional check
    close_intent = make_intent(reduce_only=True, price=2000.0, quantity=0.001)
    result = adapter.submit(close_intent)
    call_args = client.new_order.call_args
    params = call_args[0][0]
    assert params.get("reduceOnly") == "true"
    assert result.synthetic_fill is False

    # Regular order: reduce_only=False
    client.reset_mock()
    open_intent = make_intent(reduce_only=False, price=2000.0, quantity=0.01)
    result2 = adapter.submit(open_intent)
    params2 = client.new_order.call_args[0][0]
    assert "reduceOnly" not in params2
