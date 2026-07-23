# Phase 2 · 减法工程 + online_learner 接入
设计院立项 · 2026-07-23 | 长期目标

## 当前基线
- 总代码量: 109,286行
- archive/残留: 72个文件 11,064行
- 真孤儿模块: 14个 2,465行
- 目标: 削减至 ~65,000行 (-40%)

## 三阶段执行

### 阶段A: archive清理（本周，零风险）
- 删除 archive/ 整目录（旧版本备份，已无用）
- 预计释放: 11,064行

### 阶段B: 孤儿模块处置（两周内）
优先接入（有价值）:
- online_learner_v2   → 每周运行，动态调整维度权重
- vectorbt_simfactory → 每月回测，建立benchmark
- headroom            → 资金余量保护

归档/删除（无用）:
- offline_adapters    → archive后删除
- tardis_liq_layer    → archive后删除
- exception_injector  → 直接删除（测试工具）

### 阶段C: 主链路精简（一个月）
- 合并 brahma_core_entry.py → brahma_engine（消除第三入口）
- 合并重复 formatter → 保留 brahma_brain 版本
- 拆分 brahma_engine.py（3641行）→ 按层拆分为3个子模块

## online_learner_v2 接入设计
触发条件: 每周日 00:00 UTC（brahma-online-calibrate cron已存在）
输入: episodic_memory/ + calibration_feedback.jsonl（≥30条后生效）
输出: signal_weights.json（维度权重更新）
约束: 调整幅度单次≤15%，防止过拟合

## 触发条件（何时启动阶段B）
- calibration_feedback.jsonl ≥ 30条实盘数据
- 或苏摩111指令
