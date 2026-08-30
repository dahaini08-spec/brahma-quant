# QuantConnect 独立验证梵天WR矩阵 — 最小落地方案
**设计院 2026-08-30 苏摩111**

---

## 一、验证目标

用 QuantConnect LEAN 事件流对以下两条梵天核心铁证做独立复现：

| 梵天铁证 | 当前n | 当前WR | 验证必要性 |
|---------|-------|--------|----------|
| BEAR_RECOVERY:LONG | 9 | 100% | n<15小样本，需第三方验证 |
| BULL_TREND:LONG score≥140 + SL≥3% 死亡区 | 14 | 0% | 高置信封禁，需独立确认 |

验收标准：WR差异 ±5% 以内算复现成功；>±10% 则怀疑梵天回测存在未来函数污染。

---

## 二、数据层要求

### 2.1 必须有的数据
```
BTC/ETH OHLCV（日频 + 4H）
    来源：LEAN内置 Crypto 数据 or 自导入 CSV
永续合约资金费率（8H）
    来源：Binance API 历史接口 → 导入LEAN自定义数据
OI（未平仓合约）
    来源：同上，Binance futures/data/openInterestHist
```

### 2.2 点时间（PIT）要求
- **禁止未来函数**：任何特征计算只能用 `t` 时刻之前的数据
- LEAN 自带 PIT 数据框架，开启 `SetStartDate` + `SetEndDate` 后自动处理
- 资金费率和OI作为自定义数据导入时，必须设置 `EndTime = period_end`

### 2.3 回测范围
```
起始：2020-01-01
截止：2026-06-30（排除近3个月，避免过拟合近期数据）
标的：BTCUSDT、ETHUSDT
```

---

## 三、策略层 — 梵天35维简化为LEAN Alpha模型

不需要完整复现35维。只需要复现**体制识别**和**信号触发条件**：

### 3.1 体制识别（简化版）
```python
# 梵天体制识别核心逻辑（简化）
def identify_regime(history_4h, history_1d):
    ema20 = history_4h['close'].ewm(span=20).mean()
    ema50 = history_4h['close'].ewm(span=50).mean()
    
    if ema20 > ema50 and price > ema20:
        return 'BULL_TREND'
    elif ema20 < ema50 and price < ema20:
        return 'BEAR_TREND'
    elif recent_low < prev_low * 0.95 and now_recovering:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP'
```

### 3.2 信号触发条件（简化版）
```python
# 验证铁证1：BEAR_RECOVERY:LONG
def signal_bear_recovery_long(regime, rsi_4h, score):
    return (
        regime == 'BEAR_RECOVERY'
        and signal_dir == 'LONG'
        and score >= 100  # 梵天最低入场阈值
    )

# 验证铁证2：BULL_TREND死亡区
def signal_death_zone(regime, score, sl_pct):
    return (
        regime == 'BULL_TREND'
        and signal_dir == 'LONG'
        and score >= 140
        and sl_pct >= 3.0  # SL≥3%是死亡区条件
    )
```

### 3.3 评分简化方案（3维代替35维）
LEAN回测不需要全量35维，只需要足够区分高低分：

| 梵天维度 | LEAN简化 | 权重 |
|---------|---------|------|
| 体制乘数 | EMA趋势判断 | 主要 |
| RSI状态 | talib.RSI(14) | 次要 |
| 资金费率 | Binance历史费率 | 次要 |

---

## 四、执行层 — 仓位和止损

```python
# 止损宪法（必须一致）
SL_PCT = {
    'BEAR': 2.0,
    'CHOP': 2.5,
    'BULL': 2.5,
}
POSITION_SIZE = 0.05  # 5%NAV

# 止损计算
if direction == 'LONG':
    stop_price = entry_price * (1 - SL_PCT[regime] / 100)
elif direction == 'SHORT':
    stop_price = entry_price * (1 + SL_PCT[regime] / 100)
```

**关键：** LEAN 回测必须模拟 taker 费用（Binance 永续 = 0.04% taker）

---

## 五、防泄漏检查清单

- [ ] 特征计算不超前：所有指标只用 `history[:-1]`（排除当前K线）
- [ ] 体制识别基于收盘价而非当日高低点
- [ ] 资金费率使用上一个8H窗口的费率，不用当前窗口
- [ ] OI变化用前4H的OI，不用当前4H的OI
- [ ] 止损基于开盘价入场，不基于K线最低点

---

## 六、验收标准

| 验证项目 | 梵天WR | LEAN复现WR | 判定 |
|---------|--------|-----------|------|
| BEAR_RECOVERY:LONG | 100% (n=9) | ≥95% | ✅ 铁证可信 |
| BEAR_RECOVERY:LONG | 100% (n=9) | 70-95% | ⚠️ 小样本自然波动 |
| BEAR_RECOVERY:LONG | 100% (n=9) | <70% | 🔴 怀疑未来函数 |
| BULL_TREND死亡区 | 0% (n=14) | ≤10% | ✅ 死亡区铁证可信 |
| BULL_TREND死亡区 | 0% (n=14) | >20% | 🔴 死亡区可能过拟合 |

---

## 七、实施路径（三步走）

### Step 1：数据准备（1-2天）
```bash
# 下载Binance历史资金费率
python scripts/export_funding_rate.py --symbol BTCUSDT --start 2020-01-01 --output data/lean/btc_funding.csv

# 下载OI历史
python scripts/export_oi_history.py --symbol BTCUSDT --start 2020-01-01 --output data/lean/btc_oi.csv
```

### Step 2：LEAN策略编写（2-3天）
- 用 QuantConnect Cloud 或本地 LEAN CLI
- 实现简化体制识别 + 信号触发 + 止损宪法

### Step 3：结果对比（1天）
- 导出LEAN交易日志
- 按 `regime × direction × score_bin` 分组统计WR
- 与 `data/wr_matrix_realtime.json` 对比

---

## 八、为什么现在不做

1. **梵天实盘数据不够多**：当前BEAR_RECOVERY:LONG n=9，LEAN回测需要更长历史才有统计意义
2. **复现成本高**：把35维简化成3维会引入误差，需要小心设计
3. **当前优先级**：提升实盘信号质量 > 外部验证

**建议：等n≥30后启动QuantConnect验证，届时样本量足够支撑统计显著性检验。**

---

*文档版本：v1.0 | 设计院 2026-08-30*
