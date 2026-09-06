"""Monte Carlo worlds around the same signal set.

Three orthogonal shocks, because one bootstrap of WR is not a system test:
  1. path seed  — market is not a single history
  2. cost shock — fee/slippage 1x / 2x / 3x
  3. same-bar law — stop vs target vs skip
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import mean

from brahma_os.config import Settings
from brahma_os.contracts import Signal
from brahma_os.paths import generate_gbm_path
from brahma_os.simulator import PathSimulator, SimReport


@dataclass
class WorldResult:
    seed: int
    cost_mult: float
    same_bar: str
    nav: float
    max_dd: float
    wr: float
    n_closed: int
    ev: float
    filled: int


@dataclass
class MCSummary:
    worlds: int
    nav_p05: float
    nav_p50: float
    nav_p95: float
    dd_p50: float
    dd_p95: float
    wr_p50: float
    ruin_rate: float
    mean_ev: float
    worlds_detail: list[WorldResult]

    def as_dict(self) -> dict:
        return {
            "worlds": self.worlds,
            "nav_p05": round(self.nav_p05, 2),
            "nav_p50": round(self.nav_p50, 2),
            "nav_p95": round(self.nav_p95, 2),
            "dd_p50": round(self.dd_p50, 4),
            "dd_p95": round(self.dd_p95, 4),
            "wr_p50": round(self.wr_p50, 4),
            "ruin_rate": round(self.ruin_rate, 4),
            "mean_ev": round(self.mean_ev, 4),
        }


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    idx = int(round((len(ys) - 1) * q))
    return ys[idx]


def run_worlds(
    signals: list[Signal],
    *,
    settings: Settings | None = None,
    n_worlds: int = 200,
    n_bars: int = 1_500,
    start_px: float = 100.0,
    base_seed: int = 7,
    cost_mults: tuple[float, ...] = (1.0, 2.0),
    same_bars: tuple[str, ...] = ("stop",),
    ruin_nav: float | None = None,
    mu_per_year: float = 0.0,
    vol_per_year: float = 0.80,
) -> MCSummary:
    base = settings or Settings()
    ruin = ruin_nav if ruin_nav is not None else base.start_nav * 0.70
    results: list[WorldResult] = []
    for cost_mult in cost_mults:
        for law in same_bars:
            for w in range(n_worlds):
                seed = base_seed + w * 17 + int(cost_mult * 100) + len(law)
                st = replace(
                    base,
                    taker_fee_bps=base.taker_fee_bps * cost_mult,
                    maker_fee_bps=base.maker_fee_bps * cost_mult,
                    slippage_bps=base.slippage_bps * cost_mult,
                    funding_default_bps_8h=base.funding_default_bps_8h * cost_mult,
                    same_bar_priority=law,
                )
                path = generate_gbm_path(
                    seed=seed,
                    start_px=start_px,
                    n_bars=n_bars,
                    mu_per_year=mu_per_year,
                    vol_per_year=vol_per_year,
                )
                report: SimReport = PathSimulator(st).run(
                    signals, path, symbol=signals[0].symbol if signals else None
                )
                snap = report.snapshot
                results.append(
                    WorldResult(
                        seed=seed,
                        cost_mult=cost_mult,
                        same_bar=law,
                        nav=snap.nav,
                        max_dd=snap.max_drawdown,
                        wr=snap.wr,
                        n_closed=snap.n_closed,
                        ev=snap.ev_usd,
                        filled=report.filled,
                    )
                )
    navs = [r.nav for r in results]
    dds = [r.max_dd for r in results]
    wrs = [r.wr for r in results]
    return MCSummary(
        worlds=len(results),
        nav_p05=_pct(navs, 0.05),
        nav_p50=_pct(navs, 0.50),
        nav_p95=_pct(navs, 0.95),
        dd_p50=_pct(dds, 0.50),
        dd_p95=_pct(dds, 0.95),
        wr_p50=_pct(wrs, 0.50),
        ruin_rate=sum(1 for r in results if r.nav <= ruin) / len(results) if results else 1.0,
        mean_ev=mean([r.ev for r in results]) if results else 0.0,
        worlds_detail=results,
    )


def block_bootstrap_nav(
    daily_rets: list[float], *,
    n_worlds: int, block: int, seed: int, start_nav: float
) -> list[float]:
    if not daily_rets:
        return [start_nav] * n_worlds
    rng = __import__("random").Random(seed)
    n = len(daily_rets)
    block = max(1, min(block, n))
    out = []
    for _ in range(n_worlds):
        nav = start_nav
        have = 0
        while have < n:
            i = rng.randrange(0, n)
            chunk = daily_rets[i : i + block]
            if len(chunk) < block:
                chunk = daily_rets[i:] + daily_rets[: block - len(chunk)]
            for r in chunk:
                nav *= 1.0 + r
                have += 1
                if have >= n:
                    break
        out.append(nav)
    return out
