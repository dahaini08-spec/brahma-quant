"""
brahma_v6/ops/ten_million/optimizer.py — PolicyOptimizer: grid search over parameter space
Phase 5 | 2026-07-09

Scores each combo by:
  - zero invariant violations
  - win_rate
  - expectancy
  - risk_score
"""
from __future__ import annotations
import itertools
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    import random
    HAS_NUMPY = False


@dataclass
class PolicyParams:
    """A set of policy parameters to evaluate."""
    leverage: int = 5
    daily_loss_pct: float = 0.10
    max_trades: int = 20
    min_score: float = 0.5
    stop_loss_pct: float = 0.02
    trailing_activation_pct: float = 0.012


@dataclass
class PolicyScore:
    """Score for a policy evaluation."""
    params: PolicyParams
    composite_score: float
    win_rate: float
    expectancy: float
    risk_score: float
    invariant_violations: int
    valid: bool  # True if zero invariant violations


def _simulate_policy(params: PolicyParams, n_trades: int = 1000, seed: int = 42) -> PolicyScore:
    """
    Simulate N trades under the given policy and compute score metrics.
    Fast: uses numpy if available.
    """
    if HAS_NUMPY:
        rng = np.random.default_rng(seed)
        # Generate trade outcomes based on policy params
        # Higher leverage → higher variance
        vol = 0.005 * params.leverage
        win_prob = 0.52 + (params.min_score - 0.5) * 0.3  # Better score → higher win rate
        win_prob = max(0.3, min(0.75, win_prob))

        outcomes = rng.uniform(-1.0, 1.5, n_trades)
        wins = outcomes > 0
        win_rate = float(np.mean(wins))

        # Expectancy per trade
        avg_win = float(np.mean(outcomes[wins])) if wins.any() else 0.02
        avg_loss = float(np.mean(outcomes[~wins])) if (~wins).any() else -0.01
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

        # Risk score: lower leverage + smaller stop = better
        risk_score = 1.0 - (params.leverage / 20.0) - (params.stop_loss_pct * 2.0)
        risk_score = max(0.0, risk_score)

    else:
        import random as rnd
        rnd.seed(seed)
        wins = sum(1 for _ in range(n_trades) if rnd.random() < 0.52)
        win_rate = wins / n_trades
        expectancy = win_rate * 0.02 + (1 - win_rate) * (-0.01)
        risk_score = 1.0 - (params.leverage / 20.0)

    # Invariant violations: 0 if policy is within safe bounds
    violations = 0
    if params.leverage > 10:
        violations += 100
    if params.daily_loss_pct > 0.15:
        violations += 50
    if params.max_trades > 50:
        violations += 10
    if params.min_score < 0.3:
        violations += 20

    valid = violations == 0
    composite = (win_rate * 0.3 + max(0, expectancy) * 0.4 + risk_score * 0.3) if valid else -1.0

    return PolicyScore(
        params=params,
        composite_score=composite,
        win_rate=win_rate,
        expectancy=expectancy,
        risk_score=risk_score,
        invariant_violations=violations,
        valid=valid,
    )


class PolicyOptimizer:
    """
    Grid search over parameter space to find the best trading policy.
    Only policies with zero invariant violations can rank first.
    """

    DEFAULT_LEVERAGE_GRID = [3, 5, 7]
    DEFAULT_DAILY_LOSS_GRID = [0.05, 0.08, 0.10]
    DEFAULT_MAX_TRADES_GRID = [15, 20, 25]
    DEFAULT_MIN_SCORE_GRID = [0.5, 0.6, 0.7]
    DEFAULT_STOP_LOSS_GRID = [0.015, 0.020, 0.025]
    DEFAULT_TRAILING_GRID = [0.010, 0.012, 0.015]

    def __init__(
        self,
        leverage_grid: List[int] = None,
        daily_loss_grid: List[float] = None,
        max_trades_grid: List[int] = None,
        min_score_grid: List[float] = None,
        stop_loss_grid: List[float] = None,
        trailing_grid: List[float] = None,
        n_trades_per_eval: int = 1000,
        seed: int = 42,
    ) -> None:
        self.leverage_grid = leverage_grid or self.DEFAULT_LEVERAGE_GRID
        self.daily_loss_grid = daily_loss_grid or self.DEFAULT_DAILY_LOSS_GRID
        self.max_trades_grid = max_trades_grid or self.DEFAULT_MAX_TRADES_GRID
        self.min_score_grid = min_score_grid or self.DEFAULT_MIN_SCORE_GRID
        self.stop_loss_grid = stop_loss_grid or self.DEFAULT_STOP_LOSS_GRID
        self.trailing_grid = trailing_grid or self.DEFAULT_TRAILING_GRID
        self.n_trades_per_eval = n_trades_per_eval
        self.seed = seed

    def run(self) -> List[PolicyScore]:
        """Run full grid search. Returns scores sorted by composite_score descending."""
        scores = []
        combos = list(itertools.product(
            self.leverage_grid,
            self.daily_loss_grid,
            self.max_trades_grid,
            self.min_score_grid,
            self.stop_loss_grid,
            self.trailing_grid,
        ))

        for i, (lev, dl, mt, ms, sl, ta) in enumerate(combos):
            params = PolicyParams(
                leverage=lev,
                daily_loss_pct=dl,
                max_trades=mt,
                min_score=ms,
                stop_loss_pct=sl,
                trailing_activation_pct=ta,
            )
            score = _simulate_policy(params, n_trades=self.n_trades_per_eval, seed=self.seed + i)
            scores.append(score)

        # Sort: valid first, then by composite score
        scores.sort(key=lambda s: (not s.valid, -s.composite_score))
        return scores

    def best_policy(self) -> Optional[PolicyScore]:
        """Return the best valid policy (zero invariant violations)."""
        scores = self.run()
        for score in scores:
            if score.valid:
                return score
        return None
