# 任务：梵天系统全链路深度排查
## 目标
逐层排查所有环节，消除中断根因，恢复系统满血状态
## 步骤
- [ ] L1: 基础环境（内存/进程/qdrant/segfault根因）
- [ ] L2: 数据层（brahma_bus/data_cache/klines稳定性）
- [ ] L3: 评分层（brahma_core完整性/35维覆盖率）
- [ ] L4: 报告层（brahma_full_report/360自检真实覆盖）
- [ ] L5: 执行层（auto_executor/position_sizer/NAV接入）
- [ ] L6: cron任务（43个任务健康状态）
- [ ] L7: git状态（未提交变更/接入位置确认）
## 接入位置
全系统排查，不修改，只诊断
## 冒烟测试
每层排查完输出状态表
