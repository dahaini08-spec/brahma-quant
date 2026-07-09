"""
brahma_v6/ops/ten_million/driver.py — SimDriver: generates 10M events with configurable fault rates
Phase 5 | 2026-07-09

Optimized for speed: pre-generates random arrays with numpy, processes in batches.
Target: < 30 seconds for 10M events.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Generator, List

from brahma_v6.ops.ten_million.scenario import EventScenario

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    import random
    HAS_NUMPY = False


@dataclass
class FaultRates:
    """Configurable fault injection rates."""
    unknown_rate: float = 0.001          # 0.1%
    reject_rate: float = 0.002           # 0.2%
    partial_fill_rate: float = 0.005     # 0.5%
    cancel_race_rate: float = 0.001      # 0.1%
    ledger_failure_rate: float = 0.001   # 0.1%
    adapter_mismatch_rate: float = 0.001 # 0.1%
    websocket_gap_rate: float = 0.002    # 0.2%
    kill_switch_rate: float = 0.0001     # 0.01%


class SimDriver:
    """
    Generates N simulated EventScenarios with configurable fault injection.
    Uses numpy for fast batch generation.
    """

    BATCH_SIZE = 100_000

    def __init__(
        self,
        n_events: int = 10_000_000,
        fault_rates: FaultRates = None,
        seed: int = 42,
    ) -> None:
        self.n_events = n_events
        self.fault_rates = fault_rates or FaultRates()
        self.seed = seed

    def generate(self) -> Generator[EventScenario, None, None]:
        """Generate all events as a lazy generator."""
        fr = self.fault_rates

        if HAS_NUMPY:
            yield from self._generate_numpy(fr)
        else:
            yield from self._generate_python(fr)

    def _generate_numpy(self, fr: FaultRates) -> Generator[EventScenario, None, None]:
        rng = np.random.default_rng(self.seed)
        remaining = self.n_events

        while remaining > 0:
            batch = min(self.BATCH_SIZE, remaining)
            remaining -= batch

            # Pre-generate all random arrays for batch
            scores = rng.uniform(0.4, 1.0, batch)
            prices = rng.uniform(1800.0, 2200.0, batch)
            qtys = rng.uniform(0.001, 0.1, batch)
            navs = rng.uniform(8000.0, 12000.0, batch)
            daily_pnls = rng.uniform(-500.0, 500.0, batch)
            open_pos = rng.integers(0, 3, batch)
            open_ords = rng.integers(0, 2, batch)
            trades = rng.integers(0, 22, batch)
            cons_loss = rng.integers(0, 5, batch)

            # Fault flags
            is_unknown = rng.random(batch) < fr.unknown_rate
            is_reject = rng.random(batch) < fr.reject_rate
            is_partial = rng.random(batch) < fr.partial_fill_rate
            ledger_fail = rng.random(batch) < fr.ledger_failure_rate
            ws_gap = rng.random(batch) < fr.websocket_gap_rate
            ks_trigger = rng.random(batch) < fr.kill_switch_rate

            # Regime selection
            regimes = rng.choice(["NEUTRAL", "BULL_TREND", "BEAR_TREND", "CHOP_LONG"], batch)
            ev_actions = rng.choice(["ALLOW", "WATCHLIST", "BLOCK"], batch, p=[0.9, 0.07, 0.03])

            for i in range(batch):
                yield EventScenario(
                    event_type="SIGNAL",
                    symbol="ETHUSDT",
                    signal_score=float(scores[i]),
                    regime=str(regimes[i]),
                    ev_bucket_action=str(ev_actions[i]),
                    fill_qty=float(qtys[i]),
                    fill_price=float(prices[i]),
                    is_unknown=bool(is_unknown[i]),
                    is_reject=bool(is_reject[i]),
                    is_partial=bool(is_partial[i]),
                    ledger_fail=bool(ledger_fail[i]),
                    websocket_gap=bool(ws_gap[i]),
                    kill_switch_trigger=bool(ks_trigger[i]),
                    nav=float(navs[i]),
                    daily_pnl=float(daily_pnls[i]),
                    open_positions=int(open_pos[i]),
                    open_orders=int(open_ords[i]),
                    trades_today=int(trades[i]),
                    consecutive_losses=int(cons_loss[i]),
                )

    def _generate_python(self, fr: FaultRates) -> Generator[EventScenario, None, None]:
        """Fallback pure-Python generator (slower)."""
        import random as rnd
        rnd.seed(self.seed)
        regimes = ["NEUTRAL", "BULL_TREND", "BEAR_TREND", "CHOP_LONG"]
        ev_actions = ["ALLOW", "WATCHLIST", "BLOCK"]

        for _ in range(self.n_events):
            yield EventScenario(
                event_type="SIGNAL",
                symbol="ETHUSDT",
                signal_score=rnd.uniform(0.4, 1.0),
                regime=rnd.choice(regimes),
                ev_bucket_action=rnd.choices(ev_actions, weights=[0.9, 0.07, 0.03])[0],
                fill_qty=rnd.uniform(0.001, 0.1),
                fill_price=rnd.uniform(1800.0, 2200.0),
                is_unknown=rnd.random() < fr.unknown_rate,
                is_reject=rnd.random() < fr.reject_rate,
                is_partial=rnd.random() < fr.partial_fill_rate,
                ledger_fail=rnd.random() < fr.ledger_failure_rate,
                websocket_gap=rnd.random() < fr.websocket_gap_rate,
                kill_switch_trigger=rnd.random() < fr.kill_switch_rate,
                nav=rnd.uniform(8000.0, 12000.0),
                daily_pnl=rnd.uniform(-500.0, 500.0),
                open_positions=rnd.randint(0, 2),
                open_orders=rnd.randint(0, 1),
                trades_today=rnd.randint(0, 21),
                consecutive_losses=rnd.randint(0, 4),
            )

    def generate_list(self, n: int = None) -> List[EventScenario]:
        """Generate into a list (for testing small N)."""
        orig = self.n_events
        if n is not None:
            self.n_events = n
        result = list(self.generate())
        self.n_events = orig
        return result
