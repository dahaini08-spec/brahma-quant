from brahma_os.config import Settings
from brahma_os.contracts import Signal
from brahma_os.replay import klines_to_bars, replay_signals


def test_ms_timestamp_converted():
    rows = [[1_700_000_000_000, 100, 100.2, 99.8, 100.1]]
    bars = klines_to_bars(rows)
    assert bars[0].ts == 1_700_000_000.0


def test_replay_hits_target_on_recorded_path():
    ts0 = 1_000.0
    klines = []
    px = 100.0
    for i in range(40):
        nxt = px + 0.25
        klines.append([ts0 + i * 300, px, nxt + 0.05, px - 0.05, nxt])
        px = nxt
    sig = Signal(
        signal_id="r1",
        ts=ts0,
        symbol="BTCUSDT",
        side="LONG",
        regime="BULL_EARLY",
        score=150,
        grade=85,
        entry_lo=99.6,
        entry_hi=100.4,
        stop=97.0,
        target=106.0,
        valid_until=ts0 + 86_400,
    )
    report = replay_signals([sig], klines, settings=Settings())
    assert report.filled == 1
    assert report.trades[0].outcome == "WIN"
