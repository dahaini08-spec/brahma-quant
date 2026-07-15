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
