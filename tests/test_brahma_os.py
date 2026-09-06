from brahma_os.adapter import AdapterError, analyze_to_signal
from brahma_os.config import Settings
from brahma_os.contracts import Fill, Signal
from brahma_os.costs import CostModel
from brahma_os.gates import evaluate_gates
from brahma_os.ledger import EquityLedger
from brahma_os.paper_bridge import decide_from_analyze
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
    assert pos.realized < 6.0
    assert led.snapshot().wins == 1
    assert led.nav() < 10_006.0


def test_adapter_reads_aliases_and_nested_params():
    raw = {
        "symbol": "ETHUSDT",
        "signal_dir": "BUY",
        "score_final": 151,
        "grade": "橙色极强",
        "regime": "BULL_EARLY",
        "params": {"entry_lo": 2400, "entry_hi": 2410, "stop_loss": 2350, "tp1": 2500},
        "ts": 1_700.0,
    }
    sig = analyze_to_signal(raw, ttl_hours=24)
    assert sig.side == "LONG"
    assert sig.grade == 80.0
    assert sig.stop == 2350
    assert sig.target == 2500
    assert sig.entry_lo == 2400


def test_adapter_refuses_price_only():
    raw = {"symbol": "BTCUSDT", "direction": "LONG", "score": 160, "price": 100, "grade_num": 90}
    try:
        analyze_to_signal(raw)
    except AdapterError as exc:
        assert exc.code == "ENTRY"
    else:
        raise AssertionError("expected AdapterError")


def test_paper_bridge_blocks_100x_style_and_opens_one_side():
    raw = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "score": 150,
        "grade_num": 85,
        "regime": "BULL_EARLY",
        "entry_lo": 100,
        "entry_hi": 101,
        "stop": 97,
        "tp1": 106,
        "ts": 1_000,
    }
    settings = Settings(start_nav=10_000, max_leverage=5, max_symbol_weight=0.12)
    d = decide_from_analyze(
        raw,
        settings,
        nav=10_000,
        open_positions=0,
        symbol_exposure=0.0,
        gross_exposure=0.0,
        now_ts=1_000,
    )
    assert d.allow is True
    assert d.signal is not None and d.signal.side == "LONG"
    assert d.leverage <= 5
    assert d.margin == 1_200.0
    assert d.notional == 6_000.0


def test_paper_bridge_dead_hole():
    raw = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "score": 160,
        "grade_num": 90,
        "regime": "BEAR_TREND",
        "entry_lo": 100,
        "entry_hi": 101,
        "stop": 97,
        "tp1": 106,
        "ts": 1_000,
    }
    d = decide_from_analyze(
        raw,
        Settings(),
        nav=10_000,
        open_positions=0,
        symbol_exposure=0.0,
        gross_exposure=0.0,
        now_ts=1_000,
    )
    assert d.allow is False and d.code == "DEAD_HOLE"
