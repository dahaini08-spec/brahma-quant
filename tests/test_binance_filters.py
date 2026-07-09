"""
tests/test_binance_filters.py — SymbolFilters price/qty rounding and min_notional checks
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import sys
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest
from brahma_v6.adapters.binance_filters import SymbolFilters, _floor_to_step


# ── Basic rounding ─────────────────────────────────────────────────────────
def test_price_rounded_to_tick():
    f = SymbolFilters(symbol="ETHUSDT", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001)
    assert f.adjust_price(2000.129) == pytest.approx(2000.12, abs=1e-6)
    assert f.adjust_price(2000.1) == pytest.approx(2000.1, abs=1e-6)
    assert f.adjust_price(2000.0) == pytest.approx(2000.0, abs=1e-6)


def test_qty_rounded_to_step():
    f = SymbolFilters(symbol="ETHUSDT", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001)
    assert f.adjust_qty(0.1239) == pytest.approx(0.123, abs=1e-6)
    assert f.adjust_qty(0.001) == pytest.approx(0.001, abs=1e-6)
    assert f.adjust_qty(1.0005) == pytest.approx(1.0, abs=1e-6)


def test_floor_does_not_round_up():
    f = SymbolFilters(symbol="X", tick_size=0.01, step_size=0.01, min_notional=1.0, min_qty=0.01)
    # Should floor, not round
    assert f.adjust_price(1.999) == pytest.approx(1.99, abs=1e-6)
    assert f.adjust_qty(0.999) == pytest.approx(0.99, abs=1e-6)


# ── Min notional checks ────────────────────────────────────────────────────
def test_check_notional_above_min():
    f = SymbolFilters(symbol="X", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001)
    assert f.check_notional(price=2000.0, qty=0.01) is True   # 20 USDT


def test_check_notional_below_min():
    f = SymbolFilters(symbol="X", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001)
    assert f.check_notional(price=1.0, qty=0.001) is False   # 0.001 USDT


def test_check_notional_exactly_min():
    f = SymbolFilters(symbol="X", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001)
    assert f.check_notional(price=5000.0, qty=0.001) is True  # exactly 5.0


# ── Large/integer tick sizes ───────────────────────────────────────────────
def test_price_integer_tick():
    f = SymbolFilters(symbol="BTC", tick_size=1.0, step_size=0.001, min_notional=100.0, min_qty=0.001)
    assert f.adjust_price(29999.9) == pytest.approx(29999.0, abs=1e-6)
    assert f.adjust_price(30000.0) == pytest.approx(30000.0, abs=1e-6)


def test_from_exchange_info_parsing():
    filters = [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
    ]
    f = SymbolFilters.from_exchange_info("ETHUSDT", filters)
    assert f.tick_size == pytest.approx(0.01)
    assert f.step_size == pytest.approx(0.001)
    assert f.min_notional == pytest.approx(5.0)
    assert f.symbol == "ETHUSDT"


def test_ethusdt_default():
    f = SymbolFilters.ethusdt_default()
    assert f.symbol == "ETHUSDT"
    assert f.tick_size == pytest.approx(0.01)
    assert f.step_size == pytest.approx(0.001)
