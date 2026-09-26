# 体制链路线A执行报告 — 苏摩111裁决封印
**日期:** 2026-09-26 UTC · **审计:** regime_chain_audit FINAL · **执行:** AI设计院

## 裁决内容
苏摩111选定路线A：删死代码统一override，宪法表改为「方向建议」语义。

## 变更清单（6文件）

| 文件 | 变更 |
|------|------|
| `brahma_brain/regime_config.py` | 删3套死矩阵(DEFAULT/BTC/ETH)+ALTCOIN矩阵(~130行死代码)；新增 `REGIME_DIRECTION_ADVICE` 14体制方向建议表（只降权不放大）；`get_regime_mult_info()` 返回(mult, source)；新鲜度门 `_updated_date==今日UTC` |
| `brahma_brain/brahma_core.py` | 拆除override后门直读(L338-352旧行)，统一走 `get_regime_mult_info()`；breakdown记 `_wr_override=<source>`；补回 `_sym_upper`/`_is_long_signal` 赋值防回归 |
| `scripts/trade_gateway.py` | 删矛盾内联表 `_REGIME_MULT`（BULL_TREND LONG=1.5 vs 宪法1.10等），改走 regime_config SSOT；顺手根因修复 `evaluate` NameError（pre_trade_engine.py 幻影模块，evaluate=None 可选注入） |
| `scripts/regime_realtime_watcher.py` | regime_state.json 回写改原子写(tmp+os.replace)，封9.13式截断竞态 |
| `scripts/wr_feedback_engine.py` | save_override 改原子写；BASELINE_MULT 从手写表改为 import regime_config 建议（根治双源）；MAX_DEVIATION=0.40 保留 |
| `scripts/drift_checker.py` | D5升级：守卫1 SSOT定义+调用；守卫2 brahma_core禁止override后门；守卫3 trade_gateway禁止 `_REGIME_MULT =` 内联表复活 |

## 新语义（宪法级）
1. **override新鲜（今日UTC）** → WR实盘值生效（9.25三方修复后的现行宪法）
2. **override过期/缺失** → 方向建议表：死穴侧降权0.35~0.88，其余1.0中性
3. **未知体制** → 0.85保守
4. **放大(>1.0)只来自WR反哺实盘数据**，静态建议表永不放大——这是9.20 P0「体制不封锁」与9.25 WR反馈闭环的逻辑交集

## 行为变化声明
- 宪法表「×1.60放大」侧（BEAR_TREND SHORT等）在兜底时转为1.0中性；降权侧(0.35等)保留生效
- 放大不再来自矩阵，只来自苏摩111认可的WR反馈实盘链路
- 当前生效值: CHOP_MID LONG=0.5(advice) / BULL_TREND SHORT=0.5(advice)；override过期时 BULL_TREND LONG=1.0(中性)

## 验证（冒烟全绿）
- py_compile ×6 ✅
- SSOT行为矩阵7用例 ✅（含新鲜override命中1.23、过期作废、未知体制0.85、BREAKOUT中性）
- trade_gateway multiplier=SSOT ✅（mock链路）
- brahma_core.confluence_score 真实链路 CHOP_MID LONG→0.5 [advice_fallback] ✅
- wr引擎 BASELINE=建议表对齐 ✅
- **官方冒烟 brahma_smoke_test: 12/12 全绿**（含T10 analyze真实链路）
- **wiring_check: 9通过/0警告/0失败**
- **drift_checker: 7检查0漂移，D5新守卫PASS（后门=0 内联表=0）**

## 遗留观察（非本次范围）
- drift D2: 双NAV并存（start_nav=100 vs 233503.73）— P2账本重建事项
- WR反哺为全局矩阵，方仓标的专属化是后续P2

## 封印状态
代码完成 ✅ 调用验证 ✅ full_report可见 ✅ 冒烟全绿 ✅ → **封印生效**
