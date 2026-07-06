# 🏛️ 梵天分析链路固化文档 v1.0
<!-- 设计院 + 达摩院 · 2026-07-06 深度推理封印 -->
<!-- 基于：本次全系列修复（OKX清算+extra_data+LONG反转+360修复+Phase1+2+v4.2） -->

---

## 📐 唯一入口原则

```
所有分析 → brahma_analysis_runner.run_analysis()
禁止绕过 → 直接调用analyze() / brahma_core.py
原因：runner层负责BullBonus/TimingFilter/valid重算/sw24h惩罚注入
```

---

## 🔄 完整分析链路（执行顺序）

### 阶段0 — 体制防抖（runner触发前）
```
RegimeStateMachine.update()
  MIN_UPDATE_INTERVAL = 30min（防高频体制跳动）
  last_update_ts=0修复：迁移条件 not in state OR == 0
```

### 阶段1 — brahma_core.analyze() 35+维度评分

**Step 1: 市场状态 + 体制**
```
prefetch_symbol() → 获取价格/OI/资金费率/K线
RegimeStateMachine → 体制确认（30min防抖）
CausalVerifier → 因果体制验证（P0-A，惩罚-12）
方向确认：LONG/SHORT
```

**Step 2: confluence_score() 核心评分**

| 维度 | 满分 | 说明 |
|------|------|------|
| s1 趋势一致性 | 20 | MTF多周期趋势对齐 |
| s2 关键位精确度 | 20 | OB/FVG/关键S/R |
| s3 动量背离 | 22 | RSI背离+CVD共振 |
| s4 SMC结构 | 20 | CHoCH/BOS/OB质量 |
| s5 量能验证 | 20+15 | 放量+区间结构 |
| s6 形态成熟度 | 20 | 技术形态 |
| **s7 清算/OI** | 10+15+8 | OI动量+实时清算流+OBHeatmap+**liq_density三所** |
| s8~s10 宽松维度 | 10+5+5 | BB/RSI极值/量比 |
| s11~s17 扩展维度 | 50+ | 期权/鲸鱼/L2/LSTM/宏观 |

**Step 3: 外部路由三项（s_cross/s_options/s_macro_v2）**
```
s_cross-FR+Basis  → 跨所资金费率+基差（Bybit+OKX）
s_options-PC OI  → Deribit P/C持仓比
s_macro_v2       → DXY+NQ期货+BTC.D加权
⚠️ s_macro_v2 score=0时不打印日志（设计如此，非bug）
```

**Step 4: 评分叠加顺序**
```
confluence base
→ CausalVerifier叠加（-12惩罚已减半）
→ s_cross叠加
→ s_options叠加
→ s_macro_v2叠加（score_addon=0时静默）
→ s_smart_money叠加
→ N17-SL专项覆写（体制SL校准）
→ v4.0出场后置层（RR压近目标）
→ M09-DimWeight（维度权重动态）
→ s22-GEX（期权Gamma磁铁）
→ s23-Kronos（LightGBM概率，当前lgbm_err→lite_v2回退）
→ AssetRouter乘数（BTC/ETH=1.1x）
→ N21-FibMacro（宏观EMA200分层）
→ N22b-WRMatrix（历史WR矩阵）
→ VolExh（量能竭尽）
```

**Step 5: 门控层（顺序不可变）**
```
P0-A 死穴封锁        → globally_blocked（BEAR_TREND_LONG等）
P0-B MacroGate      → price < EMA200 → BULL_TREND_LONG拦截
P0-B 灰度通道        → EMA200下方9%内 + score>=170 → 允许解锁
GapGate             → gap>6% → PRICE_EXPIRED
StructureGate       → grade<80 → WR=47%死亡区封堵
EarlyTrendGate      → BULL_EARLY体制专项门控
v4.2体制专项过滤     → BEAR_TREND_LONG / BEAR_RECOVERY_SHORT 死穴
```

---

### 阶段2 — brahma_analysis_runner 注入层

