"""Single source of trading truth. Defaults are conservative and testnet-first."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    env: str = "paper"  # paper | testnet | live
    quote_ccy: str = "USDT"
    start_nav: float = 10_000.0
    max_leverage: float = 5.0
    max_gross_exposure: float = 1.5
    max_symbol_weight: float = 0.12
    # [2026-09-07 三方评估封印] 高质量体制×方向 专属仓位上限
    # BEAR_EARLY:SHORT WR=89.3%(n=28) — 当前最高质量组合
    high_quality_regime_dir: dict = None  # 当设置None时用默认
    high_quality_max_weight: float = 0.08  # 8%NAV 上限
    max_open_positions: int = 3
    min_score: float = 140.0
    min_grade: float = 80.0
    min_rr: float = 1.5
    max_rr: float = 2.5
    # [2026-09-07 三方评估封印] score死亡区间
    # 130-145小段WR=17.9%，在CHOP/BEAR体制下擦益拦截
    score_dead_zone_lo: float = 130.0
    score_dead_zone_hi: float = 145.0
    taker_fee_bps: float = 4.0
    maker_fee_bps: float = 2.0
    slippage_bps: float = 3.0
    funding_default_bps_8h: float = 1.0
    same_bar_priority: str = "stop"  # stop | target | skip
    bar_interval: str = "5m"
    signal_ttl_hours: float = 24.0
    promote_min_n: int = 80
    promote_min_wr: float = 0.55
    promote_max_dd: float = 0.12
    promote_min_days: int = 30
    dead_long_regimes: tuple[str, ...] = (
        "BEAR_TREND",
        "BEAR_CRASH",
        "CHOP_MID",    # 梵天宪法封禁：CHOP_MID_LONG WR不足支撑
        "CHOP_HIGH",   # 梵天宪法封禁
        "BEAR_EARLY",  # 梵天宪法封禁：顶部反转期严禁做多
    )
    dead_short_regimes: tuple[str, ...] = (
        "BULL_TREND",
        "BEAR_RECOVERY",  # 梵天宪法封禁：BEAR_RECOVERY SHORT WR=0%
    )
    repo_root: str = "."

    @property
    def live_enabled(self) -> bool:
        return self.env == "live"

    @property
    def data_dir(self) -> Path:
        return Path(self.repo_root) / "data" / "brahma_os"


def load_settings(repo_root: str | Path | None = None) -> Settings:
    root = str(repo_root or Path(__file__).resolve().parents[1])
    env = os.getenv("BRAHMA_ENV", "paper").strip().lower()
    if env not in {"paper", "testnet", "live"}:
        env = "paper"
    if os.getenv("BINANCE_TESTNET", "true").lower() == "true" and env == "live":
        env = "testnet"
    return Settings(env=env, repo_root=root)
