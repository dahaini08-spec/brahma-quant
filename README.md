# 🏛️ Brahma-Quant — Crypto-Native Multi-Agent Quantitative Trading System

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-35%2F35%20passing-brightgreen.svg)](#testing)
[![CI](https://img.shields.io/badge/brahma--ci-96%2F100-brightgreen.svg)](#system-health)
[![Open Core](https://img.shields.io/badge/model-Open%20Core-purple.svg)](#pro-version)

**The only crypto quantitative system built on three pillars:**
**Multi-Agent Debate · Statistical Iron-Proof Validation · Crypto-Native Pump Hunter**

[Architecture](#architecture) · [Quick Start](#quick-start) · [Dharma Validation](#dharma-validation) · [Live Performance](#live-performance) · [Pro Version](#pro-version)

</div>

---

## 🎯 What Makes Brahma Different

| Feature | Brahma-Quant | Generic Multi-Agent | Traditional Quant |
|---------|-------------|--------------------|--------------------|
| 35-Dimensional Scoring | ✅ | ❌ | ⚠️ Partial |
| 5-Regime State Machine | ✅ | ❌ | ❌ |
| 6-Agent Council Debate | ✅ | ✅ | ❌ |
| Dharma Iron-Proof Validation | ✅ | ❌ | ⚠️ Basic |
| Pump Hunter (meme surge) | ✅ | ❌ | ❌ |
| Full-Chain Circuit Breaker | ✅ | ❌ | ⚠️ Partial |
| Monte Carlo 3000+ runs | ✅ | ❌ | ⚠️ Partial |
| Zero-Cost Regime Watcher | ✅ | ❌ | ❌ |

---

## 🏗️ Architecture (Brahma-360 v3.0)

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 0  DataShield        感知数据防护 + 数据契约验证            │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1  BrainGuard 🧠     35维评分 + 异常免疫 + 幻觉检测        │
│           brahma_core.py · timing_filter · position_sizer       │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2  Dharma-360 🔬     Monte Carlo + Walk-Forward + PBO    │
│           dharma_360_validator · realistic_cost_model           │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3  CouncilGuard 🏛️   6-Agent辩论 + 监督者编排 + 轨迹记录  │
│           llm_council_bridge · brahma_orchestrator              │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4  CircuitBreaker ⚡  全链路9层熔断 + 自动回滚             │
│           circuit_breaker.py (CLOSED/OPEN/HALF 三状态机)        │
├─────────────────────────────────────────────────────────────────┤
│  Layer 5  Observability 📊  AI行为看板 + Token追踪 + 信号质量    │
│           ai_behavior_dashboard · brahma_ci_v2 (96/100)         │
├─────────────────────────────────────────────────────────────────┤
│  Layer 6  AutoGuardian 🛡️   蓝绿部署 + 单元测试(35/35) + CI      │
│           blue_green_deploy · test_core_brahma_units            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

```bash
git clone https://github.com/dahaini08-spec/brahma-quant
cd brahma-quant
pip install -r requirements.txt

# 1. 系统健康检查
python3 brahma_brain/brahma_ci_v2.py

# 2. 运行 Dharma Monte Carlo 验证
python3 dharma/dharma_360_validator.py --sym BTCUSDT --dir SHORT --runs 3000

# 3. 查看 AI 行为看板
python3 scripts/ai_behavior_dashboard.py

# 4. 运行单元测试 (35/35)
python3 -m pytest tests/test_core_brahma_units.py -v

# 5. 蓝绿部署状态
python3 scripts/blue_green_deploy.py --status
```

---

## 🧠 Core Modules

### 35-Dimensional Scoring Engine (`brahma_brain/brahma_core.py`)
```
技术指标层 (RSI/BB/ATR/Volume)     → 多时间框架动量
SMC结构层 (OB/FVG/BOS/CHoCH)       → Smart Money Concepts
体制感知层 (5-regime multipliers)   → 顺势/逆势加权
外部信号层 (FR/OI/跨所套利)         → 市场情绪
Kronos时序层 (p_up概率)             → 深度学习时机预测
因果验证层 (CausalVerifier)         → 噪音过滤 -12分惩罚
```

### 5-Regime State Machine
```python
BEAR_TREND    → 空为主 (SHORT multiplier: 1.6x)   # 死穴: LONG WR=45%
BULL_TREND    → 多为主 (LONG multiplier: 1.6x)
CHOP_MID      → 不发策略 (score≥110 → WATCH 0.5%NAV)
BEAR_EARLY    → 空单初现
BEAR_RECOVERY → 仅多单，严禁空单
```

### 6-Agent Council (`brahma_brain/llm_council_bridge.py`)
```
Quant Engineer   → 技术分析
Researcher       → 统计验证
Trader           → 执行时机
Risk Director    → 风险评估
Macro Analyst    → 宏观背景
CEO/Soma         → 最终裁决 (苏摩111 最高批准权)
```

---

## 🔬 Dharma Validation (Iron-Proof Framework)

Brahma-Quant 的核心差异化：**统计铁证驱动，拒绝过拟合**

```bash
# 完整 Monte Carlo 验证
python3 dharma/dharma_360_validator.py --sym BTCUSDT --dir SHORT --runs 5000

# 输出示例:
# 全量 WR: 61.3% 🟢 良好(≥62%)
# 置信区间: [58.1%, 64.5%] (95%CI)
# OOS WR:  59.8% (样本外验证)
# PBO:     0.18 → VALID (不过拟合)
# DSR:     1.23 → VALID
# 🎯 综合判定: ✅ 铁证级 — 真实信号，可上线
```

**Dharma 验证矩阵:**

| 验证维度 | 方法 | 工具 |
|---------|------|------|
| 基础验证 | Bootstrap WR 置信区间 | `bootstrap_wr()` |
| 过拟合检测 | Probability of Backtest Overfitting | `calc_pbo()` |
| 统计显著性 | Deflated Sharpe Ratio | `deflated_sharpe()` |
| 体制分层 | 5-regime 独立统计 | `validate_by_regime()` |
| 真实成本 | 滑点+手续费+资金费率 | `realistic_cost_model` |
| 数据增强 | 合成体制样本扩充 | `regime_aware_augmentor` |

---

## 🎯 Pump Hunter (Crypto-Native)

针对 meme/妖币的专项检测，基于**2年全量历史数据**验证：

```
TIGHT 压缩 (<15%) 7日胜率: 97.5% (n=1600)
RSI<30 + TIGHT:         93%
连续缩量 13H+:           100% (n=19)
OOS 验证 2026:           80.6% ✅
```

评分维度：
- `TIGHT(<15%)` = +40分
- `RSI<30` = +25分
- `缩量>12H` = +20分
- `趋势略跌` = +10分

≥75分 = 🚨预警 | ≥85分 = 💣三级预警

---

## ⚡ Circuit Breaker (9-Layer Full-Chain)

```python
from brahma_brain.circuit_breaker import BrahmaCircuitRegistry

registry = BrahmaCircuitRegistry.get()

# 通过熔断器安全调用
result = registry.call_safe('confluence_score', run_analysis, symbol='BTCUSDT')

# 查看状态
registry.status_all()
# 🟢 rsi_watcher      CLOSED  失败:0
# 🟢 brahma_scan      CLOSED  失败:0
# 🟢 auto_executor    CLOSED  失败:0 (threshold=1, recovery=600s)
```

---

## 🛡️ System Health (Brahma-CI v2)

```bash
python3 brahma_brain/brahma_ci_v2.py

# 🔬 梵天360加强版 CI报告 | 07-02 09:40 UTC
# 总分: 96/100 [HEALTHY] | ❌0 ⚠️2 ℹ️38
# 覆盖: 11维探针 × 381文件 × 1786函数
```

**11-Dimension Probe Coverage:**
`P1 信号流量` · `P2 数据链路` · `P3 持仓一致性` · `P4 WS守护` ·
`P5 Cron健康` · `P6 数据鲜度` · `P7 函数契约` · `P8 数据流完整性` ·
`P9 版本一致性` · `P10 自愈能力` · `P11 日志健康`

---

## 📊 Live Performance

> 真实交易记录（非回测）。样本较小，持续累积中。

| 日期 | 品种 | 方向 | 体制 | 结果 |
|------|------|------|------|------|
| 2026-06-18 | TRUMPUSDT | LONG | CHOP_MID | ✅ +22% |
| 2026-06-15 | BTCUSDT | SHORT | BEAR_TREND | ✅ +2.1% |
| 2026-06-12 | ETHUSDT | SHORT | BEAR_TREND | ✅ +3.8% |
| 2026-06-08 | BNBUSDT | LONG | BEAR_RECOVERY | ✅ +4.2% |

*实盘记录由 `update_live_performance.py` 每日自动同步*

---

## 📁 Repository Structure

```
brahma-quant/
├── brahma_brain/          # AI大脑层 (101个模块)
│   ├── brahma_core.py     # 35维评分引擎
│   ├── timing_filter.py   # 三层时机过滤
│   ├── circuit_breaker.py # 全链路熔断器 ⭐ NEW
│   ├── exception_injector.py # 异常免疫工具 ⭐ NEW
│   ├── memory_watchdog.py # 内存水位监控 ⭐ NEW
│   └── brahma_ci_v2.py    # 11维CI探针 ⭐ NEW
├── dharma/                # 验证治理层
│   ├── dharma_360_validator.py # MC验证框架 ⭐ NEW
│   ├── realistic_cost_model.py # 真实成本建模
│   ├── regime_aware_augmentor.py # 体制增强
│   └── pump_hunter/       # 暴涨猎手系统
├── scripts/               # 执行与工具层
│   ├── blue_green_deploy.py  # 蓝绿部署 ⭐ NEW
│   ├── ai_behavior_dashboard.py # AI行为看板 ⭐ NEW
│   └── data_freshness_guard.py  # 数据鲜度守门 ⭐ NEW
├── tests/                 # 测试金字塔
│   └── test_core_brahma_units.py # 35/35 ⭐ NEW
├── guardrails/            # 防护层
├── docs/                  # 架构文档
└── BRAHMA_360_V3_DESIGN.md # 系统设计封印文档 ⭐ NEW
```

---

## 🔒 Pro Version

**Open Core 模型：框架开源，智能私有**

| | 开源版 | Pro 版 |
|--|--------|--------|
| 35维框架 | ✅ | ✅ |
| 训练好的权重矩阵 | 占位符 | ✅ v4.2铁证 |
| Timing Filter 参数 | 基础版 | ✅ 精调阈值 |
| Auto-Executor | ❌ | ✅ |
| 实时预警推送 | ❌ | ✅ Jarvis |
| Kronos M1 timing | Shadow | ✅ Live |
| 技术支持 | Issue | ✅ 直接 |

获取 Pro 版：参见 [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 🧪 Testing

```bash
# 单元测试 (35/35)
python3 -m pytest tests/test_core_brahma_units.py -v

# 包含宪法级测试 (永不删除):
# ✅ BEAR_TREND_LONG 死穴验证 (WR=45% 严禁)
# ✅ MAX_POS_PCT_NAV=10% 上限验证 (PIXEL教训)
# ✅ brahma_state 数据鲜度验证
```

---

## 📖 Key Documents

- [BRAHMA_360_V3_DESIGN.md](BRAHMA_360_V3_DESIGN.md) — Brahma-360 v3.0 完整架构设计
- [dharma/DHARMA.md](dharma/DHARMA.md) — Dharma 铁证验证体系
- [docs/](docs/) — 架构文档、体制定义、信号生命周期

---

## 📄 License

MIT License — 框架代码完全开源。
核心权重文件（`factor_weights.yaml` Pro版、`wr_matrix_v7`）属于 Pro 私有资产。

---

<div align="center">

**Brahma-Quant: 有灵魂的 crypto 量化多智能体系统**

*设计院封印 · 梵天系统 · 2026*

</div>
