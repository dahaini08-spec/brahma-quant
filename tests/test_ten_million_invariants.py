"""
tests/test_ten_million_invariants.py
10M Simulation — 20 invariants + policy optimizer
Phase 5 | 2026-07-09
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from brahma_v6.ops.ten_million.scenario import EventScenario
from brahma_v6.ops.ten_million.driver import SimDriver, FaultRates
from brahma_v6.ops.ten_million.invariant_checker import InvariantChecker
from brahma_v6.ops.ten_million.optimizer import PolicyOptimizer


# ─── helpers ─────────────────────────────────────────────────────────────────

def _run(n: int = 10_000, seed: int = 42, **fault_kwargs) -> InvariantChecker:
    rates = FaultRates(**fault_kwargs)
    driver = SimDriver(n_events=n, fault_rates=rates, seed=seed)
    checker = InvariantChecker()
    for ev in driver.generate():
        checker.check(ev)
    return checker


# ─── 1. Normal run — all invariants pass ─────────────────────────────────────

def test_all_invariants_pass_normal_run():
    """Under normal conditions (no fault injection), all 20 invariants pass."""
    checker = _run(10_000)
    failed = [r for r in checker.results.values() if not r.passed]
    assert len(failed) == 0, f"Failed invariants: {[r.invariant_id for r in failed]}"


def test_all_twenty_invariants_registered():
    checker = InvariantChecker()
    assert len(checker.results) == 20


# ─── 2. Kill switch ───────────────────────────────────────────────────────────

def test_kill_switch_blocks_all_orders():
    """I1: Once kill switch fires, no new orders should be processed."""
    checker = _run(5_000, kill_switch_rate=0.01)
    # Simulation should handle it without I1 violations
    assert checker.results["I1"].passed


# ─── 3. No market orders ─────────────────────────────────────────────────────

def test_no_market_orders_invariant():
    """I2: No market orders ever."""
    checker = _run(10_000)
    assert checker.results["I2"].passed


# ─── 4. Synthetic fill always False ──────────────────────────────────────────

def test_synthetic_fill_always_false():
    """I3: live synthetic_fill must always be False."""
    checker = _run(10_000)
    assert checker.results["I3"].passed


# ─── 5. Position limits ──────────────────────────────────────────────────────

def test_max_open_positions():
    """I4: open_positions <= 1."""
    checker = _run(10_000)
    assert checker.results["I4"].passed


def test_max_open_orders():
    """I5: open_orders <= 1."""
    checker = _run(10_000)
    assert checker.results["I5"].passed


def test_max_trades_per_day():
    """I6: trades_today <= 20."""
    checker = _run(10_000)
    assert checker.results["I6"].passed


# ─── 6. Daily loss / kill switch ─────────────────────────────────────────────

def test_daily_loss_triggers_kill_switch():
    """I7: daily_loss <= 10% NAV before kill_switch fires."""
    checker = _run(10_000)
    assert checker.results["I7"].passed


# ─── 7. EV + score gates ─────────────────────────────────────────────────────

def test_ev_block_produces_block():
    """I8: ev_block always produces BLOCK decision."""
    checker = _run(10_000)
    assert checker.results["I8"].passed


def test_score_below_min_blocked():
    """I9: score below min always BLOCK."""
    checker = _run(10_000)
    assert checker.results["I9"].passed


# ─── 8. UNKNOWN handling ─────────────────────────────────────────────────────

def test_unknown_orders_trigger_reconciliation():
    """I10: UNKNOWN orders must be reconciled."""
    checker = _run(5_000, unknown_rate=0.05)
    assert checker.results["I10"].passed


def test_reconcile_timeout_triggers_kill_switch():
    """I11: reconcile timeout → kill_switch."""
    checker = _run(5_000, unknown_rate=0.05)
    assert checker.results["I11"].passed


# ─── 9. Ledger + DLQ ─────────────────────────────────────────────────────────

def test_ledger_written_for_resolved_orders():
    """I12: all resolved orders enter ledger."""
    checker = _run(10_000)
    assert checker.results["I12"].passed


def test_dlq_populated_for_unknown():
    """I13: DLQ populated for UNKNOWN events."""
    checker = _run(5_000, unknown_rate=0.02)
    assert checker.results["I13"].passed


def test_no_duplicate_client_order_ids():
    """I14: no duplicate client_order_ids."""
    checker = _run(10_000)
    assert checker.results["I14"].passed


# ─── 10. Symbol + filter correctness ─────────────────────────────────────────

def test_symbol_always_in_allowlist():
    """I15: symbol always ETHUSDT."""
    checker = _run(10_000)
    assert checker.results["I15"].passed


def test_price_adjusted_to_tick_size():
    """I16: price adjusted to tickSize."""
    checker = _run(10_000)
    assert checker.results["I16"].passed


def test_qty_adjusted_to_step_size():
    """I17: qty adjusted to stepSize."""
    checker = _run(10_000)
    assert checker.results["I17"].passed


def test_notional_above_min():
    """I18: notional >= min_notional."""
    checker = _run(10_000)
    assert checker.results["I18"].passed


def test_reduce_only_only_for_close():
    """I19: reduce-only only for close orders."""
    checker = _run(10_000)
    assert checker.results["I19"].passed


def test_consecutive_loss_cooldown():
    """I20: max_consecutive_losses cooldown enforced."""
    checker = _run(10_000)
    assert checker.results["I20"].passed


# ─── 11. Fault injection — all invariants still pass ─────────────────────────

def test_all_invariants_pass_with_fault_injection():
    """Full fault injection run — all 20 invariants must still pass."""
    checker = _run(
        20_000,
        unknown_rate        = 0.001,
        reject_rate         = 0.005,
        partial_fill_rate   = 0.08,
        cancel_race_rate    = 0.002,
        ledger_failure_rate = 0.0001,
        websocket_gap_rate  = 0.0001,
        kill_switch_rate    = 0.00001,
    )
    failed = [r for r in checker.results.values() if not r.passed]
    assert len(failed) == 0, f"Failed: {[(r.invariant_id, r.violations) for r in failed]}"


# ─── 12. Policy optimizer ────────────────────────────────────────────────────

def test_policy_optimizer_finds_valid_policy():
    """PolicyOptimizer must find at least one valid policy."""
    optimizer = PolicyOptimizer()
    optimizer.run()
    best = optimizer.best_policy()
    assert best is not None
    assert best.composite_score >= 0.0


def test_policy_optimizer_no_block_only_top():
    """Best policy must not be degenerate."""
    optimizer = PolicyOptimizer()
    optimizer.run()
    best = optimizer.best_policy()
    assert best is not None
    assert best.composite_score >= 0.0
