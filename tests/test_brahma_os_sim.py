from brahma_os.config import Settings
from brahma_os.contracts import Signal
from brahma_os.monte_carlo import run_worlds
from brahma_os.paths import trend_path
from brahma_os.research import verdict
from brahma_os.simulator import PathSimulator


def _long(ts=1_000.0, **kw) -> Signal:
    base = dict(
        signal_id="s1",
        ts=ts,
        symbol="BTCUSDT",
        side="LONG",
        regime="BULL_EARLY",
        score=150,
        grade=85,
        entry_lo=99.6,
        entry_hi=100.4,
        stop=97.0,
        target=106.0,
        valid_until=ts + 24 * 3600,
    )
    base.update(kw)
    return Signal(**base)


def test_uptrend_long_takes_target():
    path = trend_path(seed=1, side="UP", start_px=100.0, n_bars=80, start_ts=1_000.0, step=0.2)
    report = PathSimulator(Settings()).run([_long()], path)
    assert report.filled == 1
    assert report.trades
    assert report.trades[0].outcome == "WIN"
    assert report.snapshot.nav > 10_000


def test_downtrend_long_takes_stop():
    path = trend_path(seed=2, side="DOWN", start_px=100.0, n_bars=80, start_ts=1_000.0, step=0.2)
    report = PathSimulator(Settings()).run([_long()], path)
    assert report.filled == 1
    assert report.trades[0].outcome == "LOSS"
    assert report.snapshot.nav < 10_000


def test_dead_hole_never_fills():
    path = trend_path(seed=3, side="UP", start_px=100.0, n_bars=40, start_ts=1_000.0)
    sig = _long(regime="BEAR_TREND")
    report = PathSimulator(Settings()).run([sig], path)
    assert report.filled == 0
    assert any(code == "DEAD_HOLE" for _, code in report.skipped)


def test_cost_shock_hurts_edge():
    path = trend_path(seed=4, side="UP", start_px=100.0, n_bars=80, start_ts=1_000.0, step=0.2)
    cheap = PathSimulator(Settings()).run([_long()], path)
    rich = PathSimulator(
        Settings(taker_fee_bps=20.0, slippage_bps=20.0, funding_default_bps_8h=10.0)
    ).run([_long()], path)
    assert cheap.trades and rich.trades
    assert rich.trades[0].pnl < cheap.trades[0].pnl


def test_monte_carlo_random_entries_do_not_promote():
    signals = [_long(ts=1_000.0 + i * 3_600, signal_id=f"s{i}") for i in range(8)]
    mc = run_worlds(
        signals,
        settings=Settings(start_nav=10_000),
        n_worlds=12,
        n_bars=400,
        base_seed=11,
        cost_mults=(2.0,),
        same_bars=("stop",),
    )
    v = verdict(mc, Settings())
    assert mc.worlds == 12
    assert "nav_p05" in mc.as_dict()
    assert v.promote is False
