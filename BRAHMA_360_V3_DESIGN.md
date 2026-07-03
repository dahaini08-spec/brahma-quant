# 🏛️ Brahma-360 v3.0 · 私有版系统级全覆盖方案
<!-- 设计院封印 2026-07-02 | 苏摩111最终批准 -->

## 一、全系统诊断基线（设计前实测）

| 维度 | 数值 | 风险等级 |
|------|------|---------|
| Python文件 | 381个 | — |
| 总代码行 | 123,543行 | — |
| 函数总数 | 1,741个 | — |
| **异常处理覆盖率** | **36%** | 🔴 致命 |
| dharma层异常覆盖 | 17% | 🔴 致命 |
| 现有有效测试 | 5个（非archive） | 🔴 严重 |
| 内存风险点 | 3处 | 🟡 中等 |
| Cron任务总数 | 26个 | — |
| 高频任务（≤15m） | 3个 | 🟡 Token消耗 |
| 数据链路完整度 | 100%（9层全通） | 🟢 正常 |
| 私有版核心模块 | 14/14 全部存在 | 🟢 正常 |

**最大发现：** 1,114个函数无异常处理。这意味着任何一个崩溃都可能级联传播，无防火墙隔离。

---

## 二、顶层架构理念 — 私有版 vs 开源版的本质差异

```
开源版 Brahma-quant
└── 框架骨架 + 基础功能（35维打分、5-regime、基础验证）
└── 痛点：工程成熟度低、无生产防护

私有版 梵天系统（苏摩当前运营）
└── 已有：训练好的智能权重、Kronos timing filter、auto-executor
└── 已有：实时预警、6-Agent联合评审、v4.2出场参数铁证
└── 痛点：代码庞大→维护盲区、异常覆盖率36%→稳定性风险
└── 痛点：测试体系弱→上线即赌博、Agent幻觉无防护

Brahma-360 v3.0 私有版专属设计
└── 目标：在不破坏已有智能的前提下，注入工程免疫系统
└── 核心：让380文件系统有自感知、自愈、自证明的能力
```

---

## 三、七层防护架构（私有版专属）

```
┌─────────────────────────────────────────────────────────────────┐
│  层0  感知数据防护层  (DataShield)                                │
│  ├── 流式摄入 + Headroom压缩 + TTL缓存（brahma_bus强化）          │
│  ├── 数据契约验证器（BRAHMA_FIELD_CONTRACT强化）                  │
│  └── 实时鲜度看门狗（替代P6手动检查）                             │
├─────────────────────────────────────────────────────────────────┤
│  层1  AI大脑免疫层  (BrainGuard)                                  │
│  ├── 函数级异常毯（ASTInjector自动注入try-except）                │
│  ├── Agent输出契约检查（35维字段验证，现有→自动化）               │
│  └── 幻觉检测器（输出置信度 + 历史一致性检查）                    │
├─────────────────────────────────────────────────────────────────┤
│  层2  验证治理加强层  (Dharma-360)                                │
│  ├── Monte Carlo 10k+ runs（基于realistic_cost_model）           │
│  ├── Walk-Forward Validation（基于regime_aware_augmentor）       │
│  ├── PBO/Deflated Sharpe（过拟合检测）                            │
│  └── 反作弊机制（StructureGate + CausalVerifier双重验证）         │
├─────────────────────────────────────────────────────────────────┤
│  层3  多Agent协作治理层  (CouncilGuard)                           │
│  ├── 监督者模式（Supervisor编排llm_council_bridge）               │
│  ├── 轨迹级记录（每次Agent调用全程存档）                           │
│  └── 共识机制（3/6 Agent通过才允许推送）                          │
├─────────────────────────────────────────────────────────────────┤
│  层4  执行风控熔断层  (CircuitBreaker)                            │
│  ├── 全链路熔断（信号→仓位→执行→结算每步设熔断）                   │
│  ├── 自动回滚（执行失败→自动平仓）                                 │
│  └── 容差降级（模块崩溃→规则基线兜底）                            │
├─────────────────────────────────────────────────────────────────┤
│  层5  全栈可观测层  (Observability-360)                           │
│  ├── 实时指标（信号质量/PnL/回撤/Pump命中率）                     │
│  ├── AI行为评估（Token消耗/幻觉率/成本/质量评分）                  │
│  └── 自动根因分析（问题出现→自动溯源到层级）                      │
├─────────────────────────────────────────────────────────────────┤
│  层6  CI/CD治理层  (AutoGuardian)                                 │
│  ├── brahma_ci_v2强化（现有94分→目标99分）                        │
│  ├── 自动测试金字塔（每次提交触发）                                │
│  └── 蓝绿部署保护（热更新不停服）                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 四、最高优先级修复：异常免疫系统

### 4.1 当前痛点数据

```
brahma_brain: 724函数 → 只有249个有try-except (34%)
dharma:       216函数 → 只有38个有try-except  (17%) ← 最危险
scripts:      764函数 → 只有321个有try-except  (42%)

