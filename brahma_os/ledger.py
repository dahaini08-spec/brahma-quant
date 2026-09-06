"""Account ledger. Marks-to-market, compounding NAV, drawdown, Sharpe on daily equity."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from brahma_os.config import Settings
from brahma_os.contracts import Fill, Position, Side
from brahma_os.costs import CostModel


@dataclass
class DailyPoint:
    day: str
    nav: float
    ret: float


@dataclass
class LedgerSnapshot:
    nav: float
    peak: float
    max_drawdown: float
    n_closed: int
    wins: int
    losses: int
    timeouts: int
    wr: float
    ev_usd: float
    sharpe: float | None
    days: int


@dataclass
class EquityLedger:
    settings: Settings
    cost: CostModel
    cash: float = field(init=False)
    peak: float = field(init=False)
    max_dd: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    closed: list[Position] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)
    equity_curve: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.settings.start_nav
        self.peak = self.settings.start_nav

    def nav(self) -> float:
        upnl = 0.0
        for pos in self.positions.values():
            px = self.marks.get(pos.symbol, pos.avg_entry)
            upnl += self.cost.signed_pnl(
                pos.side, pos.qty, pos.avg_entry, px, fees=0.0, funding=0.0
            )
        return self.cash + upnl

    def mark(self, ts: float, prices: dict[str, float]) -> float:
        self.marks.update(prices)
        value = self.nav()
        self.equity_curve.append((ts, value))
        if value > self.peak:
            self.peak = value
        dd = (self.peak - value) / self.peak if self.peak else 0.0
        if dd > self.max_dd:
            self.max_dd = dd
        return value

    def apply_entry(self, fill: Fill, stop: float, target: float) -> Position:
        notional = fill.qty * fill.price
        self.cash -= fill.fee + fill.slippage
        pos = Position(
            position_id=fill.fill_id,
            signal_id=fill.signal_id,
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            avg_entry=fill.price,
            stop=stop,
            target=target,
            opened_ts=fill.ts,
            fees_paid=fill.fee + fill.slippage,
        )
        self.positions[pos.position_id] = pos
        self.marks[fill.symbol] = fill.price
        return pos

    def apply_exit(self, pos_id: str, fill: Fill, hours_held: float, outcome: str) -> Position:
        pos = self.positions.pop(pos_id)
        funding = self.cost.funding(pos.qty * pos.avg_entry, hours_held)
        fees = pos.fees_paid + fill.fee + fill.slippage
        pnl = self.cost.signed_pnl(pos.side, pos.qty, pos.avg_entry, fill.price, fees, funding)
        self.cash += pos.qty * pos.avg_entry + pnl + fees  # restore margin-equivalent + net
        # cash model: start with NAV as cash; entry only deducted fees; exit adds signed pnl
        # Correct the restore: entry did not lock notional (paper NAV model).
        self.cash -= pos.qty * pos.avg_entry
        pos.qty = 0.0
        pos.fees_paid = fees
        pos.funding_paid = funding
        pos.realized = pnl
        pos.closed_ts = fill.ts
        pos.outcome = outcome  # type: ignore[assignment]
        self.closed.append(pos)
        self.mark(fill.ts, {fill.symbol: fill.price})
        return pos

    def snapshot(self) -> LedgerSnapshot:
        closed = self.closed
        wins = sum(1 for p in closed if p.outcome == "WIN")
        losses = sum(1 for p in closed if p.outcome == "LOSS")
        timeouts = sum(1 for p in closed if p.outcome == "TIMEOUT")
        decided = wins + losses
        wr = wins / decided if decided else 0.0
        ev = sum(p.realized for p in closed) / len(closed) if closed else 0.0
        sharpe = self._sharpe()
        days = len(self._daily())
        return LedgerSnapshot(
            nav=self.nav(),
            peak=self.peak,
            max_drawdown=self.max_dd,
            n_closed=len(closed),
            wins=wins,
            losses=losses,
            timeouts=timeouts,
            wr=wr,
            ev_usd=ev,
            sharpe=sharpe,
            days=days,
        )

    def _daily(self) -> list[DailyPoint]:
        by_day: dict[str, float] = {}
        for ts, nav in self.equity_curve:
            day = __import__("datetime").datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
            by_day[day] = nav
        points: list[DailyPoint] = []
        prev = self.settings.start_nav
        for day in sorted(by_day):
            nav = by_day[day]
            ret = (nav - prev) / prev if prev else 0.0
            points.append(DailyPoint(day=day, nav=nav, ret=ret))
            prev = nav
        return points

    def _sharpe(self) -> float | None:
        pts = self._daily()
        if len(pts) < 20:
            return None
        rets = [p.ret for p in pts]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        if std <= 0:
            return None
        return (mean / std) * math.sqrt(365.0)
