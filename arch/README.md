# 梵天四层架构 — Phase 1 占位

## 目录规划（武曲Paper 200条后迁移）
- layer0_data/     数据层：brahma_state / signal_log / wuqu_paper
- layer1_compute/  计算层：brahma_brain（拆分后的3个文件）
- layer2_decision/ 决策层：push_hub / dd1_confirm_gate
- layer3_execution/ 执行层：executor / live_signal_settler
- core_utils/      原子工具：state_writer / schema_validator

## 迁移时间表
- 武曲Paper 50条：评估WR，确认策略方向
- 武曲Paper 100条：开始 layer1_compute 模块拆分
- 武曲Paper 200条：完整四层架构迁移 + brahma_brain重写

## 代码冻结协议（Phase 0 生效，2026-06-08）
禁止修改：brahma_brain评分逻辑 / GapGate / BridgeGate参数
允许修改：SSOT数据参数 / Bug修复 / 监控脚本
