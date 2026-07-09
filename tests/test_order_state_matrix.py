"""
tests/test_order_state_matrix.py
Order State Machine — 完整状态矩阵测试
设计院 · 2026-07-09

覆盖：
  - 所有合法主路径
  - 边界竞态（cancel race）
  - UNKNOWN 恢复路径
  - 终态封口（terminal → any = 非法）
  - 非法跳跃检测
"""
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from brahma_v6.execution.order_state import (
    OrderState,
    IllegalTransitionError,
    validate_transition,
    is_terminal,
    TERMINAL_STATES,
    ALLOWED_TRANSITIONS,
)

S = OrderState  # alias for brevity


# ─────────────────────────────────────────────
# 1. 合法主路径
# ─────────────────────────────────────────────

def test_happy_path_full_fill():
    """CREATED → RISK_APPROVED → SUBMITTING → SUBMITTED → ACCEPTED → FILLED"""
    path = [S.CREATED, S.RISK_APPROVED, S.SUBMITTING, S.SUBMITTED, S.ACCEPTED, S.FILLED]
    for a, b in zip(path, path[1:]):
        validate_transition(a, b)  # must not raise


def test_happy_path_partial_then_full():
    """ACCEPTED → PARTIALLY_FILLED → FILLED"""
    validate_transition(S.ACCEPTED, S.PARTIALLY_FILLED)
    validate_transition(S.PARTIALLY_FILLED, S.FILLED)


def test_happy_path_cancel():
    """ACCEPTED → CANCEL_PENDING → CANCELLED"""
    validate_transition(S.ACCEPTED, S.CANCEL_PENDING)
    validate_transition(S.CANCEL_PENDING, S.CANCELLED)


def test_happy_path_partial_cancel():
    """PARTIALLY_FILLED → CANCEL_PENDING → CANCELLED"""
    validate_transition(S.PARTIALLY_FILLED, S.CANCEL_PENDING)
    validate_transition(S.CANCEL_PENDING, S.CANCELLED)


def test_happy_path_rejected_at_risk():
    """CREATED → RISK_APPROVED → REJECTED"""
    validate_transition(S.CREATED, S.REJECTED)
    validate_transition(S.RISK_APPROVED, S.REJECTED)


def test_happy_path_expired():
    """ACCEPTED → EXPIRED"""
    validate_transition(S.ACCEPTED, S.EXPIRED)


# ─────────────────────────────────────────────
# 2. 竞态边界：cancel race — order already filled
# ─────────────────────────────────────────────

def test_cancel_race_already_filled():
    """CANCEL_PENDING → FILLED is legal (exchange filled before cancel ACK)"""
    validate_transition(S.CANCEL_PENDING, S.FILLED)


def test_cancel_race_unknown():
    """CANCEL_PENDING → UNKNOWN is legal (timeout / exchange silence)"""
    validate_transition(S.CANCEL_PENDING, S.UNKNOWN)


# ─────────────────────────────────────────────
# 3. UNKNOWN 恢复路径
# ─────────────────────────────────────────────

def test_unknown_reconcile():
    """UNKNOWN → RECONCILED (manual reconciliation)"""
    validate_transition(S.UNKNOWN, S.RECONCILED)


def test_unknown_recover_to_filled():
    """UNKNOWN → FILLED (exchange confirms fill after timeout)"""
    validate_transition(S.UNKNOWN, S.FILLED)


def test_unknown_recover_to_accepted():
    """UNKNOWN → ACCEPTED (exchange confirms order live)"""
    validate_transition(S.UNKNOWN, S.ACCEPTED)


def test_unknown_recover_to_cancelled():
    """UNKNOWN → CANCELLED"""
    validate_transition(S.UNKNOWN, S.CANCELLED)


# ─────────────────────────────────────────────
# 4. 终态封口：terminal → 任何状态均非法
# ─────────────────────────────────────────────

@pytest.mark.parametrize("terminal", list(TERMINAL_STATES))
def test_terminal_no_outgoing(terminal):
    """Terminal states must have no allowed outgoing transitions."""
    assert ALLOWED_TRANSITIONS[terminal] == set(), (
        f"{terminal.value} should have no outgoing transitions"
    )


@pytest.mark.parametrize("terminal", list(TERMINAL_STATES))
@pytest.mark.parametrize("any_state", list(OrderState))
def test_terminal_raises_on_any_transition(terminal, any_state):
    """validate_transition from any terminal must always raise."""
    with pytest.raises(IllegalTransitionError):
        validate_transition(terminal, any_state)


# ─────────────────────────────────────────────
# 5. 非法跳跃检测
# ─────────────────────────────────────────────

def test_illegal_skip_created_to_filled():
    """CREATED → FILLED is not allowed (must go through risk/submit)."""
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.CREATED, S.FILLED)


def test_illegal_skip_submitted_to_partially_filled():
    """SUBMITTED → PARTIALLY_FILLED skips ACCEPTED."""
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.SUBMITTED, S.PARTIALLY_FILLED)


def test_illegal_backward_filled_to_accepted():
    """FILLED → ACCEPTED is backward and forbidden."""
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.FILLED, S.ACCEPTED)


def test_illegal_cancelled_to_submitting():
    """CANCELLED → SUBMITTING is resurrection — forbidden."""
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.CANCELLED, S.SUBMITTING)


def test_illegal_risk_approved_to_accepted():
    """RISK_APPROVED → ACCEPTED skips SUBMITTING/SUBMITTED."""
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.RISK_APPROVED, S.ACCEPTED)


# ─────────────────────────────────────────────
# 6. is_terminal helper
# ─────────────────────────────────────────────

@pytest.mark.parametrize("state,expected", [
    (S.FILLED,      True),
    (S.CANCELLED,   True),
    (S.EXPIRED,     True),
    (S.REJECTED,    True),
    (S.RECONCILED,  True),
    (S.CREATED,     False),
    (S.ACCEPTED,    False),
    (S.UNKNOWN,     False),
    (S.SUBMITTING,  False),
])
def test_is_terminal(state, expected):
    assert is_terminal(state) == expected


# ─────────────────────────────────────────────
# 7. 全量转换表完整性
# ─────────────────────────────────────────────

def test_all_states_in_transition_table():
    """Every OrderState must appear as a key in ALLOWED_TRANSITIONS."""
    for state in OrderState:
        assert state in ALLOWED_TRANSITIONS, (
            f"{state.value} missing from ALLOWED_TRANSITIONS"
        )
