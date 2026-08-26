# BRAHMA_BRAIN_MAP.md — 梵天代码地图
<!-- 设计院自动生成 · 2026-08-26 苏摩111封印 -->
<!-- 用途：每次会话启动时快速加载，替代重读源码 -->

---

## 🏗️ 系统架构全景

```
苏摩指令
    ↓
brahma_analysis_runner.run_analysis()   ← 唯一入口（1661行）
    ↓
brahma_core.analyze()                   ← 35维评分引擎（4353行）
    ├── brahma_core_block_a.calc_block_a()  维度1-6  技术分析层
    ├── brahma_core_block_b.calc_block_b()  维度7-10 链上/清算层
    ├── brahma_core_block_c.calc_block_c()  维度11-35 高级信号层
    └── brahma_core_entry / brahma_core_step4  主流程
    ↓
timing_filter.evaluate_timing()         ← 时机门控
    ↓
llm_council_bridge.review()             ← AI议会（4专家）
    ↓
brahma_full_report.run_full_analysis()  ← 全量报告输出
    ↓
auto_executor.py                        ← 自动执行层
    ↓
signal_settler.py                       ← 结算+WR反馈
```

---

## 📊 35维评分矩阵

### BLOCK-A（维度1-6）技术分析层 · `brahma_core_block_a.py` 399行

| 维度 | 说明 | 最高分 |
|------|------|--------|
| 趋势一致性 | EMA20/50/200多周期对齐 | +15 |
| OB新鲜度 | OrderBlock新鲜度1H | +12 |
| 关键位精确度 | 进场区与支撑/压力精确度 | +15 |
| RSI状态 | RSI超买超卖+极端加分 | +10 |
| SMC结构 | BOS/CHoCH结构确认 | +15 |
| 量能验证 | OBV方向+量价配合(B级铁证PF=1.277) | +12 |

### BLOCK-B（维度7-10）链上/清算/资金费层 · `brahma_core_block_b.py` 343行

| 维度 | 说明 | 最高分 |
|------|------|--------|
| 清算/OI | 三所实时清算集群+OI变化 | +12 |
| L/S拥挤度 | 多空比拥挤度 | +8 |
| 情绪/费率 | 资金费率+FG情绪 | +8 |
| 多周期对齐 | 15m/1H/4H/1D五周期MTF | +10 |

### BLOCK-C（维度11-35）高级信号层 · `brahma_core_block_c.py` 458行

| 维度 | 说明 | 最高分 |
|------|------|--------|
| 鲸鱼+微观 | 大单方向+微观结构 | +8 |
| Kronos(s23) | HAR-RV波动率预测+p_up/p_down | +15 |
| HCME情境匹配 | 6.5年方仓历史相似案例WR | +10 |
| 宏观+事件 | DXY+FG+宏观日历事件 | +8 |
| 方仓RSI分层 | RSI>65做多方仓奖励/矛盾惩罚 | ±12 |
| 三周期RSI共振 | 1H+4H+1D三周期同向 | ±15/±8 |

---

## 🧠 核心模块速查表

### 数据层
| 模块 | 行数 | 职责 |
|------|------|------|
| `brahma_bus` | — | 数据总线，所有实时数据唯一来源 |
| `market_state` | 778 | 多周期趋势/EMA/RSI/ATR计算 |
| `data_cache` | 657 | 磁盘缓存层，防止重复API调用 |
| `onchain_engine` | 469 | OI/多空比/资金费率 |

### 分析层
| 模块 | 行数 | 职责 |
|------|------|------|
| `brahma_core` | 4353 | **唯一评分引擎**，35维confluence_score |
| `smc_engine` | 1192 | SMC智能资金：BOS/CHoCH/OB/FVG |
| `fangcang_engine` | 1097 | 方仓向量库，6.5年历史案例匹配 |
| `hcme_matcher` | 474 | HCME历史情境匹配，余弦相似度检索 |
| `kronos_bridge` | 695 | Kronos HAR-RV波动率预测，p_up/p_down |
| `price_zone_engine` | 682 | 战场预判：高空区/低多区/路径概率 |
| `divergence_engine` | 746 | RSI/MACD背离检测 |
| `cross_market_engine` | 535 | BTC.D/DXY跨市场相关性 |
| `liq_density_engine` | 467 | 三所清算集群密度分析 |

