# Cutover — do not patch main

Live remains off. Work stays on `refactor/brahma-os-v7` until verdict() is green on recorded signals.

## Replace, do not wrap

| Retire on main | Use instead |
|---|---|
| paper_engine dual-side 100x | paper_bridge + Settings 5x |
| signal_settler MODE_B | SettlementEngine |
| paper last-price settle | replay_signals(klines) |
| wr_matrix_live + realtime | EquityLedger.snapshot |
| auto_execute_gate private MIN_SCORE | evaluate_gates |
| README DSR/WR | verdict().summary |

## Sequence

1. Keep production on paper/testnet.
2. Snapshot analyze() to frozen Signal jsonl (no rewrite).
3. Replay those signals on 5m klines.
4. run_worlds on the costed equity path.
5. verdict() true + 80 closed / 30 days paper before testnet size-up.
