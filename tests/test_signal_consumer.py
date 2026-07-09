"""
tests/test_signal_consumer.py — SignalConsumer tests
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import sys
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest
from brahma_v6.runtime.signal_consumer import SignalConsumer, RawSignal


def make_signal(**kwargs):
    defaults = dict(
        symbol="ETHUSDT",
        side="BUY",
        score=0.8,
        price=2000.0,
        quantity=0.01,
        regime="NEUTRAL",
        ev_bucket_action="ALLOW",
        order_type="LIMIT",
    )
    defaults.update(kwargs)
    return RawSignal(**defaults)


def make_consumer(**kwargs):
    defaults = dict(
        min_score=0.5,
        symbol_allowlist=["ETHUSDT"],
    )
    defaults.update(kwargs)
    return SignalConsumer(**defaults)


# ── Score gate ─────────────────────────────────────────────────────────────
def test_below_score_filtered():
    consumer = make_consumer(min_score=0.7)
    result = consumer.consume(make_signal(score=0.6))
    assert result is None
    assert consumer.stats["filtered"] == 1


def test_at_score_threshold_passes():
    consumer = make_consumer(min_score=0.5)
    result = consumer.consume(make_signal(score=0.5))
    assert result is not None


def test_above_score_passes():
    consumer = make_consumer(min_score=0.5)
    result = consumer.consume(make_signal(score=0.9))
    assert result is not None


# ── Regime gate ────────────────────────────────────────────────────────────
def test_blocked_regime_filtered():
    consumer = make_consumer()
    result = consumer.consume(make_signal(regime="BEAR_TREND"))
    assert result is None
    assert consumer.stats["filtered"] == 1


def test_unknown_broken_regime_filtered():
    consumer = make_consumer()
    result = consumer.consume(make_signal(regime="UNKNOWN_BROKEN"))
    assert result is None


def test_neutral_regime_passes():
    consumer = make_consumer()
    result = consumer.consume(make_signal(regime="NEUTRAL"))
    assert result is not None


def test_bull_trend_passes():
    consumer = make_consumer()
    result = consumer.consume(make_signal(regime="BULL_TREND"))
    assert result is not None


# ── EV gate ────────────────────────────────────────────────────────────────
def test_ev_watchlist_filtered():
    consumer = make_consumer()
    result = consumer.consume(make_signal(ev_bucket_action="WATCHLIST"))
    assert result is None


def test_ev_block_filtered():
    consumer = make_consumer()
    result = consumer.consume(make_signal(ev_bucket_action="BLOCK"))
    assert result is None


def test_ev_allow_passes():
    consumer = make_consumer()
    result = consumer.consume(make_signal(ev_bucket_action="ALLOW"))
    assert result is not None


# ── Symbol allowlist ────────────────────────────────────────────────────────
def test_symbol_not_allowed_filtered():
    consumer = make_consumer(symbol_allowlist=["ETHUSDT"])
    result = consumer.consume(make_signal(symbol="BTCUSDT"))
    assert result is None


def test_symbol_allowed_passes():
    consumer = make_consumer(symbol_allowlist=["ETHUSDT"])
    result = consumer.consume(make_signal(symbol="ETHUSDT"))
    assert result is not None


# ── Callback ───────────────────────────────────────────────────────────────
def test_callback_called_on_forward():
    received = []
    consumer = make_consumer(on_intent=received.append)
    consumer.consume(make_signal())
    assert len(received) == 1
    assert received[0].symbol == "ETHUSDT"


def test_callback_not_called_on_filter():
    received = []
    consumer = make_consumer(min_score=0.9, on_intent=received.append)
    consumer.consume(make_signal(score=0.5))
    assert len(received) == 0


# ── Stats tracking ─────────────────────────────────────────────────────────
def test_stats_tracking():
    consumer = make_consumer(min_score=0.7)
    consumer.consume(make_signal(score=0.5))  # filtered
    consumer.consume(make_signal(score=0.8))  # forwarded
    consumer.consume(make_signal(score=0.9))  # forwarded
    assert consumer.stats["received"] == 3
    assert consumer.stats["filtered"] == 1
    assert consumer.stats["forwarded"] == 2
