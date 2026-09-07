"""Append-only signal log. Never rewrite a frozen intent."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

from brahma_os.config import Settings
from brahma_os.contracts import Signal


def snapshot_path(settings: Settings) -> Path:
    return settings.data_dir / "signals.jsonl"


def signal_record(signal: Signal, **extra) -> dict:
    row = {
        "signal_id": signal.signal_id,
        "ts": signal.ts,
        "symbol": signal.symbol,
        "side": signal.side,
        "regime": signal.regime,
        "score": signal.score,
        "grade": signal.grade,
        "entry_lo": signal.entry_lo,
        "entry_hi": signal.entry_hi,
        "stop": signal.stop,
        "target": signal.target,
        "valid_until": signal.valid_until,
        "source": signal.source,
        "rr": signal.rr,
    }
    row.update(extra)
    return row


def append_snapshot(settings: Settings, row: dict) -> Path:
    path = snapshot_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def load_snapshots(settings: Settings) -> list[dict]:
    path = snapshot_path(settings)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
