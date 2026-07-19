# 梵天系统 · 输出模板规范
# 设计院封印 · 2026-07-15 苏摩批准（视觉升级版）
#
# 核心原则：禁用代码块(```)作为分析内容容器
# 代码块仅用于：系统命令、JSON数据、脚本输出
# 分析内容全部使用 Markdown 原生排版

---

## 模板使用规则（宪法级）

| 场景 | 模板 | 禁止 |
|------|------|------|
| VIP策略播报 | VIP模板（▋▋▋姓赵不宣） | 禁止用于任何分析 |
| 1号工程全矩阵 | TEMPLATE-A | 禁止套VIP格式 |
| 实时信号速报 | TEMPLATE-B | 禁止拼接VIP格式 |
| 体制/系统审计 | TEMPLATE-C | 禁止混入策略参数 |
| 完整性检查 | TEMPLATE-D | 禁止夹杂策略建议 |
| **新闻局·市场速递** | **TEMPLATE-NEWS v3.0** | **禁止套VIP边框/禁止等宽平铺** |

---

## TEMPLATE-A · 1号工程专业分析报告

> 适用：1号工程全矩阵 / 深度推理 / 6方联合

---

### 🏛️ 梵天1号工程 · {SYMBOL} 深度分析
**{DATE} UTC ｜ {REGIME} ｜ 健康{HEALTH}/100**

---

**▌ 系统裁定**

评分 **{SCORE} / 175** {VALID_ICON} · 结构等级 **{GRADE}/100** · 时机 **{TIMING_ICON} {TIMING}**
体制 **{REGIME}** · 多周期共识 **{CONSENSUS}** {CONSENSUS_ICON}

---

**▌ 入场参数**

| 方向 | 区间 | 止损 | TP1 | TP2 | R:R |
|------|------|------|-----|-----|-----|
| {DIR} | ${ENTRY_LO}~${ENTRY_HI} | ${SL}（{SL_PCT}%） | ${TP1} | ${TP2} | {RR} |

---

**▌ 核心指标**

- **RSI** 1H={RSI_1H} {RSI_1H_ICON} · 4H={RSI_4H} {RSI_4H_ICON}
- **Kronos p_up** = {P_UP} {P_UP_ICON} — {P_UP_NOTE}
- **PD区域** = {PD}% {PD_ICON} — {PD_NOTE}
- **OI_1H** = {OI}% {OI_ICON} — {OI_NOTE}
- **期权 P/C** = {PC} {PC_ICON} — {PC_NOTE}
- **订单簿** BID={BID} / ASK={ASK} {OB_ICON}
- **宏观层** = {MACRO}分 {MACRO_ICON}

---

**▌ 流动性结构**

- 空头止损池 **${BEAR_POOL}** {BEAR_POOL_DIST} → {BEAR_POOL_NOTE}
- 多头止损池 **${BULL_POOL}** {BULL_POOL_DIST} → {BULL_POOL_NOTE}
- 关键FVG **${FVG_LO}~${FVG_HI}** 未回填 → 下行引力持续

---

**▌ 6方联合推理**

> **梵天引擎** {BRAHMA_CONCLUSION}

> **达摩院** {DHARMA_CONCLUSION}

> **量化工程师** {QUANT_CONCLUSION}

> **顶级交易员** {TRADER_CONCLUSION}

> **风控委员会** {RISK_CONCLUSION}

> **设计院** {DESIGN_CONCLUSION}

---

**▌ 封印结论**

**操作：{ACTION_ICON} {ACTION}**

触发条件（全部满足）：
{TRIGGER_LIST}

风险预警：
{RISK_LIST}

---
*梵天1号工程 · {DATE} UTC · 数据驱动 仅供参考*

---
---

## TEMPLATE-B · 实时信号速报

> 适用：快速分析 / 单标的信号 / 实时播报

---

### ⚡ 梵天信号速报 · {SYMBOL}
**{DATE} UTC · ${PRICE} · {REGIME}**

评分 **{SCORE}/175** {VALID_ICON} · 时机 **{TIMING_ICON}** · 方向 **{DIR}**

**RSI** 1H={RSI_1H} · 4H={RSI_4H} · **p_up**={P_UP} {P_UP_ICON}

**进场** ${ENTRY_LO}~${ENTRY_HI} · **止损** ${SL} · **目标** ${TP1} / ${TP2} · **R:R** {RR}

> ⚠️ {RISK_1}
> ⚠️ {RISK_2}

