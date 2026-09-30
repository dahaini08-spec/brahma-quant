# 任务：梵天2.0 工程化重构（功能保持不变，仅提升质量）
## 目标
按 reports/refactor20260930/REFACTOR_PLAN.md 五阶段执行，验收门=冒烟13/13+wiring 0孤岛+git封印
## 步骤
- [x] P0-1: 三路审计（A/B/C 报告落盘）
- [x] P0-2: 重构总方案 REFACTOR_PLAN.md
- [x] P0-3: 死代码三重验证（7候选→3实锤）
- [ ] P1-1: 66处裸except机械修复
- [ ] P1-2: 死代码归档 dharma 三文件
- [ ] P1-3: SYSTEM_VERSION.json 幽灵入口修正
- [ ] P1-4: 冒烟13/13+wiring+commit封印
- [ ] P2-1: 重复RSI/EMA委托math_utils
- [ ] P2-2: 乘数表收敛regime_config
- [ ] P3: API收敛（1hao改名+反向依赖下沉）
- [ ] P4: 巨型函数外挂拆分（最后做，等苏摩111）
## 接入位置
见各阶段 commit message
## 冒烟测试
每阶段后: python3 brahma_brain/brahma_smoke_test_v2.py
