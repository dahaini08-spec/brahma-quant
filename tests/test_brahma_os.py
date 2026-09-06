from brahma_os.config import Settings
from brahma_os.contracts import Fill, Signal
from brahma_os.costs import CostModel
from brahma_os.gates import evaluate_gates
from brahma_os.ledger import EquityLedger
from brahma_os.settlement import SettlementEngine, SettlementRule


def _sig(**kw) -> Signal:
    base = dict(
        signal_id="s1",
        ts=1_000.0,
        symbol="BTCUSDT",
        side="LONG",
        regime="BULL_EARLY",
        score=150,
        grade=85,
        entry_lo=100.0,
        entry_hi=101.0,
        stop=97.0,
        target=106.0,
        valid_until=10_000.0,
    )
    base.update(kw)
    return Signal(**base)


def test_dead_hole_blocks_bear_long():
    d = evaluate_gates(
        _sig(regime="BEAR_TREND", side="LONG", stop=97, entry_lo=100, entry_hi=101, target=106),
        Settings(),
        open_positions=0,
        symbol_exposure=0.0,
        gross_exposure=0.0,
    )
    assert d.allow is False and d.code == "DEAD_HOLE"


def test_pass_geometry():
    d = evaluate_gates(
        _sig(),
        Settings(),
        open_positions=0,
        symbol_exposure=0.0,
        gross_exposure=0.0,
    )
    assert d.allow is True


def test_settlement_does_not_rewrite_and_stop_wins_same_bar():
    eng = SettlementEngine(SettlementRule(same_bar_priority="stop"))
    bars = [(1_060.0, 107.0, 96.5, 102.0)]
    r = eng.settle("LONG", 1_000.0, 100.5, 97.0, 106.0, bars)
    assert r is not None
    assert r.outcome == "LOSS"
    assert r.exit_price == 97.0


def test_settlement_ignores_bars_at_or_before_entry():
    eng = SettlementEngine()
    bars = [(1_000.0, 110.0, 90.0, 100.0), (1_100.0, 106.5, 99.0, 106.2)]
    r = eng.settle("LONG", 1_000.0, 100.5, 97.0, 106.0, bars)
    assert r is not None and r.outcome == "WIN"


def test_ledger_costs_reduce_nav():
    settings = Settings(start_nav=10_000)
    cost = CostModel(settings)
    led = EquityLedger(settings, cost)
    entry = Fill(
        fill_id="e1",
        intent_id="i1",
        signal_id="s1",
        ts=1_000.0,
        symbol="BTCUSDT",
        side="LONG",
        role="ENTRY",
        qty=1.0,
        price=100.0,
        fee=0.4,
        slippage=0.3,
    )
    led.apply_entry(entry, stop=97.0, target=106.0)
    exit_ = Fill(
        fill_id="x1",
        intent_id="i1",
        signal_id="s1",
        ts=1_000.0 + 8 * 3600,
        symbol="BTCUSDT",
        side="LONG",
        role="TARGET",
        qty=1.0,
        price=106.0,
        fee=0.4,
        slippage=0.3,
    )
    pos = led.apply_exit(entry.fill_id, exit_, hours_held=8.0, outcome="WIN")
    assert pos.realized < 6.0  # 6 gross minus fees/slip/funding
    assert led.snapshot().wins == 1
    assert led.nav() < 10_006.0
