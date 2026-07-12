"""
brahma_v6/risk/risk_kernel.py — RiskKernel: full risk evaluation pipeline
Phase 5 | 2026-07-09

Evaluation order (from spec):
  1. kill_switch check — BLOCK all
  2. daily_loss >= 10% NAV → activate kill_switch + BLOCK
  3. open_positions >= max → BLOCK
  4. open_orders >= max → BLOCK
  5. trades_today >= max → BLOCK
  6. ev_bucket.action != ALLOW → BLOCK
  7. score < min_score → BLOCK
  8. symbol not in allowlist → BLOCK
  9. proposed notional < min_notional → BLOCK
  10. order_type not LIMIT/LIMIT_MAKER → BLOCK
  11. → APPROVE with proposed order
"""
from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from brahma_v6.risk.models import RiskDecision, RiskAction, ProposedOrder
from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.risk.daily_loss_guard import DailyLossGuard
from brahma_v6.risk.exposure_guard import ExposureGuard
from brahma_v6.risk.leverage_guard import LeverageGuard


MIN_SCORE_DEFAULT = 0.5
MIN_NOTIONAL_DEFAULT = 5.0  # USDT
ALLOWED_ORDER_TYPES = {"LIMIT", "LIMIT_MAKER"}


@dataclass
class AccountState:
    """Snapshot of account state for risk evaluation."""
    nav: float = 0.0
    daily_pnl: float = 0.0
    open_positions: int = 0
    open_orders: int = 0
    trades_today: int = 0
    consecutive_losses: int = 0
    last_trade_ts: Optional[float] = None
    leverage: int = 5
    # Cooldown tracking
    last_loss_ts: Optional[float] = None
    last_reject_ts: Optional[float] = None


@dataclass
class Signal:
    """Input signal to risk evaluation."""
    symbol: str
    side: str                   # BUY | SELL
    score: float
    order_type: str = "LIMIT"
    price: float = 0.0
    quantity: float = 0.0
    ev_bucket_action: str = "ALLOW"   # ALLOW | WATCHLIST | BLOCK
    reduce_only: bool = False
    signal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: Dict[str, Any] = field(default_factory=dict)


class RiskKernel:
    """
    Pure function risk evaluation engine.
    evaluate() is idempotent: given the same signal + account_state, returns same decision.
    No I/O, no side effects except activating kill_switch on daily loss breach.
    """

    def __init__(
        self,
        kill_switch: KillSwitch,
        symbol_allowlist: Optional[List[str]] = None,
        max_open_positions: int = 1,
        max_open_orders: int = 1,
        max_trades_per_day: int = 20,
        max_daily_loss_pct: float = 0.10,
        min_score: float = MIN_SCORE_DEFAULT,
        min_notional: float = MIN_NOTIONAL_DEFAULT,
        cooldown_after_loss_minutes: float = 20.0,
        cooldown_after_reject_minutes: float = 10.0,
        max_consecutive_losses: int = 3,
    ) -> None:
        self.kill_switch = kill_switch
        self.symbol_allowlist = set(symbol_allowlist) if symbol_allowlist else {"ETHUSDT"}
        self.max_open_positions = max_open_positions
        self.max_open_orders = max_open_orders
        self.max_trades_per_day = max_trades_per_day
        self.max_daily_loss_pct = max_daily_loss_pct
        self.min_score = min_score
        self.min_notional = min_notional
        self.cooldown_after_loss_seconds = cooldown_after_loss_minutes * 60
        self.cooldown_after_reject_seconds = cooldown_after_reject_minutes * 60
        self.max_consecutive_losses = max_consecutive_losses

    def evaluate(self, signal: Signal, account_state: AccountState) -> RiskDecision:
        """
        Full risk evaluation. Returns RiskDecision(action, reason, proposed_order).
        Kill switch activation on daily loss breach is the only side effect.
        """
        now = time.time()

        # 1. Kill switch check — block everything
        if self.kill_switch.is_active():
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"kill_switch active: {self.kill_switch.reason}",
            )

        # 2. Daily loss check — activate kill switch + block
        if account_state.nav > 0:
            loss_pct = -account_state.daily_pnl / account_state.nav
            if loss_pct >= self.max_daily_loss_pct:
                self.kill_switch.activate(
                    f"daily_loss_breach: loss={loss_pct:.2%} >= limit={self.max_daily_loss_pct:.2%}"
                )
                return RiskDecision(
                    action=RiskAction.BLOCK,
                    reason=f"daily_loss={loss_pct:.2%} >= limit={self.max_daily_loss_pct:.2%}; kill_switch activated",
                )

        # 3. Open positions limit
        if account_state.open_positions >= self.max_open_positions:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"open_positions={account_state.open_positions} >= max={self.max_open_positions}",
            )

        # 4. Open orders limit
        if account_state.open_orders >= self.max_open_orders:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"open_orders={account_state.open_orders} >= max={self.max_open_orders}",
            )

        # 5. Daily trades limit
        if account_state.trades_today >= self.max_trades_per_day:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"trades_today={account_state.trades_today} >= max={self.max_trades_per_day}",
            )

        # 5b. Consecutive losses cooldown
        if account_state.consecutive_losses >= self.max_consecutive_losses:
            if account_state.last_loss_ts is not None:
                elapsed = now - account_state.last_loss_ts
                if elapsed < self.cooldown_after_loss_seconds:
                    remaining = self.cooldown_after_loss_seconds - elapsed
                    return RiskDecision(
                        action=RiskAction.BLOCK,
                        reason=f"consecutive_losses={account_state.consecutive_losses} cooldown active ({remaining:.0f}s remaining)",
                    )

        # 5c. Reject cooldown
        if account_state.last_reject_ts is not None:
            elapsed = now - account_state.last_reject_ts
            if elapsed < self.cooldown_after_reject_seconds:
                remaining = self.cooldown_after_reject_seconds - elapsed
                return RiskDecision(
                    action=RiskAction.BLOCK,
                    reason=f"reject_cooldown active ({remaining:.0f}s remaining)",
                )

        # 6. EV bucket check
        ev_action = (signal.ev_bucket_action or "ALLOW").upper()
        if ev_action != "ALLOW":
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"ev_bucket_action={ev_action} != ALLOW",
            )

        # 7. Score gate
        if signal.score < self.min_score:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"score={signal.score:.4f} < min_score={self.min_score}",
            )

        # 8. Symbol allowlist
        if signal.symbol not in self.symbol_allowlist:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"symbol={signal.symbol} not in allowlist={self.symbol_allowlist}",
            )

        # 9. Notional check
        notional = signal.price * signal.quantity
        if notional < self.min_notional and not signal.reduce_only:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"notional={notional:.2f} < min_notional={self.min_notional}",
            )

        # 10. Order type check
        order_type_upper = signal.order_type.upper()
        if order_type_upper not in ALLOWED_ORDER_TYPES:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=f"order_type={signal.order_type} not in {ALLOWED_ORDER_TYPES}",
            )

        # 11. APPROVE
        proposed = ProposedOrder(
            symbol=signal.symbol,
            side=signal.side,
            order_type=order_type_upper,
            quantity=signal.quantity,
            price=signal.price,
            client_order_id=f"brahma_{signal.signal_id[:16]}",
            reduce_only=signal.reduce_only,
        )
        return RiskDecision(
            action=RiskAction.APPROVE,
            reason="all_checks_passed",
            proposed_order=proposed,
        )
