"""
tests/test_reconciler.py — Reconciler tests
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest
from unittest.mock import MagicMock

from brahma_v6.ops.reconciler import Reconciler, UnknownOrderTicket, TradeLedger
from brahma_v6.ops.dlq import DeadLetterQueue
from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.adapters.live_binance_adapter import AdapterOrderEvent


def make_event(status="FILLED", client_order_id="coid123"):
    return AdapterOrderEvent(
        event_type="FILLED" if status == "FILLED" else "SUBMITTED",
        client_order_id=client_order_id,
        symbol="ETHUSDT",
        status=status,
        executed_qty=0.01,
        avg_price=2000.0,
        synthetic_fill=False,
    )


# ── Timeout triggers kill switch ──────────────────────────────────────────
def test_timeout_triggers_kill_switch():
    ks = KillSwitch()
    dlq = DeadLetterQueue(persist=False)
    reconciler = Reconciler(kill_switch=ks, dlq=dlq, timeout_seconds=30.0)

    # Old ticket: created 60s ago
    ticket = UnknownOrderTicket(
        client_order_id="coid_old",
        symbol="ETHUSDT",
        created_at=time.time() - 60,
    )

    mock_adapter = MagicMock()
    results = reconciler.reconcile_unknown_orders([ticket], mock_adapter)

    assert ks.is_active()
    assert "reconcile_timeout" in ks.reason
    assert results["coid_old"] == "TIMEOUT_KILL_SWITCH"
    assert dlq.count() == 1


# ── Successful resolution writes to ledger ────────────────────────────────
def test_successful_resolution_writes_ledger():
    ks = KillSwitch()
    ledger = TradeLedger()
    dlq = DeadLetterQueue(persist=False)
    reconciler = Reconciler(kill_switch=ks, ledger=ledger, dlq=dlq, timeout_seconds=30.0)

    ticket = UnknownOrderTicket(
        client_order_id="coid_ok",
        symbol="ETHUSDT",
        created_at=time.time() - 5,  # 5s ago, within timeout
    )

    mock_adapter = MagicMock()
    mock_adapter.query_order.return_value = make_event(status="FILLED", client_order_id="coid_ok")

    results = reconciler.reconcile_unknown_orders([ticket], mock_adapter)

    assert not ks.is_active()
    assert results["coid_ok"] == "FILLED"
    assert ticket.resolved is True
    entries = ledger.entries()
    assert len(entries) == 1
    assert entries[0]["client_order_id"] == "coid_ok"
    assert entries[0]["status"] == "FILLED"


# ── Already-resolved ticket is skipped ────────────────────────────────────
def test_already_resolved_skipped():
    ks = KillSwitch()
    reconciler = Reconciler(kill_switch=ks, timeout_seconds=30.0)

    ticket = UnknownOrderTicket(
        client_order_id="coid_resolved",
        symbol="ETHUSDT",
        resolved=True,
        resolution="FILLED",
    )

    mock_adapter = MagicMock()
    results = reconciler.reconcile_unknown_orders([ticket], mock_adapter)
    mock_adapter.query_order.assert_not_called()
    assert results["coid_resolved"] == "FILLED"


# ── Query failure triggers kill switch ────────────────────────────────────
def test_query_failure_triggers_kill_switch():
    ks = KillSwitch()
    dlq = DeadLetterQueue(persist=False)
    reconciler = Reconciler(kill_switch=ks, dlq=dlq, timeout_seconds=30.0)

    ticket = UnknownOrderTicket(
        client_order_id="coid_err",
        symbol="ETHUSDT",
        created_at=time.time() - 5,
    )

    mock_adapter = MagicMock()
    mock_adapter.query_order.side_effect = RuntimeError("network error")

    results = reconciler.reconcile_unknown_orders([ticket], mock_adapter)

    assert ks.is_active()
    assert "reconcile_failure" in ks.reason
    assert results["coid_err"] == "RECONCILE_FAILURE"
    assert dlq.count() == 1
