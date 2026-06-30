# 梵天大脑字段契约 v1.0
<!-- 设计院 · 2026-05-28 -->

> **规则：消费方代码（lana_scan / brahma_post / story_post / news_formatter）读取梵天大脑输出时，必须遵循此契约。**
> 新增消费方先查这里，不得自行猜测字段路径。

## 顶层字段（`r = bb.analyze()`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `r['symbol']` | str | 'ETHUSDT' |
| `r['price']` | float | 当前价 |
| `r['signal_dir']` | str | **'SHORT' / 'LONG' / 'NEUTRAL'** （英文，不是中文！）|
| `r['regime']` | str | 体制：BEAR_TREND / CHOP_MID / BULL_TREND 等 |
| `r['valid_signal']` | bool | 是否可执行（冷却期内=False）|
| `r['score_final']` | int | 最终评分（含所有加成，与 confluence.total 相同）|
| `r['summary']` | str | 简要摘要文字 |

## params 子字段（`p = r['params']`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `p['entry_lo']` | float | 入场区间低位 |
| `p['entry_hi']` | float | 入场区间高位 |
| `p['stop_loss']` | float | **止损价（注意：是stop_loss不是stop！）** |
| `p['tp1']` | float | 目标一 |
| `p['tp2']` | float | 目标二 |
| `p['rr1']` | float | R:R 比率 |
| `p['sl_pct']` | float | 止损百分比 |
| `p['valid']` | bool | 点位结构是否有效 |
| `p['sl_price_dyn']` | float | 动态止损价（ATR计算）|

## confluence 子字段（`c = r['confluence']`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `c['total']` | int | 总评分（同 score_final）|
| `c['action']` | str | 'ENTER_FULL' / 'ENTER_HALF' / 'WAIT' |
| `c['rr_gate']` | str | 'PASS' / 'FAIL' |
| `c['breakdown']` | dict | 各子项评分明细 |

## sentiment 子字段（`sent = r['sentiment']`）

> ⚠️ **正确路径是 `r['sentiment']`，不是 `extra['market_state']`（已废弃）**

| 字段 | 类型 | 说明 |
|------|------|------|
| `sent['long_short_ratio']` | float | 散户多头% （如 77.8 = 77.8%）|
| `sent['funding_rate']` | float | 资金费率 |
| `sent['oi_momentum']` | str | 'NEUTRAL' / 'INCREASING' / 'DECREASING' |

## extra 常用子字段

| 路径 | 类型 | 说明 |
|------|------|------|
| `extra['multitf']` | dict | 多周期共识数据 |
| `extra['dynamic_sl']` | dict | 动态止损详情 |
| `extra['onchain']['raw']['ls']['long_pct']` | float | 链上散户多头（与r.sentiment一致）|
| `extra['macro']['raw']['fear_greed']` | dict | F&G数据 |

## ❌ 废弃字段（禁止使用）

| 废弃路径 | 原正确路径 | 废弃原因 |
|---------|-----------|--------|
| `extra['market_state']` | `r['sentiment']` | 字段已从大脑移除，值为None |
| `extra['market_state']['sentiment']['ls_ratio']` | `r['sentiment']['long_short_ratio']` | 同上 |
| `params['stop']` | `params['stop_loss']` | 字段名历史遗留，实际是stop_loss |

## 常见错误模式

```python
# ❌ 错误（market_state已废弃）
ms = extra.get('market_state', {})
ls = ms.get('sentiment', {}).get('ls_ratio', 50)

# ✅ 正确
sent = r.get('sentiment', {}) or {}
ls = float(sent.get('long_short_ratio', 50))

# ❌ 错误（stop字段名）
stop = p.get('stop', 0)

# ✅ 正确
stop = p.get('stop_loss', 0)
```
