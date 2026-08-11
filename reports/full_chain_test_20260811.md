# 梵天全流程链条测试报告
**日期**: 2026-08-11 | **触发**: 大刀阔斧重构后上游改造下游传导验证

## 结论
**43✅ 0❌ 2⚠️ (95% 通过)**

所有核心层链路传导正常，2个警告均为架构正常行为（非Bug）。

## 逐层验证结果

### L0 拆分模块 import 链 ✅
| 模块 | 状态 |
|------|------|
| brahma_core | ✅ |
| brahma_core_block_a/b/c | ✅ |
| brahma_core_analyze_steps | ✅ |
| brahma_core_step4 | ✅ |

### L1 零成本守望层 ✅
- `rsi_structure_watcher` import 正常
- 对外接口：`run()`，`detect_events(data, prev_state, sym)` 是内部函数
- ⚠️ 警告原因：测试直接调内部函数，架构正常

### L2 分析引擎层 ✅
- `brahma_analysis_runner.run_analysis()` 返回 6662 字符报告
- 报告含：体制/评分/操作指令/CST时间 ✅
- 去AI味：0个禁用词 ✅
- `brahma_core.analyze(ETH)`: regime=CHOP_MID score=14.6 dims=43

### L3 决策树 + 方仓层 ✅
- `brahma_decision_engine.decide()` 正常调用，返回 action=SKIP
- `fangcang_engine.get_fangcang_context()` 正常调用

### L4 执行层 ✅
- `auto_executor.execute_signal()` 可调用
- `position_sizer.get_position_pct()` 正常：score=130 → 120% EXPLORING+MACRO_GATE

### L5 结算闭环 ✅
- `signal_settler.settle_signal()` ✅
- `ic_tracker.calc_ic() / compute_all_ic()` ✅
- `ev_feedback.on_settlement() / update_ev()` ✅

### L6 推送链路 ✅
- `push_hub.push_signal_card()` ✅
- `signal_card_formatter.format_vip_card()` ✅

### L7 全链路端到端 ✅
- BTC: regime=CHOP_MID score=53.6 dims=37 grade=72
- ETH: regime=CHOP_MID score=14.6 dims=43 grade=100
- 双标的总耗时: 3.9s
- format_report: 78行正常输出

### L8 TRADFI 传导 ✅
- `tradfi_signal_layer.compute_tradfi_context()` 可调用
- `market_state.analyze()` 正常，tradfi 字段按需注入（仅 TRADFI_STOCK 类）
- BTC/ETH 为 `BTC_ETH` 类型，tradfi C3 段不触发属正常架构

## 架构说明
```
信号触发层 (rsi_structure_watcher.run)
    ↓ 事件触发
分析层 (brahma_analysis_runner → brahma_core.analyze)
    ↓ 35+维 breakdown
决策层 (brahma_decision_engine.decide + fangcang_engine)
    ↓ action
执行层 (auto_executor → position_sizer → binance_cli)
    ↓ 成交
结算闭环 (signal_settler → ic_tracker → ev_feedback)
    ↓ 反馈
推送层 (push_hub.push_signal_card → Jarvis)
```

## 封印
commit: 见下方 git log
