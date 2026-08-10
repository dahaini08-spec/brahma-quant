# 梵天分析标准输出流程 v1.0
# 设计院封印 2026-08-10 苏摩111批准
# 
# 核心铁律：每次分析必须完整走完5层，输出格式固定
# 禁止：手写估算、跳过任何层、临时拼凑输出

---

## 🏛️ 梵天分析标准流程（5层输出体系）

### 触发条件
苏摩说「梵天分析XXX」或「分析XXX」→ 无条件执行以下5层

---

## 第一层：实时数据采集（引擎层）

**必须调用真实引擎，禁止手写估算**

```python
# 标准调用方式（带Kronos预加载）
from brahma_brain import kronos_engine as ke
ke._model_loaded = False; ke._load_model()  # Kronos预加载
from brahma_brain import brahma_engine as be
r = be.analyze('XXUSDT', deep=True)
```

**必须包含的字段：**
- price / regime / signal_dir / consensus
- score_final / effective_grade / s23_p_up(Kronos实时)
- RSI: 15m / 1H / 4H / 1D
- FR / LS多空比 / OI_1H变化
- entry_lo / entry_hi / stop_loss / tp1 / tp2 / RR / SL%

---

## 第二层：35维矩阵 + SMC结构（矩阵层）

**输出格式（固定）：**

```
▌ 35维矩阵关键项
  加分层: [趋势/量能/SMC/衍生品/ML] 各层最强项
  扣分层: [宏观/VolSkew/Kronos/时机惩罚] 各项扣分
  
▌ SMC结构
  市场结构: UPTREND/RANGING/DOWNTREND
  BullOB/BearOB: 数量 + 最近OB价格区间 + age新鲜度
  FVG: 未填数量 + 首个磁铁目标价
  极近止损池: 上方/下方距离%
  PD Zone: PREMIUM/DISCOUNT + 位置% + BIAS
```

---

## 第三层：清算矩阵（清算层）

**输出格式（固定）：**

```
▌ 清算矩阵（三所实时）
  上方空头爆仓: $价格 (+距离%, $体量M)  ← TP首选
  下方多头止损: $价格 (-距离%) 次数密集程度
  100x空头清算: $价格 (+0.95%)
  100x多头清算: $价格 (-0.95%)
  SL参考位:    $价格 (-SL%)
  FVG磁铁:     $价格（若有下方未填Bear FVG）
  
  L2买卖比: Xx（>1多头占优 <1空头占优）
  多空比: XX%多 ⚠️/✅
```

---

## 第四层：方仓铁证（方仓层）

**输出格式（固定）：**

```
▌ 方仓铁证
  n=XX  ↑XX%  ↓XX%  EV=+XX%  置信XX%
  主力意图: BULLISH/BEARISH/NEUTRAL  陷阱预警: True/False
  
  Top3相似案例:
    YYYY-MM-DD [REGIME] 涨跌+X.X%（最高+X.X% 最低-X.X%）
    
  HCME情境: adj=+XX  历史WR=XX%  n=XX案例
  Squeeze: Phase1.5/Phase2/None
  market_bias: hour_up=XX%  weekday_up=XX%  month_up=XX%
  
  ⚠️ 若n=0: 无历史铁证，仓位严格≤3%NAV
```

---

## 第五层：决策树裁决（操作层）

**输出格式（固定）：**

```
▌ 决策树（X/4步通过）
  Step1: grade=XX [✅/❌ 需≥80] | SL=XX% [✅/❌ 需≤2.0%]
  Step2: OI变化=XX% | 催化剂=XX
  Step3: CHoCH=XX次 | MTF共振=X/4 | EV=+XX%
  Step4: RR=X.XX [✅≥1.0 / ❌<1.0]
  
  ── 操作指令 ──
  [SKIP/WATCH/ENTER]
  
  SKIP:  原因 + 解封条件（需要什么变化才能入场）
  WATCH: 进场区 + 止损 + TP1/TP2 + RR + 仓位 + 杠杆 + 等待信号
  ENTER: 进场区 + 止损 + TP1/TP2 + RR + 仓位 + 杠杆 + 立即执行
  
  B/C类模块状态:
    ssi=[NORMAL/HIGH/EXTREME]  mode_c=[MODE_A/B/C]
    integrity_gate=[✅/❌]  us_session=[时段]  vol_regime=[LOW/NORMAL/HIGH]
```

---

## 输出完整模板（苏摩看到的最终卡片）

```
## 🏛️ XXX/USDT 梵天全能力分析
YYYY-MM-DD HH:MM UTC | Kronos ✅/⚠️ src=kronos_full/cache

━━━━ 第一层：实时状态 ━━━━
价格 $XX  体制 XX  方向 XX  评分 XX/150
Kronos p_up=0.XX(实时/cache)  RSI 15m/1H/4H/1D=XX/XX/XX/XX
FR=XX%  多空比=XX%多  OI_1H=±XX%

━━━━ 第二层：35维矩阵 ━━━━
[加分层] XX +XX  XX +XX  XX +XX
[扣分层] XX -XX  XX -XX
SMC: struct=XX  BullOB=X  BearOB=X  FVG=X个
PD=XX(XX%)  极近猎杀=X重  FVG磁铁=$XX

━━━━ 第三层：清算矩阵 ━━━━
上方空头墙 $XX (+XX%, $XXM)
下方多头墙 $XX (-XX%) XX次
100x空头=$XX  100x多头=$XX
L2买卖比=Xx  多空=XX%多

━━━━ 第四层：方仓铁证 ━━━━
n=XX ↑XX% ↓XX% EV=+XX% 意图=XX 陷阱=X
Top1: YYYY-MM-DD [XX] +XX%
HCME adj=+XX  Squeeze=XX

━━━━ 第五层：决策树裁决 ━━━━
Step1 [✅/❌] Step2 [✅/❌] Step3 [✅/❌] Step4 [✅/❌]

🎯 [SKIP/WATCH/ENTER]
[原因 + 具体操作参数 + 解封条件]
```

---

## 特殊场景处理规则

### 场景A：SKIP时
- 必须给出「解封条件」（价格到哪里/RSI到多少/grade达到多少）
- 禁止只说SKIP不说理由

### 场景B：妖币（mode_c=MODE_C）
- 所有仓位×0.5封印
- 方仓n=0时额外说明「无历史铁证」
- 必须标注暴涨猎手系统评分（若适用）

### 场景C：走势预判请求
- 在标准5层后追加「走势路径」模块
- 必须给出概率（三路径，概率之和=100%）
- 每条路径给出：触发条件+时间轴+目标价

### 场景D：VIP发帖请求
- 5层分析先跑完
- 然后按 vip_template_F.md 格式输出
- 发布到Binance Square

---

## 质量门控（每次输出前自检）

```
□ 是否调用了真实引擎（非手写估算）？
□ Kronos是否预加载（src=kronos_full而非cache）？
□ 清算矩阵是否有实际价格（非"数据不可用"）？
□ 方仓n值是否真实（n=0要标注"无历史铁证"）？
□ 决策树步骤是否逐步列出？
□ 操作指令是否明确（SKIP/WATCH/ENTER+参数）？
□ B/C类模块（ssi/mode_c/integrity_gate）是否输出？
```

**任何一项为否 = 分析不完整，必须补全**

---

## 封印铁律

```
每次「梵天分析」= 必须5层完整输出
不得以「时间紧」「苏摩只想看结论」为由跳层
苏摩需要结论时 → 先5层跑完，再单独输出「结论摘要」
顺序不可颠倒
```
