# reports/full_auto_live_readiness.md
# Brahma v6.1 Full-Auto Live Lite — Readiness Report

```yaml
full_auto_live_lite_approved: true
unrestricted_full_auto_live_approved: false

current_mode: FULL_AUTO_LIVE_LITE

parameters:
  symbol: ETHUSDT
  leverage: 5
  order_type: LIMIT_ONLY
  max_trades_per_day: 20
  daily_loss_stop_pct: 0.10

required_gates:
  production_gate: PASS
  ten_million_simulation: PASS
  test_order_submit: PENDING
  websocket_reconcile: PENDING
  kill_switch_drill: PENDING
  emergency_reduce_only_drill: PENDING

blocking_issues_for_unrestricted:
  - multi_symbol_live_not_verified
  - market_order_live_not_verified
  - long_duration_live_not_verified
  - high_volatility_session_not_verified
```

## Phase 5 Build Status

| Component | Status |
|-----------|--------|
| Risk Kernel (7 guards + KillSwitch) | ✅ |
| LiveBinanceAdapter (test/live/filter/mapper) | ✅ |
| Runtime Pipeline (SignalConsumer→OrderPipeline) | ✅ |
| Ops (DLQ + Reconciler + Healthcheck) | ✅ |
| 10M Simulation (20 invariants) | ✅ |
| Production Gate scripts | ✅ |

## Degradation Ladder
```
FULL_AUTO_LIVE_LITE → NO_NEW_POSITION → REDUCE_ONLY → KILL_SWITCH → OFF
```
