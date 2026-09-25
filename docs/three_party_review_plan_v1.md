# 梵天设计院·三方联合顶层全局审核方案 v1.0

> 日期: 2026-09-25 | 发起: 苏摩111指令 | 状态: 待批准
> 背景输入: 08:15五层深度评估(254文件×107K行) + 08:22假死事故复盘 + OCR(open-code-review)工程化对标
> 核心命题: **把一次性评估升级为常设审核制度**——梵天的下一步成熟化不是加第95维，而是把前94维里 "must not go wrong" 的部分从AI手里收回来，钉进代码。

---

## 1. 三方定义与权责

| 方 | 角色 | 权责 | 禁止 |
|---|---|---|---|
| **P1 决策方** | 苏摩111 | 批准封印/否决方案/优先级裁决/验收签字 | 不直接改代码 |
| **P2 评审方** | AI设计院(sub-agent分治) | 产结构化发现、起草修复方案、撰写规则文档 | 发现不带证据不得进修复队列 |
| **P3 证据方** | 确定性工程(grep/AST/单测/冒烟/回放) | 实锤或证伪P2意见、执行冒烟、维护漂移检查器 | 不做主观判断——只回答"真/假/无法判定" |

> 三方映射OCR: P2=Agent(动态决策), P3=确定性工程(硬约束), P1=人类审批(gate)。
> 关键翻转: **梵天系统自己成为第三方**——系统用脚本自证，AI只提案。

## 2. 审核哲学（OCR提炼四条，全案宪法）

1. **必须不炸的环节由代码保证，不由AI自觉保证。** 08:22事故本质="启动时靠脚本自觉不装全家桶"。
2. **不对称过滤:** 保留一条错误意见损失几秒注意力；删掉一条正确意见永久毁灭一个真实发现。默认放行，只有字面证伪才删除。
3. **常量唯一定义点:** MIN_SCORE_OPEN、SL参数、WR矩阵、NAV语义——全仓只允许一处定义，其余全部引用（OCR的PromptTokenLimit先例）。
4. **失败有记忆:** 每个失败模式进代码注释+记忆档案，注释即事故档案（OCR: "One review run reattempted a single finding 6 times before this existed"）。

## 3. 证据等级制度（P3的裁判语言）

| 等级 | 定义 | 采信规则 |
|---|---|---|
| **L1 字面证据** | grep一行实锤（如常量值、函数是否存在、行号内容） | 自动采信，直接进修复队列 |
| **L2 可执行证据** | 单测/冒烟/回放/复算全绿（如settlement.py复算NAV链路） | 采信，标"已验证" |
| **L3 AI推理** | 评审员推断，无实锤（如"FVG磁铁应有效"） | 只标"待验证"，禁止作为修复依据 |

> 这就是review_filter的Ground A/B: AI意见默认放行；只有L1/L2能删除或确认。
> L3意见的处置: 进入P3验证队列排队，跑出L1/L2才升级。

## 4. 审核矩阵（五层 × 三方）

| 层 | P2评审焦点 | P3验证工具 | 今日种子发现 |
|---|---|---|---|
| **A 分析链路** | 10步强制链路逐环节归属(确定性/动态)；monkey-patch风险；try/except吞错 | 链路追踪脚本+异常注入 | runner 1632行职责堆叠；评分叠加链无审计 |
| **B 引擎** | 94维×IC验证状态；方仓5特征有效性；FVG/OB几何假设的统计验证 | dim_ic_results复算+回测 | IC结论文件缺失；FVG无验证；方仓Qdrant降级 |
| **C 决策账本** | NAV语义统一；WR闭环；SL参数唯一定义；ATR1H全路径 | 账本复算(33笔)+grep | NAV三重语义断裂(100/10k/100k)；WR不回写；ATR1H仅decision_engine层 |
| **D 数据运维** | 46条cron健康度；进程复活闭环；依赖安装红线 | supercronic日志审计+冒烟 | 假venv陷阱；兜底pip(已修)；5幽灵模块(已清) |
| **E 知识宪法** | 封印常量↔代码同值；文档-代码同步义务 | drift_checker(新建) | get_regime_mult已废但宪法仍封印；MIN_SCORE=100 vs 封印140 |

## 5. 发现→封印 全流程（七步，每步有门）

```
① 发现(P2) → ② 实锤(P3跑L1/L2) → ③ 方案(P2起草+P3影响面验证)
→ ④ 批准(P1苏摩111) → ⑤ 实施(P2执行+备份可回滚) → ⑥ 冒烟(P3全绿)
→ ⑦ 封印(宪法+MEMORY索引行, ≤12KB红线)
```

- 缺任何一步 = 未封印（延续封印铁律: 代码完成+调用验证+full_report可见+冒烟全绿）
- ⑦的产物: 修复写代码注释(日期+根因)，宪法/记忆只留索引行

## 6. 分阶段路线图

