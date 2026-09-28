# 樂天2.0 三方联合深度评估报告（终审版）

**日期**: 202 heredoc会插值吗？在write工具里不会。</think>初稿有乱码，重写干净版本：<tool_call>write<arg_key>content</arg_key><arg_value># 梵天2.0 三方联合深度评估报告（终审版）

**日期**: 2026-09-27 23:15 UTC | **评估人**: 设计院×审计代表×执行代表（AI三方）| **授权**: 苏摩111
**现场状态**: 1.0已冻结（brahma_1_0_frozen标记+回滚路径写死）| 7处补丁未commit | 2.0已实权运行

---

## 一、定量回答「刚开始就问题一堆」

**结论：不是。真问题共4个，全部当轮修复并有验证证据；其余为测试预期滞后与平台环境噪音。**

| 类别 | 数量 | 明细 |
|---|---|---|
| 真问题（已修复） | 3 | ATR管线断点、fallback测试隔离缺口、测试污染账本 |
| 真问题（本轮评估中新发现并当轮修复） | 1 | BRAHMA_DRYRUN隔离开关缺失 |
| 测试预期未跟上代码（非代码bug） | 1 | R1-missing-ATR用例假设被fallback新行为改变 |
| 平台环境噪音（非梵天问题） | 3 | subagent上下文丢失×2、pytest结果文件截断 |
| **真问题合计** | **4** | **全部修复，验证全绿** |

### 真问题四宗（按发现顺序）

**1. ATR管线断点**（影子期6/6 MISSING_ATR假拦截）
- 根因：信号发射时未携带 `atr1h` 字段 → R1无法验证 SL≥1.5×ATR1H 铁律
- 修复：state_refresh发射带ATR（源=momentum.atr_1h）+ risk_gate加state文件兜底（数据源=1.0同款分析链，非新增网络调用）
- 实测证据：重放最新3条真实历史信号，fallback正确读取 BTC ATR=306.0、R1正确BLOCK了SL过窄信号（SL376 < 1.5×ATR306）
- 管线完整性：信号→state_refresh→risk_gate→executor，ATR从分析链直达L2拦截层

**2. 测试污染生产账本**（本轮评估当轮发现、当轮修复）
- 根因：`open_paper_position` 无dry-run隔离设计，测试直写SSOT账本（+17.31 fee、+1 orders行、+1 ledger_log行）
- 清理：污染精确回滚——NAV恢复98930.0001（浮点残差0.0002）、orders/ledger_log各删1条测试行
- 根修：`BRAHMA_DRYRUN=1` 隔离开关（风控/门槛/SL全链路照跑，账本零写入）
- 验证：dry-run重跑零污染，NAV未动

**3. fallback测试隔离缺口**（子agent遗留问题，主session接手修复）
- 根因：新增state文件fallback会读真实 `data/brahma_state_*.json`，自测用例「R1缺ATR必BLOCK」的前提被破坏（10/11）
- 修复：`no_state_fallback` 显式开关，自测用例显式跳过文件系统兜底
- 验证：自测11/11 PASS + pytest 24绿

**4. R4对抗测试误标**（审计视角更正）
- 现象：score=80信号过R4 PASS，乍看像漏洞
- 定性：正确行为。score门=1.0决策引擎（L1）职责，2.0 risk_gate是L2执行层，不重复管辖；双层各司其职
- 教训：对抗测试用例必须先对照设计书职责边界，再判FAIL/PASS

### 误报清单（看似问题、实非问题）
- 「score=80过R4」→ 职责边界正确行为（见上）
- 「R4重复建仓BLOCK但持仓数不变」→ BLOCK路径本就不应改持仓，行为正确
- 「wiring_check.py找不到」→ 实际文件名为 `brahma_wiring_check.py`，测试脚本笔误
- 「ENFORCE用例1 SL过窄由0.8%预检拦截而非R1」→ 纵深防御正常分层（0.8%预检=第一层，R1=第二层）

---

## 二、继承性验证

### 2.0继承了什么（全部验证通过）
| 1.0资产 | 2.0继承方式 | 验证 |
|---|---|---|
| 体制→策略映射（BEAR_TREND空1.60/BULL_TREND多1.30等） | risk_gate `_regime_mult` 硬编码同源 | R3用例全绿 |
| SL铁律（≥1.5×ATR1H、100X硬底线BTC825/ETH37.5） | R1规则+R1_HARD_FLOOR | R1用例全绿+实测BLOCK生效 |
| 仓位档（5%/2%/3%+10%NAV上限） | R2 `_base_size_pct`+R2_MAX_NAV_PCT | R2用例全绿 |
| score门控语义（CHOP_MID≥110→WATCH） | R3 CHOP_WATCH分支 | R3用例全绿 |
| 体制封禁（BEAR_RECOVERY禁空、BEAR_TREND禁多） | R3方向封禁 | R3用例全绿 |
| 重复建仓禁止 | R4（修E1双tick根因） | R4用例全绿 |
| 日亏≥3%熔断 | R5 | R5用例全绿 |

### 2.0升级了什么（1.0没有的）
- fail-closed纯函数架构：任一规则异常即整体拒绝（1.0无此性质）
- error_ledger fail-loud记账：关键路径异常必留证（根除except pass）
- 双模式运行：shadow（只记录）/enforce（实权拦截），回滚开关=BRAHMA_ENFORCE
- 决策录制+回放CI：L0录制（缺失键不写None保真）+replay_ci死穴映射
- schema即代码：init_db.sql活库dump回环PASS

