# Brahma research protocol

Patching `wr_matrix_*.json` is not research. A number is allowed to change a live gate only if it survives this stack.

## Stack

1. Frozen `Signal` (entry/stop/target never rewritten).
2. Path fill: limit is filled only if the bar trades through the zone.
3. `SettlementEngine` on later bars. Same-bar stop/target conflict defaults to stop.
4. `EquityLedger` after fee, slippage, funding.
5. Monte Carlo worlds: new GBM seeds x cost shock x same-bar law.
6. `verdict()` — promote is a boolean with a fail list.

## What is not evidence

- One historical path.
- WR on unsettled or MODE_B-rewritten signals.
- Sharpe from per-trade returns x sqrt(365).
- LLM council text.
- n < 80 closed, costed trades.

## Promotion bar

Default Settings plus MC:

- worlds >= 50
- nav p50 >= start
- nav p05 >= 85% start
- dd p95 <= 1.5 x 12%
- ruin (NAV <= 70% start) <= 5%
- mean EV after costs > 0

Fail any one -> stay on paper.
