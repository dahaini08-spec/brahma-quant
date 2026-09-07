# Brahma OS v7

Replace the current multi-ledger organism with one spine.

## Law

1. One config (`brahma_os.config.Settings`).
2. One gate (`evaluate_gates`).
3. One ledger (`EquityLedger`).
4. Settlement never mutates the original signal.
5. PnL is always net of fee, slippage, funding.
6. Live default is off. Paper / testnet first.
7. Promotion requires n>=80, 30 days, costed EV>0, DD cap.

## Do not do on main

- Do not point auto_executor at this package until paper replay is green.
- Do not delete brahma_core.py in the first merge. Adapter only.

## Cutover

Phase A: this package + tests.
Phase B: adapter from analyze() -> Signal.
Phase C: paper engine reads Signal + ledger only.
Phase D: freeze live behind Settings.env == live and 5x leverage.
