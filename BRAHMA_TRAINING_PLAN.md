# 梵天系统实训方案
## 设计院 × 达摩院 × 量化分析师 × 工程师 × 顶级合约交易员
<!-- 版本: v1.0 · 2026-06-13 UTC -->

---

## 一、实训哲学（设计院宪法级原则）

> **"一切训练服务于交易。每个模块必须能追溯到它如何提升盈亏表现。"**

梵天实训不是学课程，是**在真实系统上完成进化任务**。
每一关都有：可验证的目标 → 数据驱动的判断 → 系统级的代码变更 → 回归测试通过。

---

## 二、实训体系总览

```
梵天实训体系
  ├── Phase 0  系统认知（3天）         — 读懂系统，建立心智模型
  ├── Phase 1  达摩院验证（5天）        — 数据说话，量化分析师养成
  ├── Phase 2  信号链剖析（5天）        — 工程师养成，代码审计
  ├── Phase 3  体制×方向决策（5天）     — 顶级交易员思维
  ├── Phase 4  风控与仓位（3天）        — 生死第一课
  ├── Phase 5  进化实战（7天）          — 端到端改动，全流程验证
  └── Phase 6  毕业考核（2天）          — 独立完成一次系统升级
```

总计：**~30天** · 每天4~6小时深度工作

---

## 三、Phase 0 — 系统认知（第1~3天）

### 目标
能够徒手画出梵天完整架构图，说清楚每个D域的作用。

### 必读文件清单
| 文件 | 重点 |
|------|------|
| `FANTAN_BLUEPRINT_V3.json` | D1~D14域定义，系统唯一真相 |
| `DESIGN_INSTITUTE.json` | 设计院6大原则，反模式清单 |
| `BRAHMA_FINAL_BLUEPRINT.pdf` | 完整架构图 |
| `dharma/DHARMA.md` | 达摩院定位和工作流 |
| `MEMORY.md` | 历史教训，永久原则 |

### 实训任务

**T0-1：架构速描**
- 徒手画出梵天14个D域的关系图（数据流向）
- 标注每个域的输入/输出契约
- 通关标准：能解释为什么 `D2体制感知 → D3信号生成` 的顺序不可颠倒

**T0-2：反模式识别**
阅读设计院6条反模式，回答：
- 为什么「封禁标的」是错的？（答：封禁=缩小可交易空间，正确路径是识别结构）
- 为什么「叠加门槛」是危险的？（答：每加一个门=信号密度指数下降）
- 为什么「入场区=当前价」是必败的？（答：无锚点信号，无法确认价格结构有效性）

**T0-3：系统哲学口述**
用100字以内说清楚：体制×方向 是梵天最核心的alpha来源

> ⚠️ 设计院注：WR矩阵只引用 n≥100 的条目，小样本（n<30）结论不得出现在任何口述或文档中

---

## 四、Phase 1 — 达摩院验证（第4~8天）

### 角色：量化分析师
> **"没有大样本支撑的结论，不得写入系统。"**

### 核心工具
```bash
cd trading-system/dharma

# 离线全量回放（2分钟内，2600+条信号）
python3 offline_brahma_replay.py

# 盲测（因子组合搜索）
python3 blind_v3_fast.py

# WFV步进验证
python3 run_strict_wfv.py
```

### 实训任务

