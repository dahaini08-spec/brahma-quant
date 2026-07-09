"""
brahma_v6/dharma2/ev_bucket.py
EV Bucket Governance — 策略期望值治理
设计院 · 2026-07-09

核心约束（写死，不可绕过）：
  1. 只能用 closed trade 更新
  2. 只能用通过 TradeLedger 三道校验的 TradeRecord
  3. 使用 net_pnl，不使用 gross_pnl
  4. 低样本（n < MIN_SAMPLE_FOR_BLOCK）不允许直接 BLOCK，只能 WATCHLIST
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from brahma_v6.dharma2.models import TradeRecord


# ─── 治理动作 ──────────────────────────────────────────────────────────────────

class BucketAction(str, Enum):
    ALLOW       = "ALLOW"        # 正常放行
    DOWN_WEIGHT = "DOWN_WEIGHT"  # 降权（减少仓位）
    WATCHLIST   = "WATCHLIST"    # 监控（不减仓，但记录预警）
    BLOCK       = "BLOCK"        # 封禁（拒绝新信号）


# 低样本阈值：n < 此值时不得直接 BLOCK
MIN_SAMPLE_FOR_BLOCK = 10

# 治理阈值
WIN_RATE_BLOCK_THRESHOLD       = 0.38   # 胜率 < 38% → BLOCK（样本充足时）
WIN_RATE_DOWN_WEIGHT_THRESHOLD = 0.45   # 胜率 < 45% → DOWN_WEIGHT
EXPECTANCY_BLOCK_THRESHOLD     = -0.005 # 期望值 < -0.5% → BLOCK（样本充足时）
MAX_DRAWDOWN_WATCHLIST         = -0.15  # 最大回撤 > 15% → WATCHLIST


# ─── Bucket Key ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BucketKey:
    """
    分桶维度：symbol × direction × regime × score_bucket × setup_type × timeframe
    score_bucket: "LOW"(< 130) / "MID"(130-150) / "HIGH"(> 150)
    """
    symbol:       str
    direction:    str    # "LONG" | "SHORT"
    regime:       str    # "BEAR_TREND" | "BULL_TREND" | ...
    score_bucket: str    # "LOW" | "MID" | "HIGH"
    setup_type:   str    # "OB" | "FVG" | "BB" | "PUMP" | ...
    timeframe:    str    # "1H" | "4H" | "1D"

    @staticmethod
    def from_record(record: TradeRecord) -> "BucketKey":
        score = record.score
        if score < 130:
            sb = "LOW"
        elif score <= 150:
            sb = "MID"
        else:
            sb = "HIGH"

        # setup_type 和 timeframe 从 regime 派生默认值（可后续扩展到信号元数据）
        setup_type = "UNKNOWN"
        timeframe  = "1H"

        return BucketKey(
            symbol       = record.symbol,
            direction    = record.direction,
            regime       = record.regime,
            score_bucket = sb,
            setup_type   = setup_type,
            timeframe    = timeframe,
        )


# ─── Bucket Stats ──────────────────────────────────────────────────────────────

@dataclass
class BucketStats:
    """
    单个 bucket 的统计指标，基于 net_pnl（不使用 gross_pnl）。
    """
    key: BucketKey
    net_pnls: List[float] = field(default_factory=list)

    # ── 只读统计属性 ────────────────────────────────────────────
    @property
    def n(self) -> int:
        return len(self.net_pnls)

    @property
    def win_rate(self) -> Optional[float]:
        if self.n == 0:
            return None
        return sum(1 for p in self.net_pnls if p > 0) / self.n

    @property
    def avg_net_pnl(self) -> Optional[float]:
        return sum(self.net_pnls) / self.n if self.n > 0 else None

    @property
    def median_net_pnl(self) -> Optional[float]:
        return statistics.median(self.net_pnls) if self.n > 0 else None

    @property
    def expectancy(self) -> Optional[float]:
        """期望值 = 平均净 PnL / 平均持仓名义（此处简化为 avg_net_pnl）。"""
        return self.avg_net_pnl

    @property
    def max_drawdown(self) -> Optional[float]:
        """简化 MDD：累计 PnL 序列的最大回撤。"""
        if self.n == 0:
            return None
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in self.net_pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = cumulative - peak
            if dd < max_dd:
                max_dd = dd
        return max_dd

    def governance_action(self) -> BucketAction:
        """
        基于当前统计决定治理动作。

        核心约束：
          - n < MIN_SAMPLE_FOR_BLOCK → 最高只能 WATCHLIST
          - 使用 net_pnl（已在 net_pnls 中保证）
        """
        if self.n == 0:
            return BucketAction.ALLOW

        wr = self.win_rate
        ev = self.expectancy
        mdd = self.max_drawdown

        # 高样本才允许 BLOCK
        if self.n >= MIN_SAMPLE_FOR_BLOCK:
            if (wr is not None and wr < WIN_RATE_BLOCK_THRESHOLD) or \
               (ev is not None and ev < EXPECTANCY_BLOCK_THRESHOLD):
                return BucketAction.BLOCK

        # 任何样本量都可以 DOWN_WEIGHT / WATCHLIST
        if wr is not None and wr < WIN_RATE_DOWN_WEIGHT_THRESHOLD:
            return BucketAction.DOWN_WEIGHT

        if mdd is not None and mdd < MAX_DRAWDOWN_WATCHLIST:
            return BucketAction.WATCHLIST

        return BucketAction.ALLOW

    def to_dict(self) -> dict:
        return {
            "symbol":        self.key.symbol,
            "direction":     self.key.direction,
            "regime":        self.key.regime,
            "score_bucket":  self.key.score_bucket,
            "setup_type":    self.key.setup_type,
            "timeframe":     self.key.timeframe,
            "n":             self.n,
            "win_rate":      round(self.win_rate, 4) if self.win_rate is not None else None,
            "avg_net_pnl":   round(self.avg_net_pnl, 6) if self.avg_net_pnl is not None else None,
            "median_net_pnl":round(self.median_net_pnl, 6) if self.median_net_pnl is not None else None,
            "expectancy":    round(self.expectancy, 6) if self.expectancy is not None else None,
            "max_drawdown":  round(self.max_drawdown, 6) if self.max_drawdown is not None else None,
            "action":        self.governance_action().value,
        }


# ─── EV Bucket Registry ────────────────────────────────────────────────────────

class EVBucketRegistry:
    """
    EV Bucket 治理注册表。

    核心约束（写死）：
      1. 只接受 closed trade（record.is_closed == True）
      2. 使用 record.attribution.net_pnl（不用 gross_pnl）
      3. 低样本不 BLOCK
    """

    def __init__(self) -> None:
        self._buckets: Dict[BucketKey, BucketStats] = {}

    def update(self, record: TradeRecord) -> BucketKey:
        """
        用一条已验证的 TradeRecord 更新对应 bucket。

        约束：
          - record 必须是 closed trade（exit_price 非 None）
          - 使用 net_pnl，不使用 gross_pnl

        Raises:
            ValueError: 若 record 未关闭（open trade 不得更新 EV）
        """
        if not record.is_closed:
            raise ValueError(
                f"EV Bucket only accepts closed trades; "
                f"trade_id={record.trade_id!r} is still open (exit_price=None)"
            )

        key = BucketKey.from_record(record)
        if key not in self._buckets:
            self._buckets[key] = BucketStats(key=key)

        # 强制使用 net_pnl — 不可绕过
        self._buckets[key].net_pnls.append(record.attribution.net_pnl)
        return key

    def update_from_ledger(self, records: List[TradeRecord]) -> Tuple[int, int]:
        """
        批量从 TradeLedger 记录更新。
        返回 (accepted, skipped_open)。
        """
        accepted = skipped = 0
        for r in records:
            if r.is_closed:
                self.update(r)
                accepted += 1
            else:
                skipped += 1
        return accepted, skipped

    def get(self, key: BucketKey) -> Optional[BucketStats]:
        return self._buckets.get(key)

    def action_for(self, key: BucketKey) -> BucketAction:
        """查询指定 bucket 的治理动作；未知 bucket 默认 ALLOW。"""
        stats = self._buckets.get(key)
        return stats.governance_action() if stats else BucketAction.ALLOW

    def all_stats(self) -> List[BucketStats]:
        return list(self._buckets.values())

    def blocked_buckets(self) -> List[BucketStats]:
        return [s for s in self._buckets.values()
                if s.governance_action() == BucketAction.BLOCK]

    def summary(self) -> dict:
        total = len(self._buckets)
        actions: Dict[str, int] = {}
        for s in self._buckets.values():
            a = s.governance_action().value
            actions[a] = actions.get(a, 0) + 1
        return {"total_buckets": total, "actions": actions}
