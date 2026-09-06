"""Map the messy analyze() dict onto a frozen Signal. Refuse to invent prices."""

from __future__ import annotations

import time
from typing import Any

from brahma_os.contracts import Signal


class AdapterError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


_GRADE_LABEL = {
    "神级": 90.0,
    "极强": 80.0,
    "强": 70.0,
    "中": 55.0,
    "弱": 40.0,
}


def _first(raw: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    nested = raw.get("params") or raw.get("trade") or raw.get("trade_params") or {}
    if isinstance(nested, dict):
        for k in keys:
            if k in nested and nested[k] not in (None, ""):
                return nested[k]
    return default


def _num(val: Any, default: float | None = None) -> float | None:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def parse_grade(raw: dict) -> float:
    for key in ("grade_num", "effective_grade", "structure_grade", "grade"):
        val = _first(raw, key)
        if val is None:
            continue
        n = _num(val)
        if n is not None:
            return n
        text = str(val)
        for label, score in _GRADE_LABEL.items():
            if label in text:
                return score
        digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
        n = _num(digits)
        if n is not None:
            return n
    return 0.0


def parse_side(raw: dict) -> str:
    side = str(_first(raw, "side", "direction", "signal_dir", default="") or "").upper()
    if side in ("LONG", "BUY"):
        return "LONG"
    if side in ("SHORT", "SELL"):
        return "SHORT"
    raise AdapterError("SIDE", "analyze() missing direction/side")


def analyze_to_signal(
    raw: dict,
    *,
    symbol: str | None = None,
    now_ts: float | None = None,
    ttl_hours: float = 24.0,
) -> Signal:
    if not isinstance(raw, dict):
        raise AdapterError("TYPE", "analyze() did not return a dict")
    if raw.get("blocked") is True:
        raise AdapterError("BLOCKED", str(raw.get("block_reason") or "analyze blocked"))

    sym = str(symbol or _first(raw, "symbol", default="") or "").upper()
    if not sym:
        raise AdapterError("SYMBOL", "analyze() missing symbol")

    side = parse_side(raw)
    score = _num(_first(raw, "score_final", "score"), 0.0) or 0.0
    regime = str(_first(raw, "regime", default="UNKNOWN") or "UNKNOWN")
    grade = parse_grade(raw)

    price = _num(_first(raw, "price", "last", "close"))
    entry_lo = _num(_first(raw, "entry_lo", "entry_low", "zone_lo"))
    entry_hi = _num(_first(raw, "entry_hi", "entry_high", "zone_hi"))
    if entry_lo is None and entry_hi is None and price:
        raise AdapterError("ENTRY", "analyze() has price but no entry zone")
    if entry_lo is None:
        entry_lo = entry_hi if entry_hi is not None else price
    if entry_hi is None:
        entry_hi = entry_lo if entry_lo is not None else price
    if entry_lo is None or entry_hi is None:
        raise AdapterError("ENTRY", "analyze() missing entry_lo/entry_hi")
    if entry_lo > entry_hi:
        entry_lo, entry_hi = entry_hi, entry_lo

    stop = _num(_first(raw, "stop", "stop_loss", "sl", "sl_price"))
    target = _num(_first(raw, "target", "tp1", "tp", "take_profit"))
    if stop is None or target is None:
        raise AdapterError("EXITS", "analyze() missing stop or tp1")

    ts = _num(_first(raw, "ts", "timestamp"), now_ts if now_ts is not None else time.time()) or time.time()
    valid_until = _num(_first(raw, "valid_until"))
    if valid_until is None:
        valid_until = ts + ttl_hours * 3600.0

    sid = str(_first(raw, "signal_id", "id", default=f"{sym}-{int(ts)}-{side}"))
    return Signal(
        signal_id=sid,
        ts=float(ts),
        symbol=sym,
        side=side,  # type: ignore[arg-type]
        regime=regime,
        score=float(score),
        grade=float(grade),
        entry_lo=float(entry_lo),
        entry_hi=float(entry_hi),
        stop=float(stop),
        target=float(target),
        valid_until=float(valid_until),
        source=str(_first(raw, "source", default="analyze")),
        extras={
            "blocked": bool(raw.get("blocked", False)),
            "rr_raw": _num(_first(raw, "rr1", "rr")),
        },
    )
