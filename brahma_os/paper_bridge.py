"""Paper decision: adapter + V7 gates + conservative size. No dual-side opens."""

from __future__ import annotations

from dataclasses import dataclass

from brahma_os.adapter import AdapterError, analyze_to_signal
from brahma_os.config import Settings
from brahma_os.contracts import Signal
from brahma_os.gates import GateDecision, evaluate_gates


@dataclass(frozen=True)
class PaperDecision:
    allow: bool
    code: str
    reason: str
    signal: Signal | None
    margin: float = 0.0
    notional: float = 0.0
    qty: float = 0.0
    leverage: float = 0.0
    gate: GateDecision | None = None


def size_from_settings(signal: Signal, settings: Settings, nav: float) -> tuple[float, float, float, float]:
    weight = min(settings.max_symbol_weight, 0.12)
    lev = min(settings.max_leverage, 5.0)
    margin = max(nav, 0.0) * weight
    px = signal.entry_mid
    if px <= 0:
        return 0.0, 0.0, 0.0, lev
    notional = margin * lev
    qty = notional / px
    return margin, notional, qty, lev


def decide_from_analyze(
    raw: dict,
    settings: Settings,
    *,
    nav: float,
    open_positions: int,
    symbol_exposure: float,
    gross_exposure: float,
    breaker_on: bool = False,
    now_ts: float | None = None,
    symbol: str | None = None,
) -> PaperDecision:
    try:
        signal = analyze_to_signal(
            raw,
            symbol=symbol,
            now_ts=now_ts,
            ttl_hours=settings.signal_ttl_hours,
        )
    except AdapterError as exc:
        return PaperDecision(False, exc.code, exc.reason, None)

    gate = evaluate_gates(
        signal,
        settings,
        open_positions=open_positions,
        symbol_exposure=symbol_exposure,
        gross_exposure=gross_exposure,
        breaker_on=breaker_on,
        now_ts=now_ts if now_ts is not None else signal.ts,
    )
    if not gate.allow:
        return PaperDecision(False, gate.code, gate.reason, signal, gate=gate)

    margin, notional, qty, lev = size_from_settings(signal, settings, nav)
    if qty <= 0:
        return PaperDecision(False, "SIZE", "qty<=0", signal, gate=gate)
    return PaperDecision(
        True,
        "PASS",
        "ok",
        signal,
        margin=margin,
        notional=notional,
        qty=qty,
        leverage=lev,
        gate=gate,
    )
