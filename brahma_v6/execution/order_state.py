"""
brahma_v6/execution/order_state.py
Order State Machine — Brahma v6 硬链路封口
设计院 · 2026-07-09
"""
from enum import Enum
from typing import Dict, Set


class OrderState(str, Enum):
    CREATED          = "CREATED"
    RISK_APPROVED    = "RISK_APPROVED"
    SUBMITTING       = "SUBMITTING"
    SUBMITTED        = "SUBMITTED"
    ACCEPTED         = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED           = "FILLED"
    CANCEL_PENDING   = "CANCEL_PENDING"
    CANCELLED        = "CANCELLED"
    EXPIRED          = "EXPIRED"
    REJECTED         = "REJECTED"
    UNKNOWN          = "UNKNOWN"
    RECONCILED       = "RECONCILED"


TERMINAL_STATES: Set[OrderState] = {
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.EXPIRED,
    OrderState.REJECTED,
    OrderState.RECONCILED,
}

ALLOWED_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.CREATED: {
        OrderState.RISK_APPROVED,
        OrderState.REJECTED,
    },
    OrderState.RISK_APPROVED: {
        OrderState.SUBMITTING,
        OrderState.REJECTED,
    },
    OrderState.SUBMITTING: {
        OrderState.SUBMITTED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.SUBMITTED: {
        OrderState.ACCEPTED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
        OrderState.CANCEL_PENDING,
    },
    OrderState.ACCEPTED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.EXPIRED,
        OrderState.UNKNOWN,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN,
    },
    OrderState.CANCEL_PENDING: {
        OrderState.CANCELLED,
        OrderState.FILLED,   # cancel race: already filled
        OrderState.UNKNOWN,
    },
    OrderState.UNKNOWN: {
        OrderState.RECONCILED,
        OrderState.SUBMITTED,
        OrderState.ACCEPTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    },
    # Terminal — no outgoing transitions
    OrderState.FILLED:      set(),
    OrderState.CANCELLED:   set(),
    OrderState.EXPIRED:     set(),
    OrderState.REJECTED:    set(),
    OrderState.RECONCILED:  set(),
}


class IllegalTransitionError(RuntimeError):
    """Raised when an order state transition is not permitted."""


def validate_transition(current: OrderState, next_state: OrderState) -> None:
    """
    Assert that (current → next_state) is a legal transition.
    Raises IllegalTransitionError on violation — never silently passes.
    """
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if next_state not in allowed:
        raise IllegalTransitionError(
            f"Illegal order transition: {current.value} → {next_state.value}"
        )


def is_terminal(state: OrderState) -> bool:
    """Return True if state is a terminal (no-exit) state."""
    return state in TERMINAL_STATES
