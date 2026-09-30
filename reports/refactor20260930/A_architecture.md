# A路审计：架构总结+数据流（v2 最终版）
> 2026-09-30 · 主会话代跑 + A-v2 子代理成功回报合并 · 全部结论文件:行号证据

## 0. A-v2 子代理核心增量结论（本轮最有价值修正）
1. **SYSTEM_VERSION.json 幽灵声明扩大化**：main.py/executor.py 之外还有 3 个声明文件不存在（position_monitor.py/market_regime.py/multi_agent_council.py 全部 ls 失败实证），:220 自认 main.py 已归档但顶层未更新 → 该版本文件整个 key_params/council 区块是文档级死数据。
2. **crontab 活跃行引用 52 个文件零死引用**（cron_reference_check.py 每6h自愈在位）→ 大孤儿=auto_executor.py（2329行 0 cron 引用，但 A/B 分离封印要求保留）。
3. 🚨 **三条并行分析线直调 brahma_core.analyze()**：brahma_cpu.py:113 / brahma_state_refresh.py:117 / battlefield_auto_analysis→brahma_manual_analysis —— CLAUDE.md「full_report 唯一入口」只是手动分支声明，实际存在 3 条旁路。这不是违规模块，是设计：cron 自动化线走轻量直调，手动线走全量报告。
4. **数据流闭环完整**：queue 写入方 7 个 → paper_executor → paper_positions → paper_tp_monitor(5min) → paper_ledger（全原子写）；signal_settler 是独立 WR 结算线，不回写 ledger。
5. **反向依赖实为 3 处**（不止我先前抓的 1 处）：core_replay / brahma_health / mcp_server import scripts 层，可下沉归零；正:反 = 62:3。

## 1. 入口清单（修正版）
- 真实入口拓扑 = **一主三旁路**：
  - 主线（手动/全量）：brahma_full_report.run_full_analysis → 1hao_analysis + analysis_runner → brahma_core.analyze
  - 旁路1：scripts/brahma_cpu.py:113 直调 analyze（cron 自动化）
  - 旁路2：scripts/brahma_state_refresh.py:117 直调 analyze（状态刷新）
  - 旁路3：battlefield_auto_analysis → brahma_manual_analysis（战场线）
- crontab 高频：square_hot_poster×5 / wr_feedback_engine×2 / square_auto_post×2 / paper_tp_monitor×2 / paper_executor×2
- 死引用：无（cron_reference_check 自愈）

## 2. 双引擎裁决（定案）
- SYSTEM_VERSION.json 全部 5 个声明入口文件不存在 → 该文件定位是历史档案非 SSOT。修正方案：Phase 1 更新为现状真值（active_entry=brahma_full_report.run_full_analysis + scripts/paper_executor.py），幽灵字段清除。
- 真执行器 = scripts/paper_executor.py（A/B 分离：auto_executor.py = dry 保险层保留）

## 3. 数据流一跳清单（维持 v1 结论，闭环完整）
cron → 分析线（直调或 full_report）→ 94维评分 → SQE → position_sizer → signal_queue_writer → paper_executor → paper_positions → tp_monitor → paper_ledger（原子写）

## 4. 反向依赖（3处，低优先级）
| 位置 | 内容 | 处理建议 |
|------|------|---------|
| brahma_full_report.py:470 | import brahma_1hao_analysis | 保留（低风险，Phase 3 改名时顺手处理） |
| brahma_brain/core_replay / brahma_health / mcp_server | import scripts | Phase 3 可下沉，非紧急 |

## 5. 架构总结（定稿）
梵天架构核心资产：**data_cache TTL 缓存层**（15模块接入）+ **SSOT 文化在位**（regime_config/math_utils/paper_ledger）+ **cron_reference_check 自愈守门** + 冒烟测试门。
最大负债：巨型函数族（2608/1091/986行）+ scripts 熵（143 文件 45,637 行，7+ 实锤死文件）+ 测试缺口（smc/trader/sizer/SQE 零快照）+ 文档漂移（SYSTEM_VERSION.json 5 个幽灵入口）。
架构定性：**分析主链健康，应用层（scripts）熵高**。重构优先级：先地基（测试快照）→ 再清理（死代码/裸except/文档）→ 后收敛（SSOT/API）→ 最后拆分（巨型函数）。
三道验收门（git 封印 / 冒烟 13/13 / wiring 0孤岛）不可绕过，保证"随时可恢复"承诺。