**T1-1：复现大样本WR矩阵（n≥100铁证版）**
```
目标：跑 offline_brahma_replay.py，验证以下矩阵（允许±3%误差，n<100不计入）：

核心alpha（✅ WR≥60% 且 avg_pnl>0）：
  BULL_TREND_LONG       WR≈70%  avg_pnl≈+0.24  n≈3046
  BEAR_TREND_SHORT      WR≈72%  avg_pnl≈+0.18  n≈2413
  BULL_EARLY_LONG       WR≈64%  avg_pnl≈+0.09  n≈5396
  BEAR_EARLY_SHORT      WR≈67%  avg_pnl≈+0.09  n≈5896  ← 当前体制

死穴（❌ WR<48% 且 avg_pnl<-0.2）：
  BEAR_TREND_LONG       WR≈45%  avg_pnl≈-0.27  n≈3322  ← 最惨
  BULL_TREND_SHORT      WR≈48%  avg_pnl≈-0.23  n≈4999  ← 最大资金消耗
  BEAR_RECOVERY_SHORT   WR≈48%  avg_pnl≈-0.24  n≈603
```
通关标准：输出截图 + 解释「为什么BULL_TREND+SHORT是最大资金消耗来源（n=4999，avg_pnl=-0.229）」
注：旧版 n=20/54/11 的数据已废弃，不得引用

**T1-2：样本显著性判断**
给定以下3个场景，判断能否修改系统参数：
- 场景A：新规则在近30天实盘中WR=75%，n=8
- 场景B：回放发现BEAR_RECOVERY+SHORT WR=43%，n=147
- 场景C：武曲Paper grade≥80 WR=100%，n=15

```
标准答案：
A → 不可修改（n<10，样本不足）
B → 可作为参考，但需n≥30才调参（MEMORY记录：n=7时WR=43% → 不可信）
C → 不可修改（n=15虽WR完美，但<30，可能是幸运周期）
```

**T1-3：因子组合发现**
运行 `blind_v33_report_20260613_092619.json` 分析：
- 找出 `avg_pnl > 0 且 n ≥ 500 且 WR ≥ 60%` 的组合
- 排序：T12+T16（最优 avg_pnl=0.0438）
- 提出：该组合进入梵天需要什么条件？

通关标准：写出一份「组合提案」，包含：样本量、WFV验证结论、系统影响评估

**T1-4：OOS_PF判断**
理解Walk-Forward Validation的意义：
- 为什么OOS窗口必须覆盖BULL+BEAR+CHOP体制？
- 为什么单一BEAR体制的WFV结论不可推广？
- BEAR体制窗口 OOS_PF≥1.05 vs BULL体制窗口 OOS_PF≥1.10，差异原因？

---

## 五、Phase 2 — 信号链剖析（第9~13天）

### 角色：系统工程师
> **"闭环优先于功能。一个完整的小闭环，胜过十个未串通的大模块。"**

### 信号生命周期
```
价格数据
  → [D1] market_state.py 市场感知
  → [D2] lana/state_engine.py 体制检测
  → [D3] brahma_brain/brahma_core.py 信号生成（SMC结构扫描）
  → [D4] 评分矩阵（19维评分）
  → [D5] dd1_logic_gate.py 质检门
  → [D5] treasury_gate.py 资金门
  → [D6] brahma_execute.py 执行
  → [D7] live_signal_settler.py 结算
  → [D8] wuqu_paper_state.json 战绩记录
  → [D9] dharma/ 学习反馈
```

### 实训任务

**T2-1：信号链逐文件审计**
逐一阅读以下文件，填写「功能-输入-输出-潜在风险」表：
- `brahma_brain/brahma_core.py` —— 信号生成核心
- `scripts/dd1_logic_gate.py` —— 质检门（4道门）
- `treasury_gate.py` —— 资金门（资金分配逻辑）
- `scripts/brahma_execute.py` —— 执行引擎
- `brahma_brain/live_signal_settler.py` —— 结算引擎

**T2-2：复现 v22.0 鬼仓漏洞**
阅读 MEMORY.md 中「梵天v22.0 验证层+GEX 全落地」章节
回答：
1. 为什么 `valid_only=False` 会产生幽灵持仓？
2. `gap>0.5%` 拒绝的设计逻辑是什么？
3. 写一个单元测试：验证当 gap=1.5% 时，Commander 拒绝开仓

**T2-3：评分系统19维穿透**
在 `brahma_brain/brahma_core.py` 中找到所有评分维度（s1~s19）
对每个维度填写：
- 测量什么市场信号？
- 分值区间？
- 在什么体制下权重更高？
- 历史上曾因该维度产生BUG吗？