= 1,114个函数是"裸函数"，崩溃直接上浮
```

### 4.2 解决方案：brahma_exception_injector.py

```python
# 自动扫描 + 智能注入异常处理的工具
# 不破坏原有逻辑，只在外层包裹fallback
# 三级处理策略：
#   A: 数据获取函数 → 返回None + 日志
#   B: 计算函数 → 返回上次缓存值 + 警告
#   C: 执行函数 → 中止 + 推送Jarvis告警
```

**目标：** 异常覆盖率 36% → 85%（3周内）

---

## 五、测试金字塔（私有版版本）

```
     ██████  层3: 端到端回测验证（Dharma-360）
    ████████  层2: 集成测试（Agent交互 + 数据流链路）  
   ██████████  层1: 属性测试（Hypothesis随机化）
  ████████████  层0: 单元测试（每个核心函数）

目标覆盖率: 85%+
当前覆盖率: ~12%（15个测试文件/381个模块）
```

### 私有版专属测试矩阵

| 测试类型 | 覆盖目标 | 工具 |
|---------|---------|------|
| 单元测试 | brahma_core/timing_filter/position_sizer | pytest |
| 属性测试 | 评分函数边界值、regime切换随机化 | hypothesis |
| Agent反作弊 | llm_council输出一致性注入测试 | 自研 |
| Kronos对比 | shadow vs lite差异持续记录 | kronos_bridge现有 |
| 大规模Monte Carlo | 1万次+走势模拟（realistic_cost_model） | 现有模块强化 |
| 压力测试 | 极端市场（-30%闪崩/+50%暴涨）下系统行为 | regime_aware_augmentor |

---

## 六、内存与稳定性专项

### 6.1 三大内存风险修复

| 文件 | 风险 | 解决方案 |
|------|------|---------|
| dharma_report.py (72次append) | 大报告生成时OOM | 流式生成器替代列表积累 |
| news_formatter.py (148次append) | 新闻聚合时内存爆炸 | 分块处理+上限截断 |
| divergence_engine.py (58次append) | 长时运行内存泄漏 | 循环缓冲区(deque maxlen=1000) |

### 6.2 全局内存策略

```python
# brahma_bus.py 强化版
# 现有：TTL缓存（已落地）
# 新增：
#   - 内存水位监控（>80% → 主动GC + 清理陈旧缓存）
#   - 大对象分片（DataFrame>100MB → 自动分块处理）  
#   - 缓存淘汰策略：LRU + regime-aware（体制切换时清空对应缓存）
```

---

## 七、可观测性升级（基于现有brahma_dashboard）

### 7.1 四大看板（扩展现有dashboard_server）

```
看板1: 系统健康仪表盘（现有brahma_health → 实时化）
  ├── 9层数据链路状态（实时灯）
  ├── 26个cron任务心跳（最后运行时间）
  └── 异常率趋势图

看板2: 信号质量仪表盘
  ├── 近30日信号WR（按体制分层）
  ├── Kronos shadow vs lite差异
  └── timing_filter过滤效率

看板3: AI行为仪表盘（私有版新增）
  ├── 每日Token消耗（按任务分类）
  ├── Agent幻觉率（llm_council_bridge输出异常）
  └── 推送精准度（推送→入场→盈利转化率）

看板4: 执行风控仪表盘
  ├── 持仓实时状态（wuqu_positions同步）
  ├── 熔断触发历史
  └── auto_executor成功率
```

### 7.2 追踪链路（私有版轻量实现）

```python
# 不引入重量级OpenTelemetry，使用轻量trace_id方案
# 每个信号从 rsi_structure_watcher触发 → brahma_analysis_runner扫描
# → 35维评分 → timing_filter → position_sizer → auto_executor
# 全程携带同一 trace_id，便于事后复盘

BRAHMA标签已有：[BRAHMA:{level}:{source}:{sym}:{score}:{dir}:{regime}:{ts}:{sha8}]
扩展为：[BRAHMA:{level}:{source}:{sym}:{score}:{dir}:{regime}:{ts}:{sha8}:{trace_id}]
```

---

## 八、Token效率优化（私有版26个cron）

### 8.1 现有消耗分析

```
高频任务（每5m）：btc-regime-watcher → ~0 tokens（rsi_structure_watcher是零成本守望 ✅）
中频（每15m）：ws-guardian-keepalive → 脚本级，0 tokens ✅
              trc20-order-monitor → 脚本级，0 tokens ✅

