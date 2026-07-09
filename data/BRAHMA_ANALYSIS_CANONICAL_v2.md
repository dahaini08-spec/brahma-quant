# 梵天币种分析 · 技术全覆盖标准 v2.0
# 6方联合审核封印 · 2026-07-09 09:05 UTC
# 基于：BRAHMA_ANALYSIS_CANONICAL.md v1.0 升级

---

## 核心升级：8个技术盲区修复

| 盲区 | 原有 | v2.0修复 |
|------|------|---------|
| SMC订单块OB | 仅说"有/无OB" | Bull OB/Bear OB 精确价位+dist_pct |
| PD Zone | 缺失 | DISCOUNT/PREMIUM zone+机构偏向 |
| 流动性猎杀池 | 缺失 | 等高/等低止损池精确价位 |
| CVD订单流 | 缺失 | CVD多空主导+分数 |
| N系列专项 | 缺失 | N03/N08/N10/N16时段/体制/ATR信号 |
| 实盘WR | 缺失 | regime_wr_live + kelly_pos_pct |
| B2接近度 | 缺失 | gap%危险区警告 |
| BullBonus来源 | 仅说"+17" | 明确每项来源 |

---

## L2 技术全覆盖模板 v2.0（标准深度分析）

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛️ 梵天全覆盖分析 · {SYM} | {TIME} UTC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【S1 核心状态】
  价格：${PRICE}（{CHG}%）| 体制：{REGIME} | 方向：{DIR}
  Score：{SCORE} | Grade：{GRADE}({STRUCTURE_LABEL}) | Valid：{VALID}
  TimingFilter：{TIMING}({TF_SCORE}分) | Dharma：{DHARMA}/6({DHARMA_VERDICT})
  仓位：{POS}% {POS_LEVEL} | 实盘WR：{LIVE_WR}%(n={LIVE_N})

【S2 三层门控】
  🚫/✅ P0B宏观门：${PRICE} vs EMA200 ${EMA200}（{P0B_GAP}%）
  🚫/✅ StructureGate：grade={GRADE}（{STRUCTURE_LABEL}，需≥80）
  🟢/🟡/🔴 TimingFilter：{TF_STATUS} | {TF_BREAKDOWN}
  BullBonus注入：{BULLBONUS_SCORE}分（{BULLBONUS_REASONS}）

【S3 RSI·布林带·ATR】
  RSI：15M={R15M} | 1H={R1H} | 4H={R4H} | 1D={R1D}
  BB：pos={BB_POS}（{BB_ZONE}）| 上轨=${BB_UP} | 中轨=${BB_MID} | 下轨=${BB_LO}
  ATR：1H={ATR1H} | 4H={ATR4H} | 波动率={ATR_PCT}%
  N系列信号：
    {N03_NOTE}
    {N08_NOTE}
    {N16_NOTE}（⚠️ ATR禁区/正常）

【S4 SMC完整结构】
  主结构：{SMC_STRUCT}（BOS={BOS} CHoCH={CHOCH}）
  波浪：{WAVE}（{WAVE_NOTE}）
  PD Zone：{PD_ZONE}（机构偏向：{PD_BIAS} | 位置：{PD_POS:.0%}）

  Bull OB（做多订单块）：
    区间：${BULL_OB_LOW}~${BULL_OB_HIGH}（中位${BULL_OB_MID}）
    距当前：{BULL_OB_DIST}%
  Bear OB（做空订单块）：
    区间：${BEAR_OB_LOW}~${BEAR_OB_HIGH}（中位${BEAR_OB_MID}）
    距当前：{BEAR_OB_DIST}%
  FVG（公允价值缺口）：{FVG_STATUS}

  流动性猎杀池：
    上方等高止损池：${LIQ_ABOVE}（+{LIQ_ABOVE_PCT}%）← 做空止损密集
    下方等低止损池：${LIQ_BELOW}（-{LIQ_BELOW_PCT}%）← 做多止损密集
  摆动高：${SWING_HI} | 摆动低：${SWING_LO}
  区间高低：${RANGE_HI}~${RANGE_LO}
  SMC评分：{SMC_SCORE}/20（{SMC_GRADE}）| 细节：{SMC_DETAILS}

【S5 量能·衍生品·CVD】
  量能衰竭：{VOL_LEVEL}（score={VOL_SCORE}）
    {VOL_NOTE1}
    {VOL_NOTE2}
  CVD订单流：{CVD_NOTE}
  多周期背离：{MTF_RESONANCE}（score={MTF_SCORE}）

  OI：{OI_CHG}%（{OI_MOMENTUM}）| 绝对OI={OI_ABS}
  LSR多空比：{LSR}%（{LSR_NOTE}）
  FR跨所均值：{FR_AVG}% | Basis：{BASIS}%（{BASIS_BIAS}）
  期权P/C：{PC_RATIO}（{PC_SIGNAL}）

