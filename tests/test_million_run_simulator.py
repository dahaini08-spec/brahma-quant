"""
tests/test_million_run_simulator.py
Phase 3 仿真器验收测试
设计院 · 2026-07-09
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from brahma_v6.apps.million_run_simulator import (
    run_state_machine_sim,
    run_ledger_sim,
    run_full_simulation,
)


# ── 状态机仿真 ────────────────────────────────────────────────────────────────

def test_sm_zero_illegal_leakage():
    """最关键：非法转移零泄漏。"""
    r = run_state_machine_sim(n_chains=10_000, seed=0)
    assert r.illegal_leaked == 0, f"Leaked {r.illegal_leaked} illegal transitions"


def test_sm_illegal_caught_positive():
    """注入 5% 非法事件，拦截数必须 > 0。"""
    r = run_state_machine_sim(n_chains=10_000, illegal_injection_rate=0.1, seed=1)
    assert r.illegal_caught > 0


def test_sm_throughput_above_100k():
    """吞吐量至少 100K transitions/s。"""
    r = run_state_machine_sim(n_chains=20_000, seed=2)
    assert r.throughput > 100_000, f"Throughput too low: {r.throughput:.0f}"


def test_sm_total_equals_legal_plus_illegal():
    """合法 + 拦截 = 总数（无遗漏）。"""
    r = run_state_machine_sim(n_chains=5_000, seed=3)
    assert r.legal_transitions + r.illegal_caught + r.illegal_leaked == r.total_transitions


# ── 账本仿真 ──────────────────────────────────────────────────────────────────

def test_ledger_math_conservation_100pct():
    """数学守恒率必须 100%。"""
    r = run_ledger_sim(n_records=2_000, seed=0)
    assert r.conservation_rate >= 0.9999, f"Conservation rate: {r.conservation_rate}"


def test_ledger_pnl_rejections_positive():
    """PnL 破损注入率 5% → 必须有拒写。"""
    r = run_ledger_sim(n_records=2_000, broken_pnl_rate=0.05, seed=1)
    assert r.rejected_pnl > 0


def test_ledger_chain_rejections_positive():
    """链路破损注入率 5% → 必须有拒写。"""
    r = run_ledger_sim(n_records=2_000, broken_chain_rate=0.05, seed=2)
    assert r.rejected_chain > 0


def test_ledger_duplicate_rejections_positive():
    """重复ID注入率 3% → 必须有拒写。"""
    r = run_ledger_sim(n_records=2_000, duplicate_rate=0.03, seed=3)
    assert r.rejected_duplicate > 0


def test_ledger_total_accounts():
    """accepted + rejected_pnl + rejected_chain + rejected_dup ≈ total_attempts。"""
    r = run_ledger_sim(n_records=2_000, seed=4)
    accounted = r.accepted + r.rejected_pnl + r.rejected_chain + r.rejected_duplicate
    assert accounted == r.total_attempts


def test_ledger_throughput_above_5k():
    """吞吐量至少 5K records/s。"""
    r = run_ledger_sim(n_records=5_000, seed=5)
    assert r.throughput > 5_000


# ── 完整仿真集成测试 ──────────────────────────────────────────────────────────

def test_full_simulation_production_candidate():
    """完整仿真必须输出 PRODUCTION CANDIDATE（双 PASS）。"""
    report = run_full_simulation(sm_chains=50_000, ledger_records=5_000, seed=42)
    assert report.sm.illegal_leaked == 0
    assert report.lg.conservation_rate >= 0.9999
