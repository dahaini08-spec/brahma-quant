"""
brahma_v6/ops/dlq.py — DeadLetterQueue: push/drain/count with persistence to data/dlq.jsonl
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parents[3]
DLQ_FILE = BASE / "data" / "dlq.jsonl"


class DeadLetterQueue:
    """
    Thread-safe dead letter queue.
    Events that fail processing are pushed here for inspection and replay.
    Persists to data/dlq.jsonl.
    """

    def __init__(self, persist: bool = True, dlq_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._queue: List[Dict] = []
        self._persist = persist
        self._dlq_path = dlq_path or DLQ_FILE
        if self._persist:
            self._dlq_path.parent.mkdir(parents=True, exist_ok=True)

    def push(self, event: Any, reason: str = "") -> None:
        """Push an event to the DLQ."""
        entry = {
            "ts": time.time(),
            "reason": reason,
            "event": event if isinstance(event, dict) else str(event),
        }
        with self._lock:
            self._queue.append(entry)

        if self._persist:
            try:
                with open(self._dlq_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass

    def drain(self) -> List[Dict]:
        """Remove and return all events from the queue."""
        with self._lock:
            events = list(self._queue)
            self._queue.clear()
        return events

    def count(self) -> int:
        """Return current queue depth."""
        with self._lock:
            return len(self._queue)

    def peek(self) -> List[Dict]:
        """Return all events without removing."""
        with self._lock:
            return list(self._queue)

    def clear(self) -> None:
        """Clear in-memory queue (does not delete persisted file)."""
        with self._lock:
            self._queue.clear()