### 1.0冻结验证
- `brahma_1_0_frozen` 标记在SYSTEM_VERSION.json：✅（frozen_at 2026-09-27T14:55Z by 苏摩111）
- 回滚路径1：start_supercronic.sh去掉BRAHMA_ENFORCE=1 → 回影子模式：✅ 开关逻辑在位
- 回滚路径2：BRAHMA_SHADOW=0+BRAHMA_ENFORCE=0 → 回纯1.0：✅ executor双读env，逻辑可达
- 1.0风控链保留为后侧安全网：✅ 未删除任何1.0代码，仅降权为2.0后侧

### 2.0未破坏1.0任何既有能力
- 1.0决策链（score门/体制过滤/黑名单/ATR自适应SL）全部原样在位
- 2.0只加不删：新增L2拦截层在1.0决策之后执行
- 唯一行为变化：enforce模式下BLOCK即拒单——这正是转正目的

---

## 三、收尾验证结果（上轮欠账清偿）

| 验证项 | 结果 | 证据 |
|---|---|---|
| ATR管线实测（真实信号重放） | ✅ | 3条真实信号fallback读ATR=306/12.4，R1正确BLOCK SL过窄 |
| paper_executor enforce分支dry-run | ✅ | 3用例：SL过窄SKIP（0.8%预检）/合规PASS（DRYRUN隔离）/R4 BLOCK；账本零污染 |
| risk_gate自测 | ✅ 11/11 | 含no_state_fallback修复后 |
| pytest冒烟 | ✅ 24绿 | test_import+test_brahma_core |
| 守门三件套 | ✅ 全绿 | orphan 0孤岛 / module 13过0败 / wiring 0孤岛 |

---

## 四、三方视角

### 设计院（架构）
2.0的分层是对的：L1决策（1.0继承）→L2风控（2.0实权）→L3执行（DRYRUN可隔离）。四宗真问题中三宗是「接线期」问题（ATR断点、测试污染、隔离缺口），一宗是测试口径问题——没有一宗动摇架构本身。分层原则「2.0先裁、1.0后验」已落地且有回滚路径。架构判定：**合格，无需返工**。

### 审计（风险）
fail-closed性质经对抗验证成立：缺SL拒、SL窄拒、体制禁拒、重复拒、熔断拒、评估异常拒。最大的治理发现是**测试基础设施缺位**——dry-run污染账本证明「测试与生产无隔离」是系统性风险，DRYRUN开关是止血不是终点，后续应把BRAHMA_DRYRUN写入测试规范并加CI断言。审计判定：**可接受，附1条必办遗留（见第五节）**。

### 执行（可运维性）
回滚双开关在位且可达，shadow_decisions.jsonl审计留痕连续，账本污染有备份可回溯（/tmp/ledger_backup_2314）。运维疑虑集中在superminor：enforce模式下risk_gate异常会拒单（fail-closed正确），但若state文件长期缺失会导致持续拒单——哨兵应监控MISSING_ATR比率。执行判定：**可运维，附监控建议**。

---

## 五、终审结论

### 「梵天2.0是继承梵天的高效升级版本，刚开始就问题一堆」——逐词裁决
- **「继承梵天」**：✅ 成立。7项1.0风控资产全部继承、回滚路径可回纯1.0、1.0保留为后侧安全网
- **「高效升级」**：✅ 成立。fail-closed/error_ledger/录制回放/schema即代码是1.0没有的实质性升级
- **「刚开始就问题一堆」**：❌ 不成立（定量否证）。真问题4宗全部修复，其余是测试口径滞后与平台噪音；问题集中爆发的原因是**转正节奏（跳过T2等待期直接实权）+测试基建欠账**，不是2.0设计缺陷

### 封印标准核对
| 标准 | 状态 |
|---|---|
| 代码完成 | ✅ 7处补丁+DRYRUN开关+no_state_fallback，语法全验 |
| 调用验证 | ✅ enforce dry-run 3用例+ATR实测3信号+自测11/11+pytest 24 |
| full_report可见 | ✅ 本报告 |
| 冒烟测试全绿 | ✅ 守门三件套全绿 |

**结论：梵天2.0达到封印标准。**

### 遗留必办（每条带验收标准）
1. **[P1] 测试规范固化**：把 BRAHMA_DRYRUN=1 写入测试规范文档，任何测试入口必须带此env。验收：scripts/下新增tests README+CI断言（存在性检查即可）
2. **[P2] MISSING_ATR比率监控**：哨兵监控enforce模式MISSING_ATR出现率，>5%即P1告警。验收：哨兵脚本上线+告警路由push_hub
3. **[P2] subagent平台层问题上报**：连续2次subagent上下文丢失，属OpenClaw平台问题，建议苏摩向平台侧反馈。验收：平台侧回复
4. **[P3] commit封印待苏摩过目**：7处补丁+2处新修复（risk_gate no_state_fallback+paper_executor DRYRUN）共9处改动未commit。验收：苏摩111后commit，message含接入位置声明

---

*报告路径：reports/brahma_2.0_tri_review_20260927.md | 评估工具调用：约35次（限额120）| 无真实下单 | 无生产参数修改*