### Phase 0 · 立即止血（今日，1-2小时）
| 任务 | 内容 | 验收 |
|---|---|---|
| 0.1 修3处漂移 | ①MEMORY.md删除已废乘数表(留索引) ②封印值对齐MIN_SCORE_OPEN=100或升级代码回140(P1裁决) ③trader_brain L780旧路径下线 | grep全绿+冒烟 |
| 0.2 建drift_checker | scripts/drift_checker.py: 封印常量清单↔代码grep自动比对，输出L1实锤 | 对今日3处漂移能全抓到 |
| 0.3 挂cron | drift_checker进现有script-only cron（不耗AI调用额度） | cron_health_board可见 |

### Phase 1 · 制度化（本周，3-5天）
| 任务 | 内容 | 验收 |
|---|---|---|
| 1.1 常量唯一定义点 | MIN_SCORE_OPEN/SL参数/WR矩阵/体制映射收进单一config module，全仓引用 | grep无第二处硬编码 |
| 1.2 推送铁证层 | VIP卡片每个$X必须能在full_report JSON溯源(FVG/OB/清算价位)，找不到=重写不推送 | 推送前校验代码上线 |
| 1.3 失败记忆 | (cron名,工具)失败计数+分级升级(借tool_failure_streak) | 注释含历史事故 |

### Phase 2 · 账本重建（下周，需111逐项批准）
| 任务 | 内容 | 验收 |
|---|---|---|
| 2.1 NAV统一语义 | 以settlement.py为模板(全仓最干净)，USD账本累加为唯一真相 | 33笔复算差=fees |
| 2.2 WR反馈闭环 | paper_tracker回写wr_matrix，manual单单独口径 | 两口径标记清晰不混用 |
| 2.3 ATR1H下沉 | SL验证从decision_engine层下沉到三条执行路径 | 每条路径冒烟含ATR1H用例 |

### Phase 3 · 深水区（1-2周，架构级）
- 3.1 确定性/动态切割审计: 94维逐步标注归属层，能收回的从AI手里收回
- 3.2 cron AI调用轨迹记录+月度蒸馏 → 梵天专属"平均轮次/失败模式"常量（OCR反蒸馏法）
- 3.3 sealed input式数据快照: cron开始时hash数据文件，推送时校验一致性

## 7. 质量门禁与红线（延续9.23铁律）

- 主session单run≤80工具调用 → 审核一律spawn sub-agent
- AI cron总调用≤33/天 → drift_checker/cron_health全用script-only
- MEMORY.md≤12KB → 新封印写daily memory，MEMORY.md只留索引行
- 禁止往活环境pip install（08:22事故红线，已封印）
- 每周校准: 统计P2意见命中率(AI发现中L1/L2实锤比例) → 校准评审prompt（生产轨迹反蒸馏）
- 验收不对称: 审核报告接受Recall偏低，但Precision必须高——宁漏勿噪（benchmark实证trade-off）

## 8. 发现登记格式（R-registry规范）

```
[R-xxx] 层级 | 严重度(P0/P1/P2) | 证据等级(L1/L2/L3)
现象: 一句话
复现: 文件:行号 + grep命令(可执行)
方案: 修复描述 + 影响面
验证: 冒烟命令 + 预期输出
状态: 发现/实锤/已批/已修/封印/否决
```
归档: `.claude/context/agents/R-registry.md`，五层报告(A-E)降级为背景参考。

## 9. 种子发现清单（今日实锤，直接进R-registry）

| ID | 发现 | 证据 | 严重度 |
|---|---|---|---|
| R-001 | get_regime_mult已废除(P0改革9.20)但宪法仍封印乘数表 | L1 grep | P0 |
| R-002 | MIN_SCORE_OPEN实际=100(9.17降)非封印140 | L1 grep | P0 |
| R-003 | trader_brain L780旧路径兼容score≥80 | L1 grep | P0 |
| R-004 | NAV三重语义: 100/10,000/100,000 | L2 复算 | P0 |
| R-005 | WR闭环断裂: paper_tracker不回写wr_matrix | L1 | P1 |
| R-006 | IC结论文件不存在，dim_ic_results是评分均值非IC | L1 | P1 |
| R-007 | FVG磁铁纯几何假设无统计验证 | L3→待P3 | P1 |
| R-008 | 10%NAV合计无硬门控 | L1 | P1 |
| R-009 | ATR1H验证仅decision_engine层，三条执行路径均无 | L1 | P0 |
| R-010 | 假venv陷阱(bin/python3→wrapper→系统python) | L1 | P2(文档化) |
| R-011 | start_supercronic兜底pip(已修9.25) | 已封印 | ✅ |

## 10. 成本与批准

- Phase 0: 零额外成本，今日完成，全为修漂移
- Phase 1: 3-5天AI工时，无外部API成本
- Phase 2/3: 涉及brahma_core/decision_engine/ledger改动，**逐项需苏摩111批准后封印**
- 审核本身: Phase 2试点OCR Delegation模式(零API key)扫scripts/，意见质量达标才进流程