---
---

## TEMPLATE-C · 体制/系统审计报告

> 适用：体制审计 / 系统能力评估 / 切换分析

---

### 🏛️ 梵天系统审计 · {SUBJECT}
**{DATE} UTC · 审计方：{AUDITORS}**

---

**▌ 审计结论**

- ✅ {FINDING_OK}
- ⚠️ {FINDING_WARN}
- ❌ {FINDING_ERR}

---

**▌ 深度推理**

{REASONING}

---

**▌ 修复建议**

- 🔴 **P0（立即）** {FIX_P0}
- 🟡 **P1（近期）** {FIX_P1}
- 🟢 **P2（中期）** {FIX_P2}

**综合评分：{SCORE} / 10**

---
*设计院×梵天×达摩院 · 审计封印*

---
---

## TEMPLATE-D · 完整性健康报告

> 适用：1号工程完整性检查 / 系统健康

---

### 🏛️ 梵天1号工程 · 完整性检查
**{DATE} UTC**

---

**▌ 检查结果**

| 步骤 | 项目 | 状态 | 得分 |
|------|------|------|------|
| Step1 | 健康检查 | {HEALTH_ICON} {HEALTH}/100 | 19/20 |
| Step2 | 360体检 | {SCORE_360_ICON} {SCORE_360}/100 | {S2}/15 |
| Step3 | 冒烟测试 | {SMOKE_ICON} {SMOKE_PASS}/{SMOKE_TOTAL} | {S3}/20 |
| Step4 | P0/P1/P2修复 | {FIX_ICON} {FIX_STATUS} | {S4}/15 |
| Step5 | Cron完整性 | {CRON_ICON} {CRON_STATUS} | {S5}/15 |
| Step6 | 数据新鲜度 | {DATA_ICON} {DATA_STATUS} | {S6}/15 |

**综合评分：{TOTAL} / 100 {TOTAL_ICON}**

---

**▌ 待处理项**

{ISSUE_LIST}

---
---

## TEMPLATE-E · 1号工程 · 35维矩阵 + 六方联合深度剖析

> 适用：苏摩指令「1号工程实时分析」/「六方联合深度剖析」/「合约深度推理」
> 调用路径：`scripts/brahma_1hao_analysis.py` → `brahma_engine.analyze(deep=True)`
> 严禁使用curl+人工计算的V3.0简化路径

---

### 🏛️ 梵天设计院 · 六方联合深度剖析
**{SYMBOL_A} {PRICE_A}U · {SYMBOL_B} {PRICE_B}U · {DATE} UTC · 35维全量矩阵**
*设计院 × 量化工程师 × 顶级合约交易员 × SMC结构师 × 衍生品专家 × 宏观分析师*

---

**▌ GATE-0 · 体制与门控**

- Regime: **{REGIME}** × mult={MULT}
- score_final: **{SCORE}**（raw={RAW}）
- grade_num: **{GRADE}** {GRADE_LABEL}
- effective_grade: **{EFF_GRADE}**
- 门控状态: **{GATE_STATUS}** ← {GATE_REASON}
- 新宪法: {CONSTITUTION_NOTE}

---

**▌ 35维评分矩阵**

*── 趋势层 ──*
趋势一致性 / 多周期对齐 / OBV方向 / 动量背离 / QEW权重

*── 结构层 ──*
关键位精确度 / SMC结构 / 区间结构 / 区间Zone / 区间Zone_v2 / 区间底部做多

*── RSI层 ──*
RSI状态描述 / RSI极端加分_v2 / Phase2c / RSI极值_v2 / 布林带偏离_v2

*── 量能层 ──*
量能验证 / 量能衰竭+背离共振 / VolProfile / 成交量比率 / 形态成熟度

*── 衍生品层 ──*
清算/OI / 情绪/费率 / VolSkew / 期权+订单流 / _options_pc / _options_pc_v56

*── 外部扩展层 ──*
鲸鱼+微观 / _smart_money / _miner_pressure / _cross_fr_basis / _causal_regime

*── AI/ML层 ──*
s23_kronos / ML+在线贝叶斯 / LSTM+NLP / HMM乘数 / 研究增强层

*── 宏观层 ──*
L2+贝叶斯+宏观 / 宏观+事件

*── 时段/体制层 ──*
时段权重 / N03时段奖励 / N08 / N10 / N15_分层仓位 / N16_ATR体制 / _regime_mult

---

**▌ SMC结构 · FVG · OB · 流动性**