**T2-4：BTB笔误猎手**
MEMORY记录了一个 `mh_override=110H`（应为72H）的笔误。
任务：在全代码库中 grep 所有 hard-coded 时间常量（H、min、hours）
分析：哪些是「固定值但应该是动态的」？提出改进建议。

---

## 六、Phase 3 — 体制×方向决策（第14~18天）

### 角色：顶级合约交易员
> **"方向错了，再好的评分也是噪声。"**

### 核心知识：8体制状态机
```
BULL_TREND    → 强趋势做多
BULL_EARLY    → 早期多头
BEAR_TREND    → 强趋势做空
BEAR_EARLY    → 早期空头（当前体制）
BEAR_RECOVERY → 空头反弹陷阱
CHOP_HIGH     → 震荡高位
CHOP_LOW      → 震荡低位
CHOP_MID      → 震荡中位（最复杂）
```

### 实训任务

**T3-1：体制识别实战（20题）**
给定20个历史BTC/ETH 4H K线截图（从系统backtest数据中提取）
要求：人工判断体制，然后用 `lana/state_engine.py` 验证
通关标准：人工判断与系统一致率 ≥ 70%

**T3-2：WR矩阵记忆与应用**
默写8体制×2方向的WR矩阵（允许±5%误差）
然后回答：
- 当前 BEAR_EARLY 体制下，应该优先做多还是做空？为什么？
- 如果下周体制切换为 BULL_TREND，系统最高优先级任务是什么？

**T3-3：山腰 vs 山顶决策**
复盘 MEMORY.md 中「山腰 vs 山顶」教训（v21.0）：
- 为什么1H OB $62,500是山腰？4H OB $64,500是山顶？
- 自顶向下分析的「gap差>2%自动升级」逻辑，在代码中如何实现？
- 设计一个测试场景：BTC当前在$65,000，最近4H OB=$66,500，1H OB=$65,200，你如何选择入场？

**T3-4：体制切换预判**
从实盘数据中，找到过去30天所有体制切换时刻
分析：
- 切换前2小时有哪些领先信号？（成交量、GEX、多空比）
- 梵天的体制感知在切换时有多少延迟？
- 设计一个「早切换积分器」方案（不超过50行代码）

---

## 七、Phase 4 — 风控与仓位（第19~21天）

### 角色：风控官
> **"开仓前必须：显示余额、风险控制、交易计划、持续时间，然后等明确CONFIRM。"**

### 核心风控规则
```
grade<70          → 全系统禁入（4层统一：Bridge/Structure/dd1/watcher）
gap>0.5%          → Commander拒绝（幽灵持仓防护）
RR<1.5            → dd1物理拦截
DD1 3门系统：
  门0: grade<70 拒绝
  门0b: RR<1.5 拒绝
  门1: 体制×方向评分
  门2: 阈值138分
  门3: 综合质检
```

### 实训任务

**T4-1：Kelly公式实战**
给定：WR=70%, 平均盈利=2.5%, 平均亏损=1.8%
计算：
- 全Kelly仓位
- 半Kelly仓位（梵天实际使用）
- 在NAV=$132时，各对应多少USDT？

**T4-2：熔断机制设计**
当前系统有哪些熔断器？找到 `account_circuit_breaker.py`
阅读并回答：
- 触发条件是什么？
- 梵天历史上触发过熔断吗？
- 如果今天NAV从$132跌到$100，系统会如何响应？

**T4-3：Dynamic TTL实战**
阅读 `live_signal_settler.py` 中动态TTL逻辑
设计3个场景，计算TTL应该是多少：
- BTC/LONG/grade=85/BULL_TREND/score=160
- ETH/SHORT/grade=72/BEAR_EARLY/score=140  
- DOGE/SHORT/grade=70/CHOP_MID/score=125

