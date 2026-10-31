<div align="center">

# 梵天量化 · Brahma-Quant 3.0

### 加密永续合约信号研究框架
### Crypto Perpetual Futures Signal Research Framework

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-research--only-orange)](docs/)

[架构](#架构--architecture) · [核心模块](#核心模块--core-modules) · [真实指标](#真实性能指标--honest-performance) · [快速启动](#快速启动--quickstart)

</div>

---

> **⚠️ 重要声明 / Important Disclaimer**
>
> 梵天3.0 是一个**信号研究框架**，不是自动交易系统。
> 执行层（OMS / 真实下单）未包含在本公开仓库中。
>
> Brahma-Quant 3.0 is a **signal research framework** for crypto perpetual futures.
> The live execution OMS layer is not included in this public repository.
>
> **请勿将本系统用于未经独立验证的自动化实盘交易。**
> Do not use for automated live trading without independent validation.

---

## 系统简介 · Overview

梵天3.0 是历时约12个月迭代构建的量化信号研究系统，核心能力如下：

- **94维评分引擎**：12步骤结构化分析，覆盖 SMC 结构 / 订单流 / 宏观 / 波动率
- **三周期裁决**：15M + 1H + 4H 多时框共振，降低虚假信号
- **达摩院门控**：6节点预测验证，信号需通过 ≥3 节点才输出
- **方仓向量库**：6.8年历史K线相似案例匹配（DTW + 25维特征向量）
- **LLM议会**：可选6智能体辩论层（可用 `BRAHMA_SKIP_COUNCIL=1` 跳过）
- **7×24 自动巡检**：13个 cron 任务持续运行

---

## 架构 · Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        梵天3.0 信号流水线                            │
│                   Brahma 3.0 Signal Pipeline                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  【数据层 / Data Layer】                                              │
│  Binance fapi (K线/OI/资金费) + CVD WebSocket + LiqMap + OKX流量    │
│                          ↓                                          │
│  【分析层 / Analysis Layer — 94维12步骤】                             │
│  Step0  数据拉取          Step1  FVG公平价值缺口                      │
│  Step1b 方仓锚点          Step2  OB订单块有效性                       │
│  Step3  清算地图          Step4  共振点识别                           │
│  Step5  OI/LSR/Z-Score   Step6  聪明钱模式                           │
│  Step7  HAR-RV波动率      Step8  宏观日历过滤                         │
│  Step9  风险评估          Step10 VIP策略生成                          │
│                          ↓                                          │
│  【门控层 / Gate Layer】                                              │
│  达摩院6节点 (≥3 pass → 继续)   +   体制分类器 (10体制)              │
│                          ↓                                          │
│  【信号层 / Signal Layer】                                            │
│  VIP策略 (做多/做空/等待)   先行信号 (15M触发)   方仓埋伏锚点          │
│                          ↓                                          │
│  【推送层 / Push Layer】                                              │
│  Jarvis通知 (苏摩实时)   Binance Square (3账号自动发布)               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 体制分类器（10体制）/ 10-Regime State Machine

| 体制 | 方向偏置 | SHORT倍率 | LONG倍率 |
|------|---------|:---:|:---:|
| `BULL_TREND` | 做多主导 | 0.15× | 1.6× |
| `BULL_EARLY` | 做多主导 | 0.35× | 1.2× |
| `BULL_CORRECTION` | 双向 | 1.1× | 0.50× |
| `BEAR_TREND` | 做空主导 | 1.6× | 0.10× |
| `BEAR_EARLY` | 做空主导 | 1.2× | 0.35× |
| `BEAR_RECOVERY` | 仅多 | 0.30× | 1.2× |
| `BEAR_CRASH` | 做空极端 | 2.0× | 0.05× |
| `CHOP_LOW` | 无信号 | 0.80× | 0.45× |
| `CHOP_MID` | 无信号 | 0.88× | 0.50× |
| `CHOP_HIGH` | 无信号 | 0.70× | 0.40× |

---

## 核心模块 · Core Modules

### brahma_brain/ — 核心引擎（103个模块）

| 模块 | 功能 | 规模 |
|------|------|------|
| `brahma_core.py` + blocks A–F | 94维评分引擎（共享 `_result` 状态） | ~4400行 |
| `brahma_core_analyze_steps.py` | 12步骤协调器 | 319行 |
| `brahma_94d_engine` (scripts) | 12步骤强制分析，透明推理链 | 599行 |
| `smc_engine.py` | SMC结构分析（BOS/CHoCH/OB/FVG/流动性） | 3171行 |
| `fangcang_engine.py` | 方仓历史案例匹配（DTW + 25维特征向量） | 3031行 |
| `dharma_nodes.py` | 达摩院6节点门控验证 | 167行 |
| `trader_brain.py` | 6层交易决策引擎 | 1803行 |
| `free_llm_client.py` | LLM路由（4级降级链：Gemini→Groq→OR→fallback） | 425行 |
| `liqmap_collector.py` | 清算地图实时采集 | — |
| `regime_hmm_v2.py` + `regime_scorer.py` | HMM + Hurst指数体制验证 | — |
| `dag_executor.py` | DAG稀疏激活（Phase 1） | — |
| `ensemble_engine.py` | 集成引擎 | — |
| `microstructure_engine.py` | 微结构分析 | — |
| `onchain_engine.py` | 链上数据引擎 | — |
| `brahma_mcp_server.py` | MCP服务器接口 | — |
| `brahma_health.py` | 模块健康检查 | — |
| `anti_manipulation_engine.py` | 反操控检测 | — |
| `circuit_breaker.py` | 软件熔断器（非交易杀链） | — |

### scripts/ — 自动化脚本（151个模块）

| 类别 | 代表模块 | 功能 |
|------|---------|------|
| **主控** | `brahma_robot.py` | 机器人主控循环 |
| **分析** | `brahma_manual_analysis.py` | 手动分析入口（5067行） |
| **副驾驶** | `trading_copilot.py` | 交易决策辅助 |
| **VIP监控** | `m7_vip_monitor.py` | VIP策略追踪监控 |
| **信号治理** | `signal_govern.py` | 信号过滤与质量控制 |
| **先行信号** | `leading_signal_executor.py` | 15M触发先行信号 |
| **状态刷新** | `brahma_state_refresh.py` | 系统状态刷新 |
| **数据采集** | `cvd_ws_collector.py` | CVD实时WebSocket |
| **方仓摄取** | `fangcang_auto_ingest.py` | 历史案例自动摄取 |
| **Square发布** | `square/` + `square_*.py` | Binance Square多账号自动发布 |
| **健康巡检** | `cron_health_board.py` | cron任务健康面板 |
| **波动率** | `har_rv_refresh.py` | HAR-RV实时刷新 |
| **战场情报** | `battlefield_auto_analysis.py` | 多标的自动扫描 |
| **风险** | `cron_drawdown_touch.py` | 回撤触发检测 |

---

## 真实性能指标 · Honest Performance

> 以下数据均来自仓库内文件，**不使用无法复现的数字**。

### 实盘记录（B线）

| 指标 | 数值 | 说明 |
|------|------|------|
| B线启动时间 | 2026-09-26 | |
| 真实成交笔数 | **0笔** | 执行层未投产 |
| 当前信号质量DSR | **1.21**（n=14） | 样本量过小，仅供参考 |

> ⚠️ DSR=1.21 基于 n=14 笔，不具备统计显著性。之前文档中的 DSR=22.64 **无法从代码复现**，已永久作废。

### 回测数据（来自 `data/wr_matrix.json`，total_settled=54）

当前已结算信号样本数量不足以给出体制级别统计，仅展示历史回测参考：

| 资产 | 回测胜率 | 样本量 | 备注 |
|------|---------|:---:|------|
| BTC/USDT | 56.7% | n=3655 | 历史回测，未扣除滑点 |
| ETH/USDT | 55.2% | n=3572 | 历史回测，未扣除滑点 |

> ⚠️ 回测数据不等于实盘表现。实盘B线尚无成交记录，上述数字仅供技术验证参考。

### 已知局限性

1. **主力桶胜率偏低**：前一版 wr_matrix 显示 BULL_TREND LONG（n=96）胜率仅 34.4%，低于随机
2. **无 CPCV/DSR 代码实现**：Walk-forward 为报告生成器，未做参数冻结
3. **方仓引擎依赖私有数据**：6.8年历史 K线不在公开仓库中
4. **执行层非生产就绪**：`auto_executor.py` 为残留代码，风险引擎 fail-open
5. **94维共享可变状态**：`_result` dict 跨维度共享，单元测试困难

---

## 快速启动 · Quickstart

```bash
git clone https://github.com/dahaini08-spec/brahma-quant.git
cd brahma-quant

# 最小安装（仅信号研究）
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 离线验证安装
python examples/quick_start.py --validate

# 单标的分析（需要 .env 中的 BINANCE_API_KEY）
BRAHMA_SKIP_COUNCIL=1 python examples/quick_start.py --symbol BTCUSDT

# 94维12步骤强制分析
python scripts/brahma_94d_engine.py --symbol BTCUSDT

# 系统健康检查
python brahma_brain/brahma_health.py

# 运行测试
pytest tests/ -q --ignore=tests/test_e2e_signal_flow.py
```

---

## 系统要求 · Requirements

| 组件 | 要求 |
|------|------|
| Python | 3.11+ |
| 内存 | ≥ 4GB（方仓索引约 2GB） |
| 存储 | ≥ 20GB（历史数据缓存） |
| 网络 | 需连接 Binance fapi / OKX |
| 可选 | Qdrant（方仓向量检索）、Redis（状态缓存） |

---

## 项目结构 · Project Structure

```
brahma_brain/          核心评分引擎（103个模块）
  brahma_core.py         94维评分主引擎（含 block_a ~ block_f）
  smc_engine.py          SMC结构分析（3171行）
  fangcang_engine.py     方仓历史案例引擎（3031行）
  dharma_nodes.py        达摩院6节点门控
  trader_brain.py        6层交易决策
  free_llm_client.py     LLM路由4级降级链
  dag_executor.py        DAG稀疏激活
  brahma_mcp_server.py   MCP服务器

scripts/               生产自动化（151个模块）
  analysis/              12步骤分析子模块（step0 ~ step10）
  square/                Binance Square 发布引擎
  brahma_robot.py        机器人主控
  brahma_manual_analysis.py  手动分析入口
  m7_vip_monitor.py      VIP策略监控
  signal_govern.py       信号质量治理
  leading_signal_executor.py 先行信号执行

data/                  运行时数据（不入版本库）
  wr_matrix.json         胜率矩阵（性能指标唯一数据源）
  brahma_state.json      系统状态
  auto_analysis_latest.json  最新分析结果

docs/                  文档
  AUDIT_CLAIMS_VS_EVIDENCE.md  指标审计对照表（2026-09-13）
  BRAHMA_DIMENSION_MAP.md      94维状态地图
```

---

## Cron 任务（7×24 自动运行 · 13个任务）

| 任务名 | 频率 | 功能 |
|--------|------|------|
| rsi-structure-watcher | 每30分钟 | RSI/BB/OI/Volume 触发检测 |
| auto-analysis-stale | 每3小时 | 自动分析新鲜度检查 |
| blood-chip-morning | 每日 06:15 UTC | 筹码分布早报 |
| ...（共13个） | — | 覆盖信号/健康/Square发布 |

---

## 重要声明 · Important Notice

**本系统是研究工具，不是投资建议。**

- 梵天3.0 输出的所有信号仅供苏摩（研究者）参考
- 实盘交易决策由人类最终负责
- 过去的回测表现不代表未来实盘结果
- 执行层代码未经过充分的风险验证，不建议直接用于实盘

**This system is a research tool, not investment advice.**

---

*最后更新 / Last updated: 2026-10-31*
*封印版本 / Sealed version: Brahma 3.0 · 苏摩111*

*README 优先诚实，不做营销。数字无法从仓库复现的，不写入此文件。*
