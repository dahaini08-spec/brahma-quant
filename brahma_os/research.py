"""Promotion is a research decision, not a README number."""

from __future__ import annotations

from dataclasses import dataclass

from brahma_os.config import Settings
from brahma_os.monte_carlo import MCSummary


@dataclass(frozen=True)
class ResearchVerdict:
    promote: bool
    fails: tuple[str, ...]
    summary: dict


def verdict(mc: MCSummary, settings: Settings | None = None) -> ResearchVerdict:
    st = settings or Settings()
    fails: list[str] = []
    if mc.worlds < 50:
        fails.append(f"worlds={mc.worlds}<50")
    if mc.nav_p50 < st.start_nav:
        fails.append(f"nav_p50={mc.nav_p50:.1f}<start")
    if mc.nav_p05 < st.start_nav * 0.85:
        fails.append(f"nav_p05={mc.nav_p05:.1f}<85% start")
    if mc.dd_p95 > st.promote_max_dd * 1.5:
        fails.append(f"dd_p95={mc.dd_p95:.2%}>1.5x cap")
    if mc.ruin_rate > 0.05:
        fails.append(f"ruin={mc.ruin_rate:.2%}>5%")
    if mc.mean_ev <= 0:
        fails.append(f"mean_ev={mc.mean_ev:.4f}<=0")
    if mc.wr_p50 < st.promote_min_wr * 0.8:
        fails.append(f"wr_p50={mc.wr_p50:.2%}<80% of min_wr")
    return ResearchVerdict(promote=not fails, fails=tuple(fails), summary=mc.as_dict())
