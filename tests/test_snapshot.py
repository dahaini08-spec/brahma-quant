from pathlib import Path

from brahma_os.config import Settings
from brahma_os.contracts import Signal
from brahma_os.snapshot import append_snapshot, load_snapshots, signal_record


def test_append_only_does_not_rewrite(tmp_path: Path):
    st = Settings(repo_root=str(tmp_path))
    sig = Signal(
        signal_id="a1",
        ts=1.0,
        symbol="BTCUSDT",
        side="LONG",
        regime="BULL_EARLY",
        score=150,
        grade=85,
        entry_lo=100,
        entry_hi=101,
        stop=97,
        target=106,
        valid_until=10,
    )
    append_snapshot(st, signal_record(sig, gate="PASS"))
    append_snapshot(st, signal_record(sig, gate="PASS", signal_id="a2"))
    rows = load_snapshots(st)
    assert len(rows) == 2
    assert rows[0]["signal_id"] == "a1"