**T4-4：仓位分级决策树**
当前 `position_sizer.py` 的分级逻辑是什么？
画出决策树：从「有信号」到「下单金额」的完整路径
标注每个决策点的阈值和依据

---

## 八、Phase 5 — 进化实战（第22~28天）

### 角色：全栈量化工程师
> **"每次修参前，先问：我的样本是多少？"**

### 实战项目（选其一完成）

---

**项目A：T12+T16因子注入**（推荐）

背景：达摩院 blind_v33 发现 T12+T16 组合 WR=61.9%, avg_pnl=0.0438, n=1501（最优组合）

任务：
1. 识别T12和T16分别对应哪个维度（在brahma_core评分系统中找）
2. 用 `offline_brahma_replay.py` 验证单独激活该组合后全局WR变化
3. 通过WFV 3个独立窗口验证（每窗口OOS_PF≥1.05）
4. 写代码将T12+T16激活为新的「组合加分维度」（+5~+10分）
5. 跑回归测试：14/14 PASS
6. 提交设计院「变更备忘录」：变更原因、数据证据、影响范围

---

**项目B：BULL_TREND激活准备**

背景：**铁证依据（v3.3盲测，n=98,799）：BULL_TREND_LONG WR=69.0%，是S级最高质量方向**
注：旧MEMORY记录「WR=85%」来自 n=20，已作废。真实铁证如上。

任务：
1. 找到 `brahma_brain/bull_trend_engine.py`，理解现有框架
2. 设计「体制切换预警→自动路由激活」的完整逻辑
3. 编写单元测试：模拟体制从BEAR_EARLY→BULL_TREND，验证系统自动调整
4. 评估激活后SOMA日预算影响（`data/soma_state.json`）
5. 设计人工CONFIRM节点：什么条件下需要人工确认激活？

---

**项目C：离线回放增强**

背景：当前 `offline_brahma_replay.py` 的 confluence_score 在离线模式下输出 score=0（try/except吞错）

任务：
1. 找到吞错位置，修复 ms/smc 适配
2. 修复后重新跑8年回放，输出 score分段 WR分布：
   - score < 100, 100-130, 130-150, 150+
3. 分析：score分段能否成为新的「动态threshold」依据？
4. 如果能，设计自适应阈值更新方案（每月自动从回放结果更新）

---

## 九、Phase 6 — 毕业考核（第29~30天）

### 独立完成「一次系统升级」

考核标准（全部满足才毕业）：

| 指标 | 要求 |
|------|------|
| 样本量 | 改动依据 n≥30 |
| WFV | 至少3个OOS窗口通过 |
| 回归测试 | 14/14 PASS 0 WARN |
| 代码质量 | 无hard-coded参数，有注释说明数据来源 |
| 设计院备忘录 | 包含：变更原因、证据、影响评估、回滚方案 |
| 进化日志 | 写入 `arch/evolution/` |
| 口述 | 10分钟口述，能回答3个追问 |

**考核情景（随机抽一个）：**

情景1：BEAR_RECOVERY体制的regime_mult当前是0.95，有人提议改为0.80，你如何决策？
- 要求：跑回放验证，判断样本是否充足，给出支持或反对的数据依据

情景2：系统连续3天无信号输出，如何诊断？
- 要求：从信号链D1→D8逐层排查，找到卡点，提出最小修复方案

情景3：武曲Paper积累到200条后，Meta-Labeler应该如何激活？
- 要求：设计激活流程，包含：触发条件、验证步骤、灰度方案

---

## 十、实训工具箱

