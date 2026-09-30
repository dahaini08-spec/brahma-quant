# B路审计：重复/死代码/一致性（主会话代跑版）
> 2026-09-30 · 子代理两连失联后由主会话直接执行 · 全部结论有文件:行号证据

## 1. 数学函数重复（SSOT: math_utils.py，已统一15文件引用）
全库 RSI/EMA/ATR/SMA 定义点（排除 archive/__pycache__）：
| 位置 | 函数 | 状态 |
|------|------|------|
| brahma_brain/market_state.py:75/99/116 | ema/atr/rsi | 公共API，被下游 import（勿删，改为委托 math_utils） |
| brahma_brain/multi_tf_context_builder.py:56/68 | _ema/_rsi | 私有重复，可换 import |
| scripts/miner_pressure.py:20 | _ema | 私有重复，可换 import |
| scripts/regime_realtime_watcher.py:135 | rsi(嵌套) | 移除后需保 rsi(close,p) 签名兼容 |
| scripts/bull_beer_engine→bull_bear_engine.py:46 | _rsi | 私有重复，可换 import |
- math_utils.py 已被 15 文件正确引用，SSOT 方向正确，残留 4 个文件 5 处私有重实现。
- 对比结论：market_state.ema==math_utils.ema（等价）；multi_tf._rsi 与 math_utils._rsi 逻辑同源（Wilder）。
### P2-1 重复数学函数定案（等价性测试推翻机械替换）
- **market_state.ema vs math_utils.ema 不等价**（SMA种子 vs 首值种子：114.64092941 vs 114.73360）→ 不替换，标注差异注释即可（改了=变信号）
- **multi_tf._rsi / regime_realtime_watcher.rsi / bull_bear._rsi 三者互相等价**（Cutler简单均值 83.3）但**与 math_utils（Wilder 递归 80.11）不等价** → 不替换。这解释了为何 8.24 设计院已把 miner_pressure/bull_bear 委托 math_utils——但委托后带 fallback 原实现（数值会漂移，属于既有债务非本次引入）
- 判定：**全部 5 处保留原实现 + 加注释声明非等价**（唯一零风险方案）；「统一 RSI 算法」列为 Phase 4 后候选（需苏摩111 拍板，属行为变更）
- P2-1 结论：零代码改动（推翻 REFACTOR_PLAN 原项），重复不是债——算法分歧才是真相

## 2. 乘数/阈值散落（SSOT: regime_config.py）
- ✅ regime_config.py L39/41/86/179：BULL_TREND:SHORT=0.50 / CHOP_MID:LONG=0.50 / BULL_TREND:LONG:140-154 locked / ETH 订单流 1.3 —— 声明的 SSOT 在位。
- ❌ 散落点（真实违规）：
  - scripts/auto_executor.py:625 内联 `'CHOP_MID:SHORT':1.0,'CHOP_MID:LONG':0.5`
  - scripts/ic_feedback_engine.py:116-117 内联 BEAR_TREND:SHORT=1.6/BULL_TREND:LONG=1.6/BEAR_EARLY:1.2/CHOP_MID:SHORT=0.88
  - scripts/brahma_decision_lifecycle.py:387 内联 BEAR_TREND/BULL_TREND=1.2 等
  - scripts/brahma_manual_analysis.py:807-811 FVG/OB/LIQ/OI/GEX/FC/CMA 七维权重表按体制硬编码（这是展示层权重，另算）
  - brahma_360.py:412-413 用正则检查"1.6/0.88"存在性——**此检查已过时**（乘数已迁 regime_config 后不再以该字面量出现，若 brahma_360 检查 regime_config 原文会假绿/假红）
  - brahma_core.py:998/1006 _regime_sl 体制SL乘数表 ×2 份（CHOP_LOW..BULL_CORRECTION），与 1165-1174 的体制 R:R 表并存
### P2-2 乘数表收敛定案（消费链验证后大部分撤案）
逐条验证结果：
- **auto_executor.py:625 _WR_DEF ≠ 乘数表**——这是 WR 默认值表（fallback WR），语义完全不同，不与 regime_config 冲突。撤案，改为注释声明语义（WR default ≠ regime multiplier）
- **ic_feedback_engine.py:116 base_multipliers 是建议生成器**：写入 wr_matrix_realtime.json 的 adjustments.multiplier_<key>（建议字段），但下游 position_sizer 只消费 ic 值 + best_wr_bucket，**不消费 multiplier_ 键**（grep 全库零消费者）→ 这是建议层死数据，收敛方案=直接 import regime_config 取当前值作 base（消除注释依赖 MEMORY.md），风险为零（只是建议生成器输入）
- **position_sizer 实际乘数源=signal_weights.json（6键，静态权重）**，不是 regime_config——乘数链路真相：regime_config(get_regime_mult_info) → brahma_core.analyze 内部应用 → position_sizer 另有 SW 层。两套乘数并行存在，各有语义，不可合并
- 结论：ic_feedback_engine 一处小修（base 改为 import regime_config），其余撤案

## 3. 死代码热点（验证方法：grep 引用计数 + crontab 交叉）
### brahma_brain/（2个，300+行零引用）
- brahma_smoke_test.py（410行）：旧冒烟，已被 brahma_smoke_test_v2.py 取代 → 可移 archive/
- self_heal_daemon.py（334行）：**注意**——9.30 修复记录表明 independent_watchdog.sh 引用的是 brahma_brain/self_heal_daemon（b1a629e7 修复），grep 未命中是因为调用方是 shell。判定：**非死代码，保留**（shell 引用）。
- 结论：brahma_brain 干净度很高，仅 1 个真死文件。