【S6 宏观·时段·LLM】
  DXY：{DXY}（{DXY_DIR}）| NQ：{NQ}
  时段权重：{TIME_WEIGHT}（{TIME_NOTE}）
  实盘自适应：门槛={ADAPTIVE_THR} | score超出={SCORE_VS_THR}
  Kelly仓位建议：{KELLY_PCT}%
  B2接近度：{B2_NOTE}

【S7 关键价位全景】
  ┌─────────────────────────────────────────┐
  │ 上方阻力区域（从近到远）                  │
  │  ${R1}  {R1_NOTE}                       │
  │  ${R2}  {R2_NOTE}                       │
  │  ${R3}  {R3_NOTE}                       │
  ├─────────────────────────────────────────┤
  │ 当前价 → ${PRICE}                        │
  ├─────────────────────────────────────────┤
  │ 下方支撑区域（从近到远）                  │
  │  ${S1}  {S1_NOTE}                       │
  │  ${S2}  入场区下沿⭐                     │
  │  ${S3}  止损位                          │
  │  ${S4}  {S4_NOTE}                       │
  └─────────────────────────────────────────┘

【S8 合约交易计划】
  方向：{DIR}（{REGIME}体制授权）
  入场区（{PRIMARY_TF} OB）：${ENTRY_LO}~${ENTRY_HI}
  止损（{SL_BASIS}）：${SL}（-{SL_PCT}%）
  TP1（50%平仓）：${TP1}（+{TP1_PCT}% R:R {RR1}）
  TP2（50%平仓）：${TP2}（+{TP2_PCT}%）
  仓位：{POS}% NAV | 杠杆：{LEV}x | VaR95：{VAR}%

【裁决】
→ {SYM} {DIR} 等 {TRIGGER_COND} 入场
  止损 ${SL}，TP ${TP1}/${TP2}
  当前距入场区：{GAP_TO_ENTRY}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## L3 六方联合全系统 v2.0（额外新增段落）

在原L3基础上，[H3] 35维展开新增以下子段：

```
D06b SMC完整OB/FVG/流动性池（全新）
  Bull OB: ${bull_ob_lo}~${bull_ob_hi} dist={dist}%
  Bear OB: ${bear_ob_lo}~${bear_ob_hi} dist={dist}%
  等高止损池: ${liq_above} / 等低止损池: ${liq_below}
  PD Zone: {zone} position={pos:.0%} 机构偏向={bias}
  FVG磁铁: {fvg_magnet_up}↑ / {fvg_magnet_down}↓

D13b N系列专项信号（全新）
  N03时段: {N03} 
  N08体制RSI: {N08}
  N10全覆盖: {N10}
  N16 ATR: {N16} ← 若PF<1.0为禁区警告
  Phase2c: {Phase2c}
  CVD: {CVD}

D18b 实盘WR校验（全新）
  实盘WR: {LIVE_WR}% (n={LIVE_N})
  自适应门槛: {ADAPTIVE_THR}
  Kelly仓位: {KELLY}%
  B2接近度: {B2_NOTE}
```

---

## 提取代码标准（v2.0必须字段清单）

