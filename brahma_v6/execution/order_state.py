"""
brahma_v6/execution/order_state.py
Order State Machine — 单一真相源
设计院 · 2026-07-09
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Set, Union


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
        OrderState.FILLED,    # cancel race: exchange filled before ACK
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
    # Terminal states — no outgoing transitions
    OrderState.FILLED:      set(),
    OrderState.CANCELLED:   set(),
    OrderState.EXPIRED:     set(),
    OrderState.REJECTED:    set(),
    OrderState.RECONCILED:  set(),
}


class IllegalTransitionError(RuntimeError):
    """Raised when an order state transition is not permitted."""


def validate_transition(
    current: Union[OrderState, str],
    next_state: Union[OrderState, str],
) -> None:
    """
    Assert that (current → next_state) is a legal transition.
    Accepts both OrderState enum values and plain strings.
    Raises IllegalTransitionError on violation.
    """
    current_state = OrderState(current)
    target_state  = OrderState(next_state)
    if target_state not in ALLOWED_TRANSITIONS.get(current_state, set()):
        raise IllegalTransitionError(
            f"Illegal order transition: {current_state.value} -> {target_state.value}"
        )


def is_terminal(state: Union[OrderState, str]) -> bool:
    """Return True if state is a terminal (no-exit) state."""
    return OrderState(state) in TERMINAL_STATES