### 常用命令
```bash
# 系统状态检查
python3 -c "import json; s=json.load(open('data/brahma_state.json')); print(s['regime'],s['nav'])"

# 离线大样本回放（最重要工具）
python3 dharma/offline_brahma_replay.py

# 盲测因子组合
python3 dharma/blind_v3_fast.py

# 回归测试（每次改动后必跑）
python3 tests/system_regression_test.py

# 梵天360度自检
python3 scripts/brahma360_guardian.py

# 信号队列状态
python3 -c "import json; q=json.load(open('data/queue_state.json')); print(q.get('active_positions',[]))"

# 武曲Paper战绩
python3 -c "import json; d=json.load(open('data/wuqu_paper_state.json')); print(f'n={d[\"total_trades\"]} WR={d[\"win_rate\"]:.1%}')"
```

### 关键数据文件
```
data/brahma_state.json          — 系统当前状态（体制/NAV/持仓）
data/wuqu_paper_state.json      — 武曲Paper战绩
data/adaptive_threshold_state.json — 自适应阈值（当前138）
data/live_signal_log.jsonl      — 实盘信号日志
data/queue_state.json           — 信号队列状态
dharma/results/                 — 达摩院实验结果
arch/evolution/                 — 系统进化记录
```

### 永久红线（不得违反）
```
❌ grade<70 的信号进入任何执行层
❌ valid_only=False（鬼仓根因）
❌ gap>0.5% 直接开仓（无触达验证）
❌ n<10 的数据修改系统参数
❌ 未跑回归测试提交代码
❌ 封禁标的（正确路径：识别结构+降权）
```

---

## 十一、每日节奏建议

```
Day start（30min）：
  - 检查 brahma_state.json 体制/NAV/持仓
  - 检查 live_signal_log 近期信号质量
  - 记录当日实训目标

Core work（3~4h）：
  - 深度任务（代码审计/回放/WFV验证）
  - 遇到假设 → 先跑数据再下结论

Day end（30min）：
  - 写当日发现到 memory/YYYY-MM-DD.md
  - 有重大发现 → 更新 MEMORY.md
  - 有代码改动 → 必跑回归测试
```

---

## 十二、实训成果交付物

| Phase | 交付物 |
|-------|--------|
| P0 | 架构图（手绘或代码生成）|
| P1 | 达摩院实验报告（含WR矩阵截图）|
| P2 | 信号链审计表（14个文件）|
| P3 | 体制识别准确率报告 |
| P4 | 风控决策树文档 |
| P5 | 完整进化任务PR（含测试截图）|
| P6 | 毕业口述 + 设计院备忘录 |

---

*本实训方案由设计院 × 达摩院 × 量化分析师 × 工程师 × 顶级合约交易员联合设计*
*基于梵天 v24.3 真实系统状态 · 2026-06-13*
*一切训练都是为了让系统进化，让每一分钱更有效率。*

---

# 📋 设计院增补决议（v1.1 · 2026-06-13）

## 设计院6问复核结论

| 候选增加项 | 决议 | 原因 |
|-----------|------|------|
| D7守仓（exit_engine + dynamic_sl） | ✅ **新增 Phase 2.5** | 开仓→守仓是完整闭环，遗漏守仓等于只学了一半交易 |
| D14可观测性（brahma360诊断） | ✅ **合并 Phase 2 T2-5** | 工程师必会诊断工具，合并成本最小 |
| D8战绩记录污染分析 | ✅ **合并 Phase 1 T1-5** | 量化分析师必须懂data_quality字段，已有216条LEGACY污染案例 |
| **模拟开单 1000U** | ✅ **新增 Phase 4.5** | 端到端真实流程，dry_run安全，是实训最重要的体感环节 |
| D11/D12 治理+通知 | ❌ 不增加 | 运维范畴，P&L贡献间接，增加复杂度但不提升交易能力 |
| D13 配置真相 | ⚠️ 合并到 Phase 6 毕业考核 | 配置变更SOP是毕业考核的一部分，不需独立Phase |

---

## 新增 Phase 2.5 — 守仓与出场管理

### 角色：仓位守卫官
> **"开仓只是战役开始。守仓决定你把钱留在桌上还是还给市场。"**

