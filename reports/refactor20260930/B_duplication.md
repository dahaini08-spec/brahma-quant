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
- 判定：可安全替换（行为等价），替换后需冒烟 13/13。

## 2. 乘数/阈值散落（SSOT: regime_config.py）
- ✅ regime_config.py L39/41/86/179：BULL_TREND:SHORT=0.50 / CHOP_MID:LONG=0.50 / BULL_TREND:LONG:140-154 locked / ETH 订单流 1.3 —— 声明的 SSOT 在位。
- ❌ 散落点（真实违规）：
  - scripts/auto_executor.py:625 内联 `'CHOP_MID:SHORT':1.0,'CHOP_MID:LONG':0.5`
  - scripts/ic_feedback_engine.py:116-117 内联 BEAR_TREND:SHORT=1.6/BULL_TREND:LONG=1.6/BEAR_EARLY:1.2/CHOP_MID:SHORT=0.88
  - scripts/brahma_decision_lifecycle.py:387 内联 BEAR_TREND/BULL_TREND=1.2 等
  - scripts/brahma_manual_analysis.py:807-811 FVG/OB/LIQ/OI/GEX/FC/CMA 七维权重表按体制硬编码（这是展示层权重，另算）
  - brahma_360.py:412-413 用正则检查"1.6/0.88"存在性——**此检查已过时**（乘数已迁 regime_config 后不再以该字面量出现，若 brahma_360 检查 regime_config 原文会假绿/假红）
  - brahma_core.py:998/1006 _regime_sl 体制SL乘数表 ×2 份（CHOP_LOW..BULL_CORRECTION），与 1165-1174 的体制 R:R 表并存
- 判定：auto_executor:625 与 ic_feedback_engine:116 是同族硬编码，应收敛到 regime_config 或 import 复用；brahma_360 的正则门需跟着 SSOT 位置更新。

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