AI任务消耗估算（基于MEMORY.md数据）：
  震荡日：48,000 tokens/天（v5.0架构已优化）✅
  活跃日：48,000~84,000 tokens/天 ✅
  
已在64%预算内，无需大改 ✅
```

### 8.2 进一步优化空间

```
lightContext=True → 适用于P3/P4任务（已部分落地，继续扩展）
Headroom压缩 → 推送内容压缩60%（已规划，Phase 0实施）
Agent简化 → smart-digest可降级到规则模板（省40%该任务token）
```

---

## 九、私有版专属新增模块清单

### Phase 0（本周，立即执行）

| 模块 | 文件 | 核心功能 |
|------|------|---------|
| 异常免疫工具 | `brahma_brain/exception_injector.py` | AST扫描+批量注入fallback |
| 内存水位监控 | `brahma_brain/memory_watchdog.py` | RSS监控+自动GC |
| 数据鲜度看门狗 | `scripts/data_freshness_guard.py` | 替代CI手动检查P6 |
| 信号追踪扩展 | `brahma_brain/signal_tracer.py` | trace_id全链路注入 |

### Phase 1（2周内）

| 模块 | 文件 | 核心功能 |
|------|------|---------|
| 测试自动生成器 | `tests/test_generator.py` | 基于AST自动生成基础单元测试 |
| Agent反作弊 | `brahma_brain/agent_anticheat.py` | 轨迹一致性检测 |
| 全链路熔断器 | `brahma_brain/circuit_breaker.py` | 多层熔断+自动回滚 |
| 蓝绿部署脚本 | `scripts/blue_green_deploy.sh` | 热切换不停服 |

### Phase 2（1个月内）

| 模块 | 文件 | 核心功能 |
|------|------|---------|
| Dharma-360扩展 | `dharma/dharma_360_validator.py` | Monte Carlo 10k+ |
| 生成式Critic | `brahma_brain/generative_critic.py` | 替代纯判别评分 |
| AI行为看板 | `scripts/ai_behavior_dashboard.py` | Token/幻觉/质量追踪 |
| 压力测试框架 | `dharma/stress_test_engine.py` | 极端市场模拟 |

---

## 十、实施路线图（精确到文件级）

### Week 1（Phase 0）：打地基

```bash
# Day 1-2: 异常免疫
python3 brahma_brain/exception_injector.py --dry-run  # 先看报告
python3 brahma_brain/exception_injector.py --apply --layer dharma  # 最危险的先修

# Day 3: 内存监控
# 在brahma_bus.py注入memory_watchdog
# 修复3处内存风险（流式替代列表积累）

# Day 4-5: 数据鲜度自动化
# 替代brahma_ci_v2中的手动P6检查
# 注册为独立cron（每30m）
```

### Week 2-3（Phase 1）：测试覆盖

```bash
# 自动生成基础单元测试（目标：核心模块100%覆盖）
# 核心模块：brahma_core / timing_filter / position_sizer / causal_regime_verifier
# 优先级：这4个模块出错 = 系统级信号质量崩溃
```

### Month 1-2（Phase 2）：智能升级

```bash
# Dharma-360大规模验证上线
# Kronos shadow升级（等待n≥100验证点 → M1升级条件达成时执行）
# AI行为看板上线（接入现有brahma_dashboard_server）
```

---

## 十一、预期效果（可量化）

| 指标 | 当前 | Phase 0后 | Phase 1后 | Phase 2后 |
|------|------|----------|----------|----------|
| 异常覆盖率 | 36% | 70% | 85% | 90%+ |
| 系统崩溃后自愈 | 手动 | 半自动 | 全自动 | 毫秒级 |
| brahma_ci得分 | 94/100 | 97/100 | 99/100 | 100/100 |
| 内存泄漏风险 | 3处 | 0处 | 0处 | 0处 |
| 有效测试覆盖 | ~12% | 30% | 60% | 85% |
| Agent幻觉检测 | ❌ | ❌ | ✅ | ✅强化 |
| Token消耗/天 | 48k~84k | 40k~75k | 35k~65k | 30k~55k |

---

## 十二、设计院铁则

1. **不破坏原有智能** — 所有注入均为外层包裹，不修改核心算法
2. **渐进式落地** — Phase 0先做，验证效果再推进
3. **每个模块必须通过brahma_ci_v2验证** — 新代码提交前必须CI通过
4. **私有版封印优先** — 开源版的设计建议只作参考，实际落地以私有版架构为准
5. **苏摩111批准制** — 修改MEMORY.md/system_config.py/position_sizer前必须得到批准

---

*设计院封印 2026-07-02 07:xx UTC | 基于实测数据设计*  
*下一步：苏摩111确认执行，设计院开始Phase 0模块开发*