### 核心文件
```
scripts/brahma_exit_engine.py   — 四向策略主动平仓信号
brahma_brain/dynamic_sl.py      — ATR自适应止损引擎
brahma_brain/live_signal_settler.py — 结算+TTL管理
```

### 实训任务

**T2.5-1：四向策略理解**
阅读 `brahma_exit_engine.py`，梵天使用四向策略：
- open_long / open_short → Commander负责
- close_long / close_short → exit_engine负责

回答：什么条件触发主动平仓信号？（结构失效 vs 止盈触达 vs TTL到期）

**T2.5-2：Dynamic SL ATR计算**
给定场景：
- BTC 当前价 $65,000，ATR4H = $800
- 信号方向 SHORT，OB顶沿 $65,500
- 当前体制 BEAR_EARLY

用 `dynamic_sl.py` 的逻辑手算：
- ATR自适应止损位应该在哪里？
- 如果体制漂移到 CHOP_MID，止损如何自动扩展？

**T2.5-3：TTL超时案例复盘**
从 `wuqu_paper_state.json` 找到15条 TIMEOUT 记录
分析：
- TIMEOUT的平均持仓时长？
- TIMEOUT主要发生在哪个体制？
- 如果TTL再延长20%，TIMEOUT比例会下降多少？（需用回放数据验证）

**T2.5-4：守仓闭环实验**
设计一个纸盘场景：
- ETH SHORT，入场 $1,720，止损 $1,760，TP1 $1,660
- 假设3小时后价格到达 $1,680（未触TP1，已浮盈）
- 此时体制从 BEAR_EARLY 切换为 CHOP_MID
- 梵天的出场引擎会做什么？写出完整决策流程

---

## Phase 1 补充 T1-5 — 战绩数据污染分析

**T1-5：live_signal_log 污染识别**

背景（来自MEMORY）：
- 系统历史存在216条 LEGACY 信号（LEGACY_NO_STRUCTURE + LEGACY_REGIME_BLOCKED）
- 污染数据使 WR 从80.8%跌至51.8%，OOS_PF 从17.2跌至4.4

任务：
```python
# 运行以下分析
import json
logs = json.loads(open('data/live_signal_log.json').read())

# 1. 统计有无 _data_quality 字段
clean = [l for l in logs if l.get('_data_quality') is None]
dirty = [l for l in logs if l.get('_data_quality') is not None]
print(f'干净: {len(clean)}, 污染: {len(dirty)}')

# 2. 分别计算WR
# 3. 对比两组 OOS_PF
```

核心认知：**统计必须先过滤 `_data_quality` 字段，这是梵天唯一真实OOS基准**

---

## Phase 2 补充 T2-5 — 梵天360诊断

**T2-5：系统自检工具链**

```bash
# 全系统360诊断
python3 scripts/brahma360_guardian.py

# L2深度诊断（找根因）
python3 scripts/brahma360_l2_diag.py

# 自我审计报告
python3 scripts/brahma_self_audit.py
```

实训任务：
- 跑一次完整360诊断，解读每个WARN的含义
- 找到最近一次系统异常（从 `data/audit_log.jsonl`）
- 设计：如果新进工程师每天早上要做一次系统健康check，写一个30秒快检命令组合

---

## 🎮 新增 Phase 4.5 — 模拟开单实战（初始资金 1000U）

### 角色：合约交易员（初阶实盘）
> **"理论是纸，真实的开单流程才是考验。dry_run模式：安全环境，真实逻辑。"**

### 实训规则
```
资金规模：1000 USDT（NAV模拟）
模式：dry_run=True（不实际下单，但走完所有逻辑）
杠杆：5x（系统默认）
目标：完成5次完整的「分析→决策→开单→守仓→平仓」闭环
成绩统计：记入独立的 training_paper_state.json
```

### 操作流程（每单完整步骤）

