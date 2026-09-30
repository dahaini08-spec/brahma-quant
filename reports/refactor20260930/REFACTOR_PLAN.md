# 梵天系统重构总体方案 v1.0
> 2026-09-30 · 三路审计汇总（A架构 / B重复死代码 / C性能可维护性）
> 系统停机窗口（9.27 苏摩111），无实时交易风险；重构铁律=功能保持不变

## 一、三路审计核心结论

### A 架构（A_architecture.md）
- SYSTEM_VERSION.json 声明的 main.py/executor.py **不存在** → 双引擎疑云解除，真入口 = brahma_full_report.run_full_analysis()
- 数据流：cron → manual/auto_1hao → full_report → 1hao_analysis + analysis_runner → brahma_core.analyze(94维) → SQE → position_sizer → signal_queue → paper_executor → tp_monitor/settler → paper_ledger
- 反向依赖实锤 1 处：brahma_full_report.py:470 import scripts 层的 brahma_1hao_analysis
- 核心资产（不可破坏）：data_cache TTL 层、三大 SSOT（regime_config/math_utils/paper_ledger）、wiring_check 守门、冒烟测试门

### B 重复/死代码（B_duplication.md）
- 重复数学函数：4 文件 5 处（market_state/multi_tf_context_builder/miner_pressure/regime_realtime_watcher/bull_bear_engine），math_utils 已被 15 文件引用，行为等价可替换
- 乘数散落：auto_executor.py:625、ic_feedback_engine.py:116-117、brahma_decision_lifecycle.py:387（与 regime_config SSOT 冲突）
- 死代码实锤 7 个 .py：auto_execute_gate / signal_watcher / brahma_lifecycle / brahma_decision_lifecycle / dharma_v6_phase1 / dharma_ultimate_validator / dharma_training_engine（cron=0 + shell=0 + pyref=0 三重验证）
- 裸 except 66 处（关键路径 1 处：auto_executor.py:2103）
- push 封装散落 12 文件（push_hub SSOT 已声明）

### C 性能/可维护性（C_metrics.md）
- 巨型函数 top：analyze 2608行 / decide 1091行 / run_analysis 986行 / execute_signal 932行
- run_analysis 三胞胎（manual/1hao/runner 同名不同签名）= API 混淆源
- 缓存架构健全（data_cache TTL + 15 模块接入）；瓶颈=smc_engine.py:2129 等旁路缓存直连 HTTP
- **测试缺口**：smc_engine/trader_brain/fangcang_engine/signal_quality_engine/paper_executor/paper_settler 六模块零快照测试
- 分支密度最高：trader_brain（if=279）、smc_engine（if=308 loop=90）

## 二、重构策略（五阶段，风险递增）

**Phase 0 — 测试地基（先于一切）**
- 为 6 个零测试核心模块建 golden-case 快照：跑一次 run_full_analysis，把 r 对象关键字段（score/direction/regime/position_pct/SL/TP）固化为 fixtures
- 后续每一阶段完成后跑：冒烟 13/13 + fixtures 全等 + wiring_check 0孤岛
- 交付：tests/golden_snapshots/ + test_equiv.py

**Phase 1 — 零行为变更清理（P1，收益最高风险最低）**
1. 66 处裸 except → `except Exception`（机械替换，语义从"吞一切"收窄到"吞异常类"，不改变 except-pass 行为）
2. auto_executor.py:2103 关键路径裸 except 单独修复+注释
3. 死代码归档 3 文件（dharma_v6_phase1 / dharma_ultimate_validator / dharma_training_engine）→ archive/deprecated_20260930/，git 历史可回溯
4. SYSTEM_VERSION.json 修正：5 个幽灵入口清除（main.py/executor.py/position_monitor.py/market_regime.py/multi_agent_council.py 均不存在），active_entry → brahma_full_report.run_full_analysis
5. brahma_smoke_test.py 撤案保留（A-v2 实锤被 8 文件引用）

**Phase 2 — SSOT 收敛（P2，等价性测试后修订）**
0. ~~重复 RSI/EMA 委托 math_utils~~ → **已取消**：等价性实测证明 SMA种子 vs 首值种子、Cutler vs Wilder 数值不等（114.64 vs 114.73 / 83.3 vs 80.11），机械替换=改变信号数值，违反"功能不变"铁律。改为注释声明差异（唯一零风险方案），统一算法需苏摩111 拍板（行为变更）
1. auto_executor:625 + ic_feedback_engine:116 乘数表 → import regime_config（先 diff 确认数值一致才动）
2. brahma_360.py:412-413 正则门更新（SSOT 检查指向 regime_config）
3. push 封装 12 文件 → push_hub 单入口（分批，先旁路后关键）
4. _safe_float 双实现合并 math_utils（先验证等价）

**Phase 3 — API 收敛（P2.5）**
1. run_analysis 三胞胎：manual 保留（用户入口）、runner 保留（机器 SSOT）、1hao.run_analysis 改名 run_report（full_report 同步改引用），消除同名混淆
2. brahma_full_report.py:470 反向依赖处理：1hao 调用改为 entrypoint 注册或把 full_report 下沉到 scripts（二选一，倾向后者——报告层本就在应用层）

**Phase 4 — 巨型函数外挂拆分（P3，最后做）**
1. brahma_core.analyze (2608行)：按 block_a/b/c 先例继续外挂——新建 brahma_core_block_d/e…，analyze 只保留编排（纯移动代码，不改逻辑，每步全量等价测试）
2. trader_brain.decide (1091行) / auto_executor.execute_signal (932行)：同法分步
3. smc_engine/liq_density_engine 直连 HTTP → 改走 data_cache（复用 TTL 通道）

## 三、验收门（每阶段必须全绿）
- [ ] 冒烟测试 brahma_smoke_test_v2.py 13/13
- [ ] golden fixtures 全等（Phase 0 后可用）
- [ ] wiring_check 0 孤岛
- [ ] git commit 含「接入位置」
- [ ] 苏摩111 批准后进入下一阶段

## 四、明确不做（YAGNI）
- 不重写 brahma_core 内部逻辑（只外挂拆分）
- 不动 regime_config 乘数数值（只收敛位置）
- 不动 paper_ledger 账本逻辑（9.30 刚修正 E1 口径）
- 不改 cron 语义（只归档死脚本后更新 crontab 注释）
- 不升级 Python/依赖版本（环境冻结）

## Phase 3 封印记录（2026-09-30 苏摩111 · commit见git log）
1. `brahma_1hao_analysis.run_analysis` → 改名 `run_report`（职责正名：报告生成器），`__main__`同步迁移
2. 兼容别名 `run_analysis = run_report` 保留 → shell/外部调用零破坏
3. `brahma_full_report.py:470` 改引 `run_report`（引用唯一化+指向明确）
4. **反向依赖物理移动撤案**：full_report 下沉到 scripts/ 会破坏 4 个 brahma_brain.brahma_full_report import链（brahma_gate/ai4trade_publisher/brahma_health/brahma_mcp_server），且文件内 7 处 `__file__` 相对路径语义需全量重验——风险>收益（消除的是「1hao在scripts但full_report在brain」的风格不一致，非运行时缺陷）。反向依赖保留但已收敛到唯一调用点，后续如需移动需苏摩111专项批准
5. run_analysis 三胞胎终态：manual(10步VIP用户入口) / runner(机器SSOT dict) / 1hao=run_report(94维报告) —— 三名三义，同名混淆消除
验证：冒烟17/17绿(T10=142.4s) + wiring 9/9绿0孤岛 + 4消费方import健康
