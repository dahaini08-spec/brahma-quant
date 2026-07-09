"""
tests/test_binance_order_mapper.py — Order mapper param correctness tests
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import sys
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest
from brahma_v6.adapters.binance_filters import SymbolFilters
from brahma_v6.adapters.binance_order_mapper import map_intent_to_binance_params, generate_client_order_id
from brahma_v6.risk.models import ProposedOrder


ETHUSDT = SymbolFilters(
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
        client_order_id="brahma_test",
        reduce_only=False,
    )
    defaults.update(kwargs)
    return ProposedOrder(**defaults)


def test_basic_limit_params():
    params = map_intent_to_binance_params(make_intent(), ETHUSDT)
    assert params["symbol"] == "ETHUSDT"
    assert params["side"] == "BUY"
    assert params["type"] == "LIMIT"
    assert params["timeInForce"] == "GTC"
    assert "newClientOrderId" in params
    assert params["positionSide"] == "BOTH"


def test_price_adjusted_in_params():
    intent = make_intent(price=2000.1234)
    params = map_intent_to_binance_params(intent, ETHUSDT)
    # tick_size=0.01 → floor to 2000.12
    assert float(params["price"]) == pytest.approx(2000.12, abs=1e-6)


def test_qty_adjusted_in_params():
    intent = make_intent(quantity=0.0123456)
    params = map_intent_to_binance_params(intent, ETHUSDT)
    # step_size=0.001 → floor to 0.012
    assert float(params["quantity"]) == pytest.approx(0.012, abs=1e-6)


def test_limit_maker_no_time_in_force():
    intent = make_intent(order_type="LIMIT_MAKER")
    params = map_intent_to_binance_params(intent, ETHUSDT)
    assert params["type"] == "LIMIT_MAKER"
    assert "timeInForce" not in params


def test_reduce_only_in_params():
    intent = make_intent(reduce_only=True)
    params = map_intent_to_binance_params(intent, ETHUSDT)
    assert params.get("reduceOnly") == "true"


def test_no_reduce_only_for_open():
    intent = make_intent(reduce_only=False, quantity=0.01, price=2000.0)
    params = map_intent_to_binance_params(intent, ETHUSDT)
    assert "reduceOnly" not in params


def test_notional_below_min_raises():
    intent = make_intent(price=1.0, quantity=0.001, reduce_only=False)
    with pytest.raises(ValueError, match="Notional"):
        map_intent_to_binance_params(intent, ETHUSDT)


def test_custom_client_order_id():
    intent = make_intent()
    params = map_intent_to_binance_params(intent, ETHUSDT, client_order_id="my_custom_id")
    assert params["newClientOrderId"] == "my_custom_id"


def test_generate_client_order_id_unique():
    ids = {generate_client_order_id("sig123") for _ in range(20)}
    assert len(ids) == 20  # All unique


def test_client_order_id_max_length():
    coid = generate_client_order_id("a" * 30, prefix="brahma")
    assert len(coid) <= 36