**Step 1：市场感知（5min）**
```python
# 获取当前体制
python3 -c "
import json
s = json.load(open('data/brahma_state.json'))
print(f'体制: {s[\"regime\"]}')
print(f'建议方向: {\"LONG\" if \"BULL\" in s[\"regime\"] else \"SHORT\"}')"

# 检查GEX情绪
python3 -c "
import json
g = json.load(open('data/gex_cache.json'))
print(g)"
```

**Step 2：信号分析**
```bash
# 对目标标的做完整分析
python3 brahma_brain/brahma_core.py BTCUSDT SHORT
# 或通过brahma_execute干跑
python3 scripts/brahma_execute.py BTC SHORT
# （不加 confirm 参数 = dry_run）
```

**Step 3：梵天评分解读**
记录并解读：
- score总分（/150）
- 入场区 entry_lo ~ entry_hi
- 止损 stop_loss 和 sl_pct
- TP1/TP2 和 RR（必须≥1.5才可入场）
- 体制×方向适配度（是否符合WR矩阵）

**Step 4：资金计算**
```
NAV = 1000 USDT
仓位公式：
  risk_per_trade = NAV × 1%  = $10（固定风险）
  position_size = risk_per_trade / sl_pct
  实际保证金 = position_size / leverage(5x)
```
手算示例：sl_pct=1.5% → position=$667 → 保证金=$133

**Step 5：开单记录（标准格式）**
```json
{
  "trade_id": "TRAIN-001",
  "symbol": "BTCUSDT",
  "direction": "SHORT",
  "regime": "BEAR_EARLY",
  "score": 142,
  "grade": 75,
  "entry_price": 65200,
  "sl": 66500,
  "tp1": 63500,
  "rr1": 1.31,
  "position_usdt": 650,
  "risk_usdt": 10,
  "open_time": "2026-06-13T10:35:00Z",
  "dry_run": true,
  "rationale": "BEAR_EARLY+SHORT=70%WR，4H OB结构完整，GEX负值，入场区确认"
}
```

**Step 6：守仓日志（每4H更新一次）**
记录：当前浮盈/浮亏%、体制是否变化、是否触及TP1/SL

**Step 7：平仓与复盘**
平仓后必须写：
- 结果（WIN/LOSS/TIMEOUT）
- 成功/失败的根因（结构判断 / 体制切换 / 评分偏差）
- 下一次同样场景我会怎么做

### 模拟开单5单目标成绩
| 指标 | 最低通关 | 优秀 |
|------|---------|------|
| 开单RR | 每单≥1.5 | 平均≥2.0 |
| 体制符合度 | 5/5正确 | 5/5正确 |
| grade门槛 | 每单≥70 | 平均≥75 |
| 完整记录 | 5单全有 | 附图分析 |
| 复盘质量 | 有根因 | 有改进方案 |

### 梵天实盘前置条件（模拟结束后评估）
```
✅ 5单完整记录
✅ 无违反红线操作（grade<70 / gap>0.5% / RR<1.5）
✅ 体制×方向全部符合WR矩阵
✅ 能独立解释每一分评分来自哪里
✅ 至少复盘一次TIMEOUT或LOSS，找到根因
→ 达标后方可申请开启实盘模式（dry_run=False）
```

---

## 更新后实训总览（v1.1）

```
Phase 0  系统认知       3天    必修
Phase 1  达摩院验证     5天    必修（含T1-5污染分析）
Phase 2  信号链剖析     5天    必修（含T2-5 360诊断）
Phase 2.5 守仓出场管理  3天    必修（新增）
Phase 3  体制×方向决策  5天    必修
Phase 4  风控与仓位     3天    必修
Phase 4.5 模拟开单1000U 5天    必修（新增）  ← 最重要体感环节
Phase 5  进化实战       7天    必修
Phase 6  毕业考核       2天    必修（含D13配置SOP）

总计：~38天  实训等级：达摩院认证量化工程师
```

---

*设计院增补决议 v1.1 · 2026-06-13 UTC*
*原则：最小增量，最大价值。每个新增都有代码、数据、闭环。*
