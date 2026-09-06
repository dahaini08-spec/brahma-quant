"""Path-dependent paper/research simulator. One law: settle bars, cost the ledger."""

from __future__ import annotations

from dataclasses import dataclass, field

from brahma_os.config import Settings
from brahma_os.contracts import Fill, Signal
from brahma_os.costs import CostModel
from brahma_os.gates import evaluate_gates
from brahma_os.ledger import EquityLedger, LedgerSnapshot
from brahma_os.paths import Bar
from brahma_os.settlement import SettlementEngine, SettlementRule


@dataclass
class SimTrade:
    signal_id: str
    symbol: str
    side: str
    outcome: str
    entry_ts: float
    exit_ts: float
    entry: float
    exit: float
    qty: float
    pnl: float
    note: str


@dataclass
class SimReport:
    snapshot: LedgerSnapshot
    trades: list[SimTrade] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    filled: int = 0
    never_filled: int = 0


class PathSimulator:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.cost = CostModel(self.settings)
        rule = SettlementRule(
            same_bar_priority=self.settings.same_bar_priority,  # type: ignore[arg-type]
            ttl_hours=self.settings.signal_ttl_hours,
        )
        self.settler = SettlementEngine(rule)

    def run(self, signals: list[Signal], path: list[Bar], *, symbol: str | None = None) -> SimReport:
        led = EquityLedger(self.settings, self.cost)
        trades: list[SimTrade] = []
        skipped: list[tuple[str, str]] = []
        filled_n = 0
        pending: list[Signal] = sorted(signals, key=lambda s: s.ts)
        active: dict[str, tuple[Signal, Fill, float]] = {}
        mark_sym = bar_symbol(signals, symbol)

        for bar in path:
            led.mark(bar.ts, {mark_sym: bar.close})
            still: list[Signal] = []
            for sig in pending:
                if symbol and sig.symbol != symbol:
                    still.append(sig)
                    continue
                if bar.ts < sig.ts:
                    still.append(sig)
                    continue
                if bar.ts > sig.valid_until:
                    skipped.append((sig.signal_id, "EXPIRED_ON_PATH"))
                    continue
                if len(active) >= self.settings.max_open_positions:
                    still.append(sig)
                    continue
                if not _touched_entry(sig, bar):
                    still.append(sig)
                    continue
                gate = evaluate_gates(
                    sig,
                    self.settings,
                    open_positions=len(active),
                    symbol_exposure=0.0,
                    gross_exposure=0.0,
                    now_ts=bar.ts,
                )
                if not gate.allow:
                    skipped.append((sig.signal_id, gate.code))
                    continue
                px = _fill_price(sig, bar)
                qty = _qty(sig, self.settings, led.nav())
                if qty <= 0:
                    skipped.append((sig.signal_id, "SIZE"))
                    continue
                notional = qty * px
                fill = Fill(
                    fill_id=f"e-{sig.signal_id}",
                    intent_id=f"i-{sig.signal_id}",
                    signal_id=sig.signal_id,
                    ts=bar.ts,
                    symbol=sig.symbol,
                    side=sig.side,
                    role="ENTRY",
                    qty=qty,
                    price=px,
                    fee=self.cost.fee(notional, taker=True),
                    slippage=self.cost.slip(notional),
                    source="sim",
                )
                led.apply_entry(fill, stop=sig.stop, target=sig.target)
                active[sig.signal_id] = (sig, fill, bar.ts)
                filled_n += 1
            pending = still

            done = []
            for sid, (sig, fill, entry_ts) in active.items():
                res = self.settler.settle(
                    sig.side,
                    entry_ts,
                    fill.price,
                    sig.stop,
                    sig.target,
                    [bar.as_settle()],
                )
                if res is None:
                    continue
                hours = max((res.exit_ts - entry_ts) / 3600.0, 0.0)
                role = "STOP" if res.outcome == "LOSS" else "TARGET" if res.outcome == "WIN" else "TIMEOUT"
                xfill = Fill(
                    fill_id=f"x-{sid}",
                    intent_id=fill.intent_id,
                    signal_id=sid,
                    ts=res.exit_ts,
                    symbol=sig.symbol,
                    side=sig.side,
                    role=role,  # type: ignore[arg-type]
                    qty=fill.qty,
                    price=res.exit_price,
                    fee=self.cost.fee(fill.qty * res.exit_price, taker=True),
                    slippage=self.cost.slip(fill.qty * res.exit_price),
                    source="sim",
                )
                pos = led.apply_exit(fill.fill_id, xfill, hours_held=hours, outcome=res.outcome)
                trades.append(
                    SimTrade(
                        signal_id=sid,
                        symbol=sig.symbol,
                        side=sig.side,
                        outcome=res.outcome,
                        entry_ts=entry_ts,
                        exit_ts=res.exit_ts,
                        entry=fill.price,
                        exit=res.exit_price,
                        qty=fill.qty,
                        pnl=pos.realized,
                        note=res.note,
                    )
                )
                done.append(sid)
            for sid in done:
                active.pop(sid, None)

        if path:
            led.mark(path[-1].ts, {path_symbol(signals, symbol): path[-1].close})
        return SimReport(
            snapshot=led.snapshot(),
            trades=trades,
            skipped=skipped,
            filled=filled_n,
            never_filled=len(pending),
        )


def bar_symbol(signals: list[Signal], symbol: str | None) -> str:
    if symbol:
        return symbol
    return signals[0].symbol if signals else "BTCUSDT"


def path_symbol(signals: list[Signal], symbol: str | None) -> str:
    return bar_symbol(signals, symbol)


def _touched_entry(sig: Signal, bar: Bar) -> bool:
    if sig.side == "LONG":
        return bar.low <= sig.entry_hi and bar.high >= sig.entry_lo
    return bar.high >= sig.entry_lo and bar.low <= sig.entry_hi


def _fill_price(sig: Signal, bar: Bar) -> float:
    mid = sig.entry_mid
    return min(max(mid, bar.low), bar.high)


def _qty(sig: Signal, settings: Settings, nav: float) -> float:
    px = sig.entry_mid
    if px <= 0 or nav <= 0:
        return 0.0
    margin = nav * min(settings.max_symbol_weight, 0.12)
    return (margin * min(settings.max_leverage, 5.0)) / px
