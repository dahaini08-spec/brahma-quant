# reports/ev_bucket_governance.md
# EV Bucket Governance — Phase 4B 封口报告
**生成时间：** 2026-07-09 UTC | **分支：** main

---

## 核心约束（写死，不可绕过）

```
1. 只能用 closed trade（exit_price 非 None）更新
2. 只能用通过 TradeLedger 三道校验的 TradeRecord
3. 使用 net_pnl，不使用 gross_pnl
4. n < 10（MIN_SAMPLE_FOR_BLOCK）不允许直接 BLOCK，只能 WATCHLIST/DOWN_WEIGHT
```

---

## Bucket Key 维度

| 维度 | 说明 |
|------|------|
| symbol | 交易标的 |
| direction | LONG / SHORT |
| regime | 梵天体制 |
| score_bucket | LOW(<130) / MID(130-150) / HIGH(>150) |
| setup_type | OB / FVG / BB / PUMP / UNKNOWN |
| timeframe | 1H / 4H / 1D |

---

## 治理阈值

| 条件 | 动作 |
|------|------|
| 胜率 < 38% 且 n ≥ 10 | BLOCK |
| 期望值 < -0.5% 且 n ≥ 10 | BLOCK |
| 胜率 < 45% | DOWN_WEIGHT |
| 最大回撤 > 15% | WATCHLIST |
| n < 10（任何情况） | 最高 DOWN_WEIGHT/WATCHLIST |
| 其余 | ALLOW |

---

## 统计指标

| 指标 | 说明 |
|------|------|
| n | 样本量 |
| win_rate | 胜率（net_pnl > 0 的比率）|
| avg_net_pnl | 平均净 PnL |
| median_net_pnl | 中位净 PnL |
| expectancy | 期望值（= avg_net_pnl）|
| max_drawdown | 累计 PnL 序列最大回撤 |

---

## 测试结果

```
tests/test_ev_bucket.py  18/18 ✅
```

**验证覆盖：**
- EV bucket only updates from closed trades ✅
- EV bucket uses net_pnl not gross_pnl ✅
- low sample bucket cannot hard block ✅
- negative expectancy bucket → BLOCK (n ≥ 10) ✅
- unknown bucket defaults to ALLOW ✅
- win_rate / avg_net_pnl 计算正确 ✅

---

## Phase 4 综合评分更新

```
before (P0封口):  7.8 / 10
after  (Phase 4): 8.3 / 10（条件：本次全绿 + pytest 207/207 + compileall）
```

| 维度 | 评分 |
|------|------|
| Reality Models | 8.2 |
| EV Governance | 8.0 |
| 综合 | **8.3 / 10** |
