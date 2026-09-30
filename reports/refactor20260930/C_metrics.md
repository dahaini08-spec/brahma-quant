# C路审计：性能与可维护性度量（主会话代跑版）
> 2026-09-30 · AST 全量扫描 brahma_brain + scripts（排除 .venv/archive/__pycache__），零语法错误

## 1. 函数级复杂度 top 20
| 行数 | 位置 |
|------|------|
| 2608 | brahma_brain/brahma_core.py:analyze (L661) |
| 1091 | brahma_brain/trader_brain.py:decide (L223) |
| 986 | brahma_brain/brahma_analysis_runner.py:run_analysis (L231) |
| 932 | scripts/auto_executor.py:execute_signal (L958) |
| 870 | scripts/brahma_1hao_analysis.py:run_analysis (L407) |
| 811 | brahma_brain/brahma_core_block_a.py:calc_block_a (L26) |
| 718 | scripts/brahma_manual_analysis.py:run_analysis (L2545) |
| 686 | brahma_brain/brahma_full_report.py:run_full_analysis (L453) |
| 677 | brahma_brain/brahma_core_entry.py:calc_trade_params (L126) |
| 552 | brahma_brain/formatter.py:brahma_panorama_report (L604) |

brahma_core.analyze 单函数 2608 行——全库最大复杂度炸弹，但 CLAUDE.md 已声明"94维核心、唯一入口"，重构必须外挂式（拆分为 block 级纯函数而非重写）。

## 2. 分支密度（if+loop/百行，>500行文件 top10）
battlefield_intel 20.3 / brahma_core_block_a 20.1 / dharma_ultimate_validator 19.8 / trader_brain.decide 所在文件 15.7（if=279！）/ formatter 14.3 / smc_engine 12.7（if=308 loop=90）
→ trader_brain 与 smc_engine 是行为密度最高的两个模块，任何重构必须带等价性测试。

## 3. I/O 热点
- HTTP 调用最多：brahma_brain/__init__.py(14)（模块导入即 HTTP？需查——可能是注释或兼容层）、brahma_1hao_analysis(13)、narrative_engine(13)、anti_manipulation_engine(12)
- 关键链路上的串行 HTTP：smc_engine.py:2129 在函数体内直接 requests.get 取价；liq_density_engine.py:82/121/160 三处独立 GET
- 循环内 sleep：brahma_1hao_analysis.py:1353-1355 订单簿采样 sleep(0.35)（功能性，保留）；oi_advanced_scanner.py:169 重试循环（功能性，保留）
- data_cache.py TTL 机制健全（TTL<蜡烛周期1/4），15 个模块已接入 —— 缓存架构是对的

## 4. JSON 重复读取
- core 链路 json.load 次数：runner=2 / trader_brain=3 / paper_executor=5（paper_positions 等）——量级可接受，非瓶颈
- brahma_state.json/regime_state.json 直接引用少（走 regime_watcher 进程间文件），架构 OK

## 5. tests 覆盖映射
20 个测试文件（4489行），test_core_* 系列覆盖 brahma_core 单元（scorer/factors/output/data/extra），test_brahma_critical.py 存在。
零测试模块（核心链路上）：smc_engine、trader_brain、position_sizer、signal_quality_engine、paper_executor、paper_settler —— **重构前必须先补这 6 个的等价性快照测试**，否则"功能不变"无法证明。

## 6. scripts/ 目录熵
190 个脚本，前缀分布：brahma×18 / paper×9 / signal×7 / dharma×4 / square×3 / regime×3 / liq×3 / cron×3 / build×3...
同族可合并对（<300行）：见 B 路死代码清单交叉（auto_execute_gate/paper_engine/signal_watcher 与现役 executor 族高度重叠）。

## 7. 性能瓶颈假设（top 5）
| 假设 | 证据 | 影响 |
|------|------|------|
| ① smc_engine 函数体内裸 requests.get（非缓存通道） | smc_engine.py:2129, liq_density_engine:82/121/160 | 中——TTL缓存被旁路，重复拉价 |
| ② brahma_core.analyze 2608行单体 | AST 实测 | 中——维护风险>性能 |
| ③ 12 个文件自建推送封装 | B路清单 | 低——一致性风险 |
| ④ paper_executor 5次 json.load | C路计数 | 低 |
| ⑤ trader_brain.decide 1091行 if=279 | AST 实测 | 中——行为等价验证成本最高 |

### 补充：run_analysis 三胞胎（子代理泄漏数据已验证）
- scripts/brahma_manual_analysis.py:2545 run_analysis(sym, push_jarvis) → 返回 str（面向用户推送）
- scripts/brahma_1hao_analysis.py:407 run_analysis(symbol, direction, compact) → 返回 str（旧报告生成）
- brahma_brain/brahma_analysis_runner.py:231 run_analysis(symbol, deep, signal_dir) → 返回 dict（机器读取）
三个同名不同签名不同返回类型 = API 混淆源。C 报告判定：manual 是用户入口（保），runner 是机器 SSOT（保），1hao 需确认是否仍被引用。

## 8. 可维护性风险结论
1. **测试缺口是最大风险**：核心决策模块（smc/trader/sizer/SQE）零快照测试，重构"功能不变"承诺无法兑现 → 必须先建 golden-case 等价性测试
2. brahma_core.analyze 拆分策略：外挂 block 函数（已有 block_a/block_b/c 先例），不重写内部逻辑
3. tests 实际引用验证：smc/fangcang/trader_brain 在 tests 中无直接 import（子代理清单里的5个文件实际只测 position_sizer + signal_quality_engine）；**核心决策模块 smc_engine/trader_brain/fangcang_engine 零测试实锤**
4. scripts 递归全量 143 个文件 45,637 行（子代理计），含子目录
5. scripts 190 个脚本：先归档死代码，再按前缀合并同族
