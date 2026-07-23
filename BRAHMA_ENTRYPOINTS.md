# 梵天系统 · 入口文档（SSOT）
<!-- 设计院自主生成 2026-07-23 | 验证后封印 -->

## 📌 唯一执行路径（封印）

```
外部调用层
  └─ brahma_1hao_analysis.py     ← 苏摩/cron 直接调用入口
       └─ brahma_engine.analyze() ← 35维矩阵主引擎
            ├─ brahma_scoring.confluence_score()  ← 汇总评分
            ├─ brahma_analysis_runner (格式化输出)
            └─ 36个独立Skill引擎（见下方）
```

## 🏗️ 三大文件职责边界

| 文件 | 行数 | 职责 | 状态 |
|------|------|------|------|
| `brahma_engine.py` | 3642行 | **主引擎调度层** — analyze()入口，调用36个Skill | ✅ 主路径 |
| `brahma_core.py` | 5180行 | **旧版主引擎** — 历史遗留，被tests/个别脚本引用 | ⚠️ 冻结勿改 |
| `brahma_scoring.py` | 2160行 | **评分汇总层** — confluence_score()，被engine导入 | ✅ 正常 |

### ⚠️ brahma_core.py 裁决（设计院2026-07-23）
- **现状**：5180行，brahma_engine.py 不导入它，但 tests/ 和部分脚本直接用
- **决定**：冻结不动，不删除，不新增功能
- **原因**：tests 依赖它做冒烟测试；删除需要迁移测试，风险高
- **规则**：新功能只加到 brahma_engine.py，brahma_core.py 作为只读快照

## 🔧 36个独立Skill引擎（已存在，已模块化）

```
分析层（输入klines/数据，输出score/结构）：
  smc_engine.py          → SMC结构分析（OB/FVG/CHoCH/BOS）
  market_state.py        → 市场状态（趋势/体制）
  divergence_engine.py   → 动量背离
  volume_engine.py       → 量能验证
  range_engine.py        → 区间/震荡分析
  options_engine.py      → 期权/资金费率情绪
  elliott_engine.py      → 艾略特波浪
  onchain_engine.py      → 链上数据
  pattern_engine.py      → K线形态
  order_flow_engine.py   → 订单流
  macro_engine.py        → 宏观指标
  ...（共36个）

门控层：
  timing_filter.py       → 时机门控（READY/MONITOR/WAIT）
  ssi_engine.py          → 轧空门控
  us_session_gate.py     → 美股时段门控 [新增 2026-07-22]

信号层：
  tradfi_signal_layer.py → TradFi信号层 Phase A [新增 2026-07-22]

工具层：
  brahma_bus.py          → 数据总线（TTL缓存）
  math_utils.py          → 数学工具（EMA/RSI/ATR）
  data_cache.py          → 行情缓存
```

## 📊 dependency_graph.json

机器可读的完整依赖图谱：`data/dependency_graph.json`
- 114个模块 × 导入/导出/消费者关系
- 用于调试 score 异常的根因追踪

## 🚫 禁止事项

1. **禁止新增功能到 brahma_core.py** — 已冻结
2. **禁止绕过 brahma_engine.analyze()** 直接调用子Skill
3. **禁止在 brahma_engine.py 新增大段逻辑** — 应新建独立Skill文件
4. **新增维度 = 新建 xxx_engine.py** + 在brahma_engine中注入调用点

## 📈 架构演进建议（下一步）

- **现在（已完成）**：36个独立Skill + dependency_graph
- **下一步（P1）**：给每个Skill加标准接口文档（输入/输出字段）
- **未来（P2）**：brahma_engine.py 瘦身至1000行以内（纯调度层）

---
*最后更新：2026-07-23 设计院 | 苏摩111审阅*