- 市场结构: **{SMC_STRUCTURE}**
- BOS: **{BOS}** — {BOS_NOTE}
- CHoCH: **{CHOCH}** {CHOCH_ICON}
- 最后摆动高点: {LAST_SH} / 摆动低点: {LAST_SL}

**Order Blocks：**
- Bull OB: {BULL_OB_COUNT}个 {BULL_OB_NOTE}
- Bear OB: {BEAR_OB_COUNT}个（{BEAR_OB_LIST}）

**FVG 价格缺口：**
- {FVG_BULL_LIST}
- {FVG_BEAR_LIST}
- FVG磁铁目标: {FVG_MAGNET}

**流动性猎杀区：**
- 等高止损池（上）: {EQ_HIGH_LIST}
- 等低止损池（下）: {EQ_LOW_LIST}

**PD Zone：** {PD_ZONE} · Bias={PD_BIAS} · {PD_NOTE}

**SMC综合评分：** {SMC_SCORE}/{SMC_MAX}（{SMC_GRADE}）

---

**▌ 六方联合深度推理**

各层逐层解读格式：

> **【层名称】六方推理**
> 量化工程师：{QUANT_LAYER_NOTE}
> 合约交易员：{TRADER_LAYER_NOTE}
> SMC结构师：{SMC_LAYER_NOTE}
> 衍生品专家：{DERIV_LAYER_NOTE}
> AI/ML层：{AI_LAYER_NOTE}
> 宏观分析师：{MACRO_LAYER_NOTE}

---

**▌ StructureGate 深度剖析**

- confluence.score = {SCORE}（{SCORE_CHECK}）
- grade_num = {GRADE}（{GRADE_CHECK}）
- effective_grade = {EFF_GRADE}（{EFF_CHECK}）
- 封禁根因: {GATE_ROOT_CAUSE}
- 解封路径:
  - 路径1（最快）: {UNLOCK_PATH_1}
  - 路径2（较慢）: {UNLOCK_PATH_2}
  - 路径3（强度）: {UNLOCK_PATH_3}

---

**▌ 六方最终联合封印结论**

**做多概率：{LONG_PROB}%** 做空概率：{SHORT_PROB}%

- 量化工程师：{QUANT_FINAL}
- 顶级合约交易员：{TRADER_FINAL}
- SMC结构师：{SMC_FINAL}
- 衍生品专家：{DERIV_FINAL}
- AI/ML层：{AI_FINAL}
- 宏观分析师：{MACRO_FINAL}

**最优策略：**
- {SYMBOL_A}：{STRATEGY_A}
  止损：{SL_A} 止盈1：{TP1_A} 止盈2：{TP2_A}
- {SYMBOL_B}：{STRATEGY_B}
  止损：{SL_B} 止盈1：{TP1_B} 止盈2：{TP2_B}

**当前操作：{CURRENT_ACTION}**

---
*梵天设计院·六方联合·35维全量矩阵·brahma_engine.analyze()·{DATE} UTC*

---

## TEMPLATE-E 使用铁律（宪法级）

| 规则 | 内容 |
|------|------|
| 调用入口 | 必须调用 `brahma_1hao_analysis.py` 或 `brahma_engine.analyze(deep=True)` |
| 禁止路径 | 禁止curl+人工RSI/EMA计算的V3.0简化路径 |
| 必含内容 | GATE-0 / 35维矩阵breakdown / SMC+FVG+OB+流动性 / 六方推理 / 封印结论 |
| 六方身份 | 设计院×量化工程师×顶级合约交易员×SMC结构师×衍生品专家×宏观分析师 |
| 深度要求 | 每层必须有实质性推理，不能只列数字 |
| 结论要求 | 必须含做多/做空概率 + 具体入场止损止盈 + 当前操作状态 |
| 封禁处理 | StructureGate封禁时必须给出三条解封路径 |

---
---

## 图标标准（统一规范）

| 类别 | 图标 | 含义 |
|------|------|------|
| 评分 | ✅ | 系统放行 |
| 评分 | ❌ | 封锁 |
| 评分 | ⚠️ | 待确认 |
| 评分 | 🚨 | 危险/极端 |
| 时机 | 🟢 READY | 立即可执行 |
| 时机 | 🟡 MONITOR | 等结构确认 |
| 时机 | ⚫ STANDBY | 禁止入场 |
| RSI | 🟢 | 健康 50~65 |
| RSI | 🟡 | 偏高 65~75 |
| RSI | 🔴 | 超买 >75 |
| RSI | 💙 | 超卖 <30 |
| p_up | 🟢 | 强势 ≥0.6 |
| p_up | 🟡 | 中性 0.3~0.6 |
| p_up | 🚨 | 极弱 <0.15 |
| PD | 🟢 | 折价 <38% |
| PD | 🟡 | 中性 38~62% |
| PD | ⚠️ | 溢价 >62% |
| PD | 🚨 | 极溢价 >88% |