### 信号层
| 模块 | 行数 | 职责 |
|------|------|------|
| `brahma_analysis_runner` | 1661 | **主入口**，编排所有分析模块 |
| `timing_filter` | — | 时机门控：READY/MONITOR/WAIT |
| `signal_15m_engine` | 684 | 15M触发：CHoCH/BOS/FVG/OB确认 |
| `trigger_15m` | 464 | 15M价格触发层 |
| `brahma_decision_engine` | 560 | 动态SL/TP/RR计算 |
| `position_sizer` | 759 | 仓位计算：SL三档+VaR+Kelly |

### AI议会层
| 模块 | 行数 | 职责 |
|------|------|------|
| `llm_council_bridge` | 861 | 四专家议会编排，梵天宪法已注入 |
| `reasoning_client` | — | OpenClaw LLM调用：advanced/standard |
| RiskAgent | — | Claude，风控+清算位，权重×1.0 |
| MacroAgent | — | Qwen，宏观+DXY+BTC.D，权重×0.8 |
| QuantAgent | — | Qwen，历史WR铁证，权重×0.6 |
| DevilAgent | — | Qwen，逆向质疑，权重×0.7 |

### 执行层
| 模块 | 行数 | 职责 |
|------|------|------|
| `brahma_full_report` | — | 全能力报告输出，唯一输出入口 |
| `formatter` | 1250 | 报告格式化：VIP卡片标准 |
| `auto_executor` | — | 自动下单执行，含circuit_breaker |
| `signal_settler` | — | 信号结算+WR矩阵重建+经验蒸馏触发 |

### 监控层
| 模块 | 行数 | 职责 |
|------|------|------|
| `brahma_health` | 913 | 健康检查：7项指标100/100 |
| `brahma_360` | 873 | 360体检：孤岛/数据/进程/接口 |
| `brahma_wiring_check` | 517 | 孤岛检测，pre-commit门禁 |

---

## ⚡ 关键性能参数（实测）

| 操作 | 耗时 | 备注 |
|------|------|------|
| `run_analysis()` deep=False | ~5-10s | 轻量模式 |
| `run_full_analysis()` | ~30-90s | 全量含Kronos |
| `brahma_bus` 价格获取 | ~280ms | Binance API |
| `market_state.analyze()` | ~1s | 含RSI/EMA/ATR |
| `llm_council.review()` | ~10s并行 | 修复后双模型并行 |

---

## 🚨 关键铁律（永久封印）

### 体制→策略映射
| 体制 | 做空乘数 | 做多乘数 | 核心规则 |
|------|---------|---------|---------|
| BEAR_TREND | 1.6x | **0.10x** | 做多WR=45%→封禁 |
| BULL_TREND | **0.15x** | 1.6x | 做空WR=38%→封禁 |
| CHOP_MID | 0.88x | 0.50x | 不发策略 |
| BEAR_RECOVERY | 0.30x | 1.2x | 严禁空单 |

### 死亡区（WR=0%铁证）
```
BULL_TREND + LONG + score_final≥140 + SL≥3% = 死亡区
```

### SL三档仓位宪法
- SL<1%：强制5%NAV（WR=100%铁证）
- SL 1~1.5%：限仓2%NAV
- SL 1.5~2%：限仓3%NAV

---

## 📦 数据资产现状

| 资产 | 路径 | 说明 |
|------|------|------|
| 信号日志 | `data/live_signal_log.jsonl` | 240条，字段已修复 |
| WR矩阵 | `data/wr_matrix.json` | 51个体制条目 |
| 方仓向量库 | `data/fangcang_*.jsonl` | 6.5年历史案例 |
| AI议会记录 | `data/llm_council_shadow_log.jsonl` | 235条 |
| 体制状态 | `data/regime_state.json` | BTC/ETH实时体制 |
| 宏观状态 | `data/macro_state.json` | FG/DXY/BTC.D |
| 经验文档 | `data/experience_docs/` | 自动蒸馏积累 |

---

## 🔧 常用诊断命令

```bash
# 健康检查
python3 -c "import sys; sys.path.insert(0,'brahma_brain'); import brahma_brain.brahma_health as h; r=h.run_health_check(full=True); print(r['score'], r['status'])"

# 360体检
python3 brahma_brain/brahma_360.py --report

# 梵天分析BTC
python3 -c "import sys; sys.path.insert(0,'brahma_brain'); from brahma_brain.brahma_full_report import run_full_analysis; r,d=run_full_analysis('BTCUSDT'); print(r)"

# 孤岛检查
python3 brahma_brain/brahma_wiring_check.py

# 冒烟测试
python3 scripts/brahma_smoke_test_v2.py
```

---
*自动生成于 2026-08-26 · 下次重大架构变更后更新*