```
① BullBonus注入（bull_regime_injector）
   条件：BULL_TREND/BULL_EARLY/BEAR_RECOVERY + LONG
   上限：+25分（Phase2封印）
   EventBonus：E1-E9事件触发额外加分

② valid_signal重算（P0B解锁通道）
   BullBonus后 score >= 155 + params.valid=True → valid_signal=True
   注：P0B只设valid=False，不清零score，BullBonus可解锁

③ sw24h噪音惩罚
   BTC/标的体制切换次数 > 阈值 → 评分扣除
   每24H自动重置（360修复）

④ TimingFilter注入
   层1：价格位置（0~40分）
   层2：RSI_1H（0~35分）
   层3：Kronos p_up（0~20分）
   BULL_TREND READY≥60 / MONITOR≥35
   结果写入：timing_status / timing_badge / timing_score

⑤ 字段验证（_validate_result）
   18字段必需检查

⑥ BTC/ETH联动共振去重（check_correlation_risk）
   BTC.D>54%+ETH信号高 → 优先ETH
   双向同时信号 → 1.85x暴露风险标记
```

---

## 🔬 s7-LiqDens 三所清算密度（2026-07-06修复封印）

```python
# 正确数据源：
Binance: fapi/v1/forceOrders（私有，当市场稳定时=0条正常）
Bybit:   /v5/market/recent-trade（成交流近似，精度低）
OKX:     /v5/public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled
         ← 真实强平记录，BTC≈929条/ETH≈1444条

# 方向评分逻辑（修复后）：
ABOVE_HEAVY（上方空头止损密集）→ LONG+加分（磁铁效应）
BELOW_HEAVY（下方多头止损密集）→ LONG-扣分（下行风险）
NEUTRAL → 0分
注：LONG方向不反转score_adj（已修复B3 Bug）

# 触发条件：
confidence >= 0.3 AND score_adj != 0 → 注入s7并打印日志

# extra_data['price']注入（已修复B2 Bug）：
extra_data初始化时加入 'price': price（L2149）
```

---

## 🛡️ 防错机制矩阵

| 防错层 | 机制 | 防止 |
|--------|------|------|
| 死穴层 | globally_blocked | 逆势死亡信号（BEAR_TREND_LONG等） |
| 结构层 | StructureGate grade<80 | WR=47%死亡区 |
| 价格层 | GapGate gap>6% | 信号过期 |
| 因果层 | CausalVerifier -12 | 体制噪音 |
| 季节层 | 7月上旬-15分 | 冷起动亏损 |
| 时机层 | TimingFilter | 高分错误时机 |
| 去重层 | correlation_risk | 双开1.85x敞口 |
| 期权层 | GEX到期日×1.5 | 到期日低估 |
| 体制防抖 | RegimeStateMachine 30min | 体制频繁切换 |
| v4.2宪法 | 价格<EMA20_1H禁止做空 | 逆结构做空 |

---

## ⚠️ 当前已知故障

| 故障 | 影响 | 状态 |
|------|------|------|
| Kronos LightGBM lgbm_err | s23=lite_v2回退，±5~8分 | 进行中修复 |
| Bybit清算数据精度低 | 用成交流近似，非真实强平 | 待WebSocket替代 |

---

## 📊 输出字段标准（18字段必需）

```python
required = ['regime','score','direction','entry_lo','entry_hi','sl','tp1','rr',
            'grade','valid_signal','timing_status','timing_badge',
            'symbol','regime_cn','score_final','timing_score','source','ts']
```

---

## 🎯 信号质量评级标准

| score_final | grade | valid | timing | 操作 |
|------------|-------|-------|--------|------|
| ≥170 | ≥90 | True | READY | ENTER_FULL 5%NAV |
| 160~169 | ≥80 | True | READY | 7月减半仓1%NAV |
| 155~159 | ≥80 | True | READY | ENTER_FULL 3%NAV |
| ≥155 | ≥80 | True | MONITOR | 等READY |
| ≥140 | ≥80 | True | STANDBY | WATCH 0.5%NAV |
| <155 or grade<80 | - | False | - | SKIP |

---

*封印时间：2026-07-06 06:07 UTC | 设计院+达摩院联合审核*
*覆盖修复：OKX清算(B1-B3) + extra_data['price'] + LONG反转 + 360修复 + Phase1+2 + v4.2*