---

## 禁止混用清单（铁律）

- ❌ TEMPLATE-A 中禁止出现 ▋▋▋ / 姓赵不宣 / VIP字样
- ❌ VIP播报中禁止插入1号工程完整分析段落
- ❌ 完整性报告中禁止出现具体入场价建议
- ❌ 速报中禁止展开6方联合推理
- ❌ **任何分析输出禁止用代码块（```）包裹正文内容**
- ✅ 代码块仅用于：系统命令、脚本输出、JSON数据

---

## TEMPLATE-NEWS v3.0 · 新闻局市场速递

> 适用：新闻快讯 / 市场速递 / 宏观事件播报 / 苏摩询问"新闻/快讯/市场"
> 封印：2026-07-17 苏摩111批准
> 核心原则：报纸头版逻辑 · 三层视觉冲击 · 禁止灰色平铺

### 视觉四原则（宪法级）

1. **结论极大化** → 核心结论用 `>` 引用块 + 粗体，强制视觉停留
2. **颜色分级** → 🔴高影响独立成节标题，🟡中影响列表，🟢低影响省略
3. **事件用序号** → ①②③粗体，≤3条，不用表格
4. **映射用表格** → 只有交易映射才用表格，事件描述绝不用表格

### 禁止事项

- ❌ 禁止 ▋▋ 边框（VIP模板专属，绝对隔离）
- ❌ 禁止 ════ 等宽边框包裹全文
- ❌ 禁止所有内容等宽平铺（灰色网格）
- ❌ 禁止超过35行
- ❌ 禁止出现技术指标原始数据（RSI数字/OB/FVG术语）
- ✅ 允许：`##` 标题分节 · `>` 引用块 · **粗体** · 颜色emoji分级

### 标准模板

---

**📰 梵天新闻局 · {DATE} {TIME} UTC**

---

## ⚡ 今日核心

> **{核心结论：驱动因素+方向+标的，≤25字}**
> 失效：{什么情况结论不成立，≤15字}

---

## 🔴 高影响 · 立即关注

**① {事件名≤10字}** → {一句影响判断≤15字}
**② {事件名≤10字}** → {一句影响判断≤15字}
**③ {事件名≤10字}** → {一句影响判断≤15字}

（最多3条，无高影响则省略本节）

---

## 🟡 中影响 · 留意节点

- {事件} → {影响}
- {事件} → {影响}

（最多3条，无则省略）

---

## 🎯 交易映射

| 标的 | 方向 | 触发 | 目标 | 有效期 |
|------|------|------|------|--------|
| BTC | 🔴空 | 反弹{价位} | {目标} | {N}H |
| ETH | 🔴空 | 反弹{价位} | {目标} | {N}H |
| BTC | 🟢多 | 跌至{价位} | {目标} | {N}H |

（≤4行，只放最重要的）

---

## ⚠️ 风险线

**最大风险：** {一句话}
**次要风险：** {一句话}

🕐 **下个节点：** {日期时间} · {事件} · {关注点}

---

### 三档触发频率

| 触发方式 | 版本 | 长度 | 内容深度 |
|---------|------|------|---------|
| 12H定时（06:00/18:00 UTC） | 完整版 | ≤35行 | 含全部四节 |
| 重大事件发生后30分钟内 | 速报版 | ≤15行 | 仅核心+高影响+映射 |
| 苏摩主动询问 | 按需版 | 按需 | 完整或精简均可 |

### 与其他模板的边界

| 模板 | 边框标识 | 受众 | 内容 |
|------|---------|------|------|
| VIP模板F | ▋▋▋ | VIP群会员 | 纯策略参数 |
| TEMPLATE-NEWS | 无边框·##分节 | 苏摩 | 新闻→交易映射 |
| 模板G简报 | ▎前缀 | 苏摩快速决策 | 7行结论卡片 |
| TEMPLATE-E | 🏛️标题 | 苏摩深度审计 | 35维完整分析 |