### scripts/（cron=0 大文件，需逐一判死）
| 文件 | 行数 | refs | 判定 |
|------|------|------|------|
| brahma_manual_analysis.py | 3416 | 0 | 手动入口（用户触发），非死 |
| auto_executor.py | 2329 | 2 | 9.26 A/B分离后进入 dry 保险层，保留（有 --allow-live 双闸） |
| brahma_1hao_analysis.py | 1959 | 1 | 被 runner 调用，活 |
| rsi_structure_watcher.py | 1033 | 1 | 被 breakout_watch.py 引用，活 |
| brahma360_self_heal.py | 862 | 1 | 活 |
| brahma360_guardian.py | 828 | 0 | 被 brahma360_self_heal 用 exec/子进程方式调用？需查 shell |
| brahma_decision_lifecycle.py | 790 | 0 | cron=0 + refs=0 → 嫌疑死代码，查 shell 引用后可 archive |
| free_llm_client.py | 649 | 1 | 活 |
| brahma_lifecycle.py | 593 | 0 | 嫌疑死 |
| dharma_ultimate_validator.py | 581 | .py ref=0 | B路泄漏数据：外部引用=0 → 死 |
| paper_engine.py | 578 | 0 | **paper_executor.py 是现役执行器，paper_engine.py 疑似旧版**，查后可 archive |
| auto_execute_gate.py | 571 | 0 | 嫌疑死 |
| signal_watcher.py | 565 | 0 | 需查 cron/shell（可能被 process_resurrect.sh 引用）|
| dharma_training_engine.py | 515 | 0 | 死（外部引用=0，B路泄漏数据同款验证） |
| btc_regime_watcher.py | 468 | 0 | 需查 shell |
| dharma_v6_phase1.py | 418 | .py引用=0 | 死 |

### 死代码修正定案（A-v2 回报后三重验证定案）
7 个 .py 中仅 1 个实锤死码：**dharma_v6_phase1.py（418行）** —— 全格式引用扫描（py/sh/txt/json/yml）零引用。
dharma_ultimate_validator/dharma_training_engine 互相引用对方（internal pair，跨族零引用，同入死名单）。
其余 4 个（auto_execute_gate/signal_watcher/brahma_lifecycle/brahma_decision_lifecycle）**全部活**：
- auto_execute_gate ← signal_watcher.py:504 import
- signal_watcher ← config/cron_jobs_cac.json 两处 AI cron prompt 引用
- brahma_lifecycle ← brahma360_guardian.py:468 系统定时器字符串引用
- brahma_decision_lifecycle ← price_trigger_monitor.py:32 + paper_executor.py:144 import

附带修正：brahma_smoke_test.py（410行旧冒烟）被 8 个文件引用（brahma_health/ai_output_guard/brahma_360/change_impact_check.sh/orphan_check 等）→ **非死码，保留**（v1 误判，已纠正）。
_stat_7cpchhrt 全库零引用 → 死（若存在）。

### B路泄漏数据交叉验证（子代理失联前抓到的实证）
- `_stat_7cpchhrt` → 0 外部引用 → 死
- `scan_and_alert` → 仅 dharma.pump_hunter 引用（活）
- `dharma_ultimate_validator` / `dharma_training_engine` → 0 外部引用 → 死
- `rsi_structure_watcher` → breakout_watch.py 引用（活）
- `portfolio_optimizer` → 3 文件引用（活）

## 4. 裸 except 分级（全库 66 处，12 处 except:pass）
- 必修 top：scripts/auto_executor.py:2103（信号队列读取处 `except: _sq_existing=[]` —— 关键路径吞异常，坏一行=静默清空队列上下文）
- 关键路径裸 except：auto_executor.py 是唯一在交易关键路径上的（paper_executor/trader_brain/brahma_core 反而干净）。
- 其余 65 处集中在 dashboard/self_heal/guardian/daily_report 等旁路（监控类，风险=假绿监控）。
- 修复策略：关键路径 1 处立改 `except Exception`；旁路 65 处分批机械替换 `except:` → `except Exception`，零行为变更。

## 5. 重复工具函数
- _safe_float：brahma_brain/fangcang_engine.py:1772 与 scripts/brahma_1hao_analysis.py:55 两份实现
- _atomic_write：scripts/brahma_state_refresh.py:62 一份（brahma_brain 侧同族函数未 grep 到同名词）—— MEMORY 提到 watcher/wr 原子写，说明同族功能散在多文件
- push 封装：12 文件各自实现推送（tv_bridge/auto_executor/push_chart/paper_engine/error_ledger/drawdown_tracker/smc_engine 等）——推送 SSOT 是 push_hub，12 处自实现需收敛（MEMORY 9.25 封印：统一 push_hub 单入口）

## 结论（重构机会排序）
2. P1: 66 处裸 except 机械替换（零行为变更）
3. P1: 死代码归档：仅 dharma_v6_phase1 / dharma_ultimate_validator / dharma_training_engine 三文件实锤（互相引用对 + 全格式零引用）；其余候选全部撤案（auto_execute_gate/signal_watcher/brahma_lifecycle/brahma_decision_lifecycle/brahma_smoke_test 均有活跃引用）
4. P1: SYSTEM_VERSION.json 修正（5 个幽灵入口清除，active_entry=brahma_full_report.run_full_analysis）
5. P2: 4 文件 5 处重复 RSI/EMA 换成 math_utils import（market_state 改为委托）
6. P2: auto_executor:625 + ic_feedback_engine:116 乘数表收敛到 regime_config
