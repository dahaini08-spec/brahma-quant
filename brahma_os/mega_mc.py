"""Million-path Monte Carlo on costed trades / daily equity.

A full 1e6 * 1500-bar event replay is not a deployable job in this repo.
What is deployable: 1e6 resamples of an already-costed ledger.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class MegaSummary:
    n: int
    mode: str
    start_nav: float
    nav_p01: float
    nav_p05: float
    nav_p50: float
    nav_p95: float
    nav_p99: float
    ruin_rate: float
    mean_nav: float
    median_maxdd: float
    p95_maxdd: float

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mode": self.mode,
            "start_nav": self.start_nav,
            "nav_p01": round(self.nav_p01, 2),
            "nav_p05": round(self.nav_p05, 2),
            "nav_p50": round(self.nav_p50, 2),
            "nav_p95": round(self.nav_p95, 2),
            "nav_p99": round(self.nav_p99, 2),
            "ruin_rate": round(self.ruin_rate, 6),
            "mean_nav": round(self.mean_nav, 2),
            "median_maxdd": round(self.median_maxdd, 4),
            "p95_maxdd": round(self.p95_maxdd, 4),
        }


def _pct(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return 0.0
    return sorted_xs[int((len(sorted_xs) - 1) * q)]


def bootstrap_trades(
    pnls: list[float],
    *,
    start_nav: float = 10_000.0,
    n: int = 1_000_000,
    seed: int = 7,
    ruin_ratio: float = 0.70,
    replace: bool = True,
    track_dd: bool = True,
    dd_sample: int = 20_000,
) -> MegaSummary:
    if not pnls:
        raise ValueError("pnls empty")
    rng = random.Random(seed)
    m = len(pnls)
    ruin_level = start_nav * ruin_ratio
    finals: list[float] = []
    ruins = 0
    dd_vals: list[float] = []
    for i in range(n):
        nav = start_nav
        peak = start_nav
        maxdd = 0.0
        want_dd = track_dd and i < dd_sample
        for _ in range(m):
            nav += pnls[rng.randrange(m)]
            if want_dd:
                if nav > peak:
                    peak = nav
                if peak > 0:
                    d = (peak - nav) / peak
                    if d > maxdd:
                        maxdd = d
        finals.append(nav)
        if nav <= ruin_level:
            ruins += 1
        if want_dd:
            dd_vals.append(maxdd)
    finals.sort()
    dd_vals.sort()
    return MegaSummary(
        n=n,
        mode="bootstrap_replace" if replace else "shuffle",
        start_nav=start_nav,
        nav_p01=_pct(finals, 0.01),
        nav_p05=_pct(finals, 0.05),
        nav_p50=_pct(finals, 0.50),
        nav_p95=_pct(finals, 0.95),
        nav_p99=_pct(finals, 0.99),
        ruin_rate=ruins / n,
        mean_nav=sum(finals) / n,
        median_maxdd=_pct(dd_vals, 0.50) if dd_vals else 0.0,
        p95_maxdd=_pct(dd_vals, 0.95) if dd_vals else 0.0,
    )


def shuffle_trades(
    pnls: list[float],
    *,
    start_nav: float = 10_000.0,
    n: int = 1_000_000,
    seed: int = 7,
    ruin_ratio: float = 0.70,
    dd_sample: int = 20_000,
) -> MegaSummary:
    if not pnls:
        raise ValueError("pnls empty")
    rng = random.Random(seed)
    base = list(pnls)
    ruin_level = start_nav * ruin_ratio
    finals: list[float] = []
    ruins = 0
    dd_vals: list[float] = []
    for i in range(n):
        rng.shuffle(base)
        nav = start_nav
        peak = start_nav
        maxdd = 0.0
        want_dd = i < dd_sample
        for p in base:
            nav += p
            if want_dd:
                if nav > peak:
                    peak = nav
                if peak > 0:
                    d = (peak - nav) / peak
                    if d > maxdd:
                        maxdd = d
        finals.append(nav)
        if nav <= ruin_level:
            ruins += 1
        if want_dd:
            dd_vals.append(maxdd)
    finals.sort()
    dd_vals.sort()
    return MegaSummary(
        n=n,
        mode="shuffle",
        start_nav=start_nav,
        nav_p01=_pct(finals, 0.01),
        nav_p05=_pct(finals, 0.05),
        nav_p50=_pct(finals, 0.50),
        nav_p95=_pct(finals, 0.95),
        nav_p99=_pct(finals, 0.99),
        ruin_rate=ruins / n,
        mean_nav=sum(finals) / n,
        median_maxdd=_pct(dd_vals, 0.50) if dd_vals else 0.0,
        p95_maxdd=_pct(dd_vals, 0.95) if dd_vals else 0.0,
    )


def block_bootstrap_daily(
    daily_rets: list[float],
    *,
    start_nav: float = 10_000.0,
    n: int = 1_000_000,
    block: int = 5,
    seed: int = 7,
    ruin_ratio: float = 0.70,
) -> MegaSummary:
    if not daily_rets:
        raise ValueError("daily_rets empty")
    rng = random.Random(seed)
    n_days = len(daily_rets)
    block = max(1, min(block, n_days))
    ruin_level = start_nav * ruin_ratio
    finals: list[float] = []
    ruins = 0
    for _ in range(n):
        nav = start_nav
        have = 0
        while have < n_days:
            i = rng.randrange(n_days)
            take = min(block, n_days - have)
            for k in range(take):
                nav *= 1.0 + daily_rets[(i + k) % n_days]
                have += 1
                if have >= n_days:
                    break
        finals.append(nav)
        if nav <= ruin_level:
            ruins += 1
    finals.sort()
    return MegaSummary(
        n=n,
        mode=f"block_bootstrap_{block}",
        start_nav=start_nav,
        nav_p01=_pct(finals, 0.01),
        nav_p05=_pct(finals, 0.05),
        nav_p50=_pct(finals, 0.50),
        nav_p95=_pct(finals, 0.95),
        nav_p99=_pct(finals, 0.99),
        ruin_rate=ruins / n,
        mean_nav=sum(finals) / n,
        median_maxdd=0.0,
        p95_maxdd=0.0,
    )


def promote_mega(summary: MegaSummary, *, start_nav: float | None = None) -> tuple[bool, list[str]]:
    nav0 = start_nav if start_nav is not None else summary.start_nav
    fails: list[str] = []
    if summary.n < 100_000:
        fails.append(f"n={summary.n}<100000")
    if summary.nav_p50 < nav0:
        fails.append(f"nav_p50={summary.nav_p50:.1f}<start")
    if summary.nav_p05 < nav0 * 0.85:
        fails.append(f"nav_p05={summary.nav_p05:.1f}<85%")
    if summary.ruin_rate > 0.05:
        fails.append(f"ruin={summary.ruin_rate:.4%}>5%")
    if summary.p95_maxdd > 0.18 and summary.p95_maxdd > 0:
        fails.append(f"dd_p95={summary.p95_maxdd:.2%}>18%")
    return (not fails), fails