```python
# === 基础字段 ===
price        = r['price']
regime       = r['regime']
score        = r['score_final']
grade        = r['grade']
eff_grade    = r['effective_grade']
valid        = r['valid_signal']
timing_st    = r['timing_status']
timing_sc    = r['timing_score']
timing_bd    = r['_timing']['breakdown']
direction    = r['signal_dir']
consensus    = r['consensus']
structure_lb = r['confluence']['structure_label']

# === 仓位 ===
pos_pct      = r['pos_pct_sizer']
pos_level    = r['pos_level_sizer']

# === 入场出场 ===
entry_lo     = r['params']['entry_lo']
entry_hi     = r['params']['entry_hi']
sl           = r['params']['stop_loss']
sl_pct       = r['params']['sl_pct']
tp1          = r['params']['tp1']
tp2          = r['params']['tp2']
rr1          = r['params']['rr1']
sl_basis     = r['confluence']['sl_basis']
primary_tf   = r['primary_tf']

# === RSI / BB / ATR ===
rsi_15m      = r['momentum']['rsi_15m']
rsi_1h       = r['momentum']['rsi_1h']
rsi_4h       = r['momentum']['rsi_4h']
rsi_1d       = r['momentum']['rsi_1d']
bb           = r['momentum']['bb']      # pos/mid/upper/lower/width
atr_1h       = r['momentum']['atr_1h']
atr_4h       = r['momentum']['atr_4h']

# === SMC完整结构（v2.0新增）===
smc          = r['smc']
smc_struct   = smc['structure']['structure']
bull_ob      = smc['order_blocks']['nearest_bull_ob']  # high/low/mid/dist_pct
bear_ob      = smc['order_blocks']['nearest_bear_ob']  # high/low/mid/dist_pct
liq_above    = smc['liquidity']['nearest_above']       # level/dist_pct
liq_below    = smc['liquidity']['nearest_below']       # level/dist_pct
pd_zone      = smc['pd_zone']                          # zone/bias/position/note
fvg_bull     = smc['fvg']['nearest_bull']
fvg_bear     = smc['fvg']['nearest_bear']
smc_score    = smc['score']                            # score/max/details/grade

# === 衍生品 ===
oi_chg       = r['sentiment']['oi_change_pct']
oi_mom       = r['sentiment']['oi_momentum']
lsr          = r['sentiment']['long_short_ratio']
fr           = r['sentiment']['funding_rate']

# === N系列专项（v2.0新增）===
bk           = r['confluence']['breakdown']
N03          = bk.get('N03时段奖励', 0)
N08          = bk.get('N08_牛市RSI中性', 0)
N10          = bk.get('N10_全覆盖奖励', 0)
N16          = bk.get('N16_ATR体制', 0)   # ⚠️ PF<1时禁区警告
Phase2c      = bk.get('Phase2c_RSI中性偏强_v2', 0)
CVD          = bk.get('CVD订单流', 0)
bullbonus    = r.get('_regime_context_bonus', {})  # bonus/reasons

# === BullBonus来源（v2.0新增）===
bb_bonus     = bullbonus.get('bonus', 0)
bb_reasons   = bullbonus.get('reasons', [])

# === 实盘WR（v2.0新增）===
v2_audit     = r['confluence']['v2_audit']
live_wr      = v2_audit['regime_wr_live']
live_n       = v2_audit['regime_n_live']
kelly_pct    = v2_audit['kelly_pos_pct']
adaptive_thr = v2_audit['adaptive_threshold']
score_vs_thr = v2_audit['score_vs_threshold']

# === B2接近度（v2.0新增）===
b2_note      = r['confluence'].get('b2_proximity', '')

# === Dharma节点（v2.0完整）===
dharma       = r['dharma_nodes']
dharma_pass  = dharma['nodes_pass']
dharma_detail= dharma['detail']  # N1✓ N2✓ N3✗...

# === 关键价位（v2.0新增）===
resistance   = r['key_levels']['resistance']  # 从近到远阻力列表
support      = r['key_levels']['support']     # 从近到远支撑列表
range_hi     = r['key_levels']['range_high']
range_lo     = r['key_levels']['range_low']
```

---

## 分级展示规则（避免信息过载）

| 字段 | L1快讯 | L2分析 | L3全系统 |
|------|--------|--------|---------|
| 价格/体制/score/valid | ✅ | ✅ | ✅ |
| RSI多周期 | ❌ | ✅ | ✅ |
| BB位置 | ❌ | ✅ | ✅ |
| 三层门控 | 一句话 | 完整 | 完整 |
| SMC结构 | ❌ | ✅ | ✅ |
| Bull/Bear OB精确价位 | ❌ | ✅ | ✅ |
| PD Zone | ❌ | ✅ | ✅ |
| 流动性猎杀池 | ❌ | ✅ | ✅ |
| FVG | ❌ | 有则展示 | ✅ |
| N系列专项 | ❌ | 核心项 | 全部 |
| 实盘WR/Kelly | ❌ | ✅ | ✅ |
| BullBonus来源 | ❌ | ✅ | ✅ |
| CVD订单流 | ❌ | ✅ | ✅ |
| 35维逐项 | ❌ | ❌ | 有效维度 |
| 入场计划 | 一句话 | 完整8项 | 完整+VaR |

---

## 禁止项（v2.0新增4条）

原有6条基础上新增：
7. 禁止忽略N16 ATR禁区（若PF<1.0必须在报告中标注⚠️）
8. 禁止在B2接近度"危险区"时不做说明（gap<1%高频止损区）
9. 禁止BullBonus仅说"+17"不解释来源（必须列出每项）
10. 禁止实盘WR=0%时不标注风险（n<50样本量不足须说明）

---

*最后更新：2026-07-09 09:05 UTC | 6方联合审核封印 v2.0*
