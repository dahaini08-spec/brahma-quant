"""Path settlement that does not rewrite the original signal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from brahma_os.contracts import Outcome

Bar = tuple[float, float, float, float]  # ts, high, low, close
Priority = Literal["stop", "target", "skip"]


@dataclass(frozen=True)
class SettlementRule:
    same_bar_priority: Priority = "stop"
    ttl_hours: float = 24.0


@dataclass(frozen=True)
class SettlementResult:
    outcome: Outcome
    exit_ts: float
    exit_price: float
    bars_held: int
    note: str


class SettlementEngine:
    def __init__(self, rule: SettlementRule | None = None) -> None:
        self.rule = rule or SettlementRule()

    def settle(
        self,
        side: str,
        entry_ts: float,
        entry: float,
        stop: float,
        target: float,
        bars: Iterable[Bar],
    ) -> SettlementResult | None:
        """Evaluate bars strictly after entry_ts. Never mutates entry/stop/target."""
        held = 0
        last: Bar | None = None
        deadline = entry_ts + self.rule.ttl_hours * 3600.0
        for ts, high, low, close in bars:
            if ts <= entry_ts:
                continue
            held += 1
            last = (ts, high, low, close)
            hit_stop = (low <= stop) if side == "LONG" else (high >= stop)
            hit_tgt = (high >= target) if side == "LONG" else (low <= target)
            if hit_stop and hit_tgt:
                if self.rule.same_bar_priority == "skip":
                    continue
                if self.rule.same_bar_priority == "stop":
                    return SettlementResult("LOSS", ts, stop, held, "same_bar_stop_priority")
                return SettlementResult("WIN", ts, target, held, "same_bar_target_priority")
            if hit_stop:
                return SettlementResult("LOSS", ts, stop, held, "stop")
            if hit_tgt:
                return SettlementResult("WIN", ts, target, held, "target")
            if ts >= deadline:
                return SettlementResult("TIMEOUT", ts, close, held, "ttl")
        if last and last[0] >= deadline:
            return SettlementResult("TIMEOUT", last[0], last[3], held, "ttl_last_bar")
        return None
