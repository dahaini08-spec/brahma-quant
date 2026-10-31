# 梵天量化系统 Brahma-Quant v3.0

> **定位声明**：梵天是面向加密永续合约的**信号研究框架**，不是自动交易系统。所有交易决策需人工确认。This is a signal research sandbox, not a live trading bot.

---

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    数据层 Data Layer                      │
│  Binance fapi (U本位永续)  CVD实时  清算热力图  宏观日历   │
└───────────────────┬─────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│                   分析层 Analysis Layer                   │
│  brahma_core.py — 94维评分引擎                           │
│  brahma_94d_engine.py — 12步骤透明推理                   │
│  达摩院(dharma_nodes) — 6节点门控验证                    │
│  HAR-RV波动率预测 + Hurst指数体制验证                    │
└───────────────────┬─────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│                   信号层 Signal Layer                     │
│  方仓引擎(fangcang) — 6.5年历史案例锚点                  │
│  signal_govern.py — 4层信号漏斗                          │
│  leading_signal_executor — 先行信号                      │
│  m7_vip_monitor — VIP策略生成                            │
└───────────────────┬─────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│                   推送层 Delivery Layer                   │
│  Jarvis (OpenClaw) — 主推送通道                          │
│  Binance Square — 公开内容发布                           │
└─────────────────────────────────────────────────────────┘
```

---

## 核心模块清单

### 🧠 brahma_brain/ — 核心引擎（105个模块）

| 模块 | 功能 | 状态 |
|------|------|------|
| `brahma_core.py` | 94维评分主引擎，94个维度→总分 | ✅ 生产 |
| `brahma_core_block_a~f.py` | 评分引擎分块（A:技术/B:结构/C:资金/D:宏观/E:方仓/F:AI议会） | ✅ 生产 |
| `brahma_core_step4.py` | extra_data构建层（OB/FVG/清算） | ✅ 生产 |
| `dharma_nodes.py` | 达摩院6节点门控（IC验证过） | ✅ 生产 |
| `smc_engine.py` | SMC结构分析（BOS/CHoCH/OB/FVG） | ✅ 生产 |
| `fangcang_engine.py` | 方仓向量锚点（6.5年历史案例） | ✅ 生产 |
| `ensemble_engine.py` | 集成引擎（多模型加权） | ✅ 生产 |
| `free_llm_client.py` | LLM路由（OR 4级降级链） | ✅ 生产 |
| `gex_unified.py` | GEX Gamma暴露计算 | ✅ 生产 |
| `liqmap_collector.py` | 实时清算热力图采集 | ✅ 生产 |
| `onchain_engine.py` | 链上指标 | ✅ 生产 |
| `universal_asset_router.py` | 42标的路由 | ✅ 生产 |

### ⚙️ scripts/ — 执行脚本（202个）

| 脚本 | 功能 | 调度 |
|------|------|------|
| `brahma_manual_analysis.py` | 手动触发全量分析，写auto_analysis_latest | 按需/UTC01/09/17 |
| `brahma_94d_engine.py` | 94维12步骤透明分析引擎（v2） | 按需 |
| `brahma_state_refresh.py` | 30min状态刷新 | `*/30 * * * *` |
| `brahma_robot.py` | 机器人主控（M1~M9九大模块） | `*/10 * * * *` |
| `trading_copilot.py` | 副驾驶（实时感知+异常推送） | `*/2 * * * *` |
| `signal_govern.py` | 4层信号漏斗（L0宏观/L1方仓/L2质量/L3共识） | `*/5 * * * *` |
| `m7_vip_monitor.py` | VIP策略监控推送 | `*/15 * * * *` |
| `leading_signal_executor.py` | 先行信号执行（15min预警） | `*/15 * * * *` |
| `vip_entry_monitor.py` | VIP入场区触达监控 | `*/5 * * * *` |
| `rsi_structure_watcher.py` | RSI结构事件扫描（42标的） | `*/5 * * * *` |
| `macro_real_fetcher.py` | 宏观数据采集 | `0 6,18 * * *` |
| `brahma_signal_early_alert.py` | 先行信号预警 | `*/30 * * * *` |
| `cvd_ws_collector.py` | CVD WebSocket实时采集 | 常驻进程 |

### 📊 验证层 arch/

| 模块 | 功能 |
|------|------|
| `arch/validation/deflated_sharpe.py` | DSR计算（Bailey & López de Prado） |
| `arch/validation/walk_forward.py` | Walk-Forward验证 |
| `arch/simulation/block_bootstrap_mc.py` | CPCV蒙特卡洛模拟 |

---

## 真实性能指标（诚实版）

> ⚠️ 以下数据均为内部记录，未经第三方审计。请独立验证后再使用。

### 统计验证

| 指标 | 数值 | 说明 |
|------|------|------|
| DSR (LdP) | **1.21** | ≥1.0判定显著；样本量n=14，统计意义有限 |
| DSR (Classic) | 1.91 | 传统DSR |
| 试验次数 N | 1286 | 历史参数调整次数估计 |
| 样本量 | 14笔结算 | **偏小，需持续积累** |

### 回测结果（大样本，有效）

| 标的 | WR | 样本量 | 说明 |
|------|-----|--------|------|
| BTC | 56.7% | n=3,655 | 下一根开盘价成交，扣手续费0.04% |
| ETH | 55.2% | n=3,572 | 同上 |

### 实盘记录（B线，2026-09-26启动）

| 项目 | 数据 |
|------|------|
| 真实实盘交易 | **0笔**（B线重置后至今） |
| 历史实盘 | 1笔TRUMP亏1.47U（3个月真实实盘全部记录） |
| WR矩阵状态 | 重建中，需积累≥50笔后更新 |

> 注：旧版WR=62% n=186数据已于2026-09-26作废，不代表当前系统性能。

---

## 信号治理架构 v3.0

### 4层漏斗（signal_govern.py）

```
L0 宏观硬门控  → FOMC前5天仓位上限1%；体制禁止组合拦截
L1 方仓先验锚  → fc=SHORT时做多降权；RSI1H<20时豁免
L1.5 时效检查  → 入场区超过4H自动作废
L2 信号质量门  → SL≥1.5×ATR1H；RR≥1.0；动态SL底线
L3 方向共识窗  → 30min内方向一致性验证
```

### 94维12步骤（brahma_94d_engine.py v2）

```
S1  RSI全周期（5M/15M/1H/4H/1D/1W）
S2  布林带多周期（收窄=爆发前兆）
S3  EMA均线阵列 + VWAP
S4  MACD多周期（1H/4H/1D）
S5  ATR波动率 + 成交量
S6  SMC价格结构（HH/HL/LH/LL全周期）
S7  FVG磁铁 + OB订单块
S8  OI资金流 + FR（SQUEEZE/BUILD/UNWIND）
S9  LSR + Taker聪明钱
S10 宏观 + 方仓 + GEX
S11 多空综合评分（双向）
S12 三周期裁决（短线/中线/长线）
```

### 体制→策略映射

| 体制 | 策略 | 做空权重 | 做多权重 |
|------|------|---------|---------|
| BEAR_TREND | 空为主 | ×1.60 | ×0.50 |
| BEAR_EARLY | 偏空 | ×1.20 | ×0.50 |
| CHOP_MID | 禁（≥110→WATCH） | ×0.88 | ×0.50 |
| BULL_TREND | 多为主 | ×0.50 | ×1.30 |
| BEAR_RECOVERY | 仅多 | ×0.50 | ×1.15 |

---

## 系统要求与启动

### 环境
```bash
Python 3.11+
supercronic（调度器，已内置于项目目录）
Binance API（合约权限，Portfolio Margin账户）
```

### 启动
```bash
cd trading-system

# 启动supercronic调度
bash scripts/start_supercronic.sh

# 启动常驻进程
setsid python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
setsid python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &

# 进程守护
bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &
```

### 手动触发分析
```bash
python3 scripts/brahma_manual_analysis.py --symbols BTC ETH
python3 scripts/brahma_94d_engine.py BTC ETH
```

---

## 数据规范（最高宪法）

**所有合约分析数据必须来源于U本位永续合约专属API**

```
主端点：https://fapi.binance.com
禁止使用：api.binance.com（现货API）用于合约分析

强制端点：
  价格：  fapi.binance.com/fapi/v1/ticker/price
  OI：    fapi.binance.com/fapi/v1/openInterest
  LSR：   fapi.binance.com/futures/data/globalLongShortAccountRatio
  Taker： fapi.binance.com/futures/data/takerlongshortRatio
```

---

## VIP策略唯一格式

```
🌿 姓赵不宣 | 今日布局
——— BTC ———
🔴 空单｜挂单区 $X~$X
止损 $X｜目标 $X→$X
杠杆 Xx｜仓位 X%
🟢 暂无多单｜等待结构
⚠️ [一句话逻辑，20字内]
🚫 破$X作废
🌿 姓赵不宣 | 不是建议
```

铁律：SL≥1.5×ATR1H / BTC+ETH方向必须一致 / 同标的禁止多空并列

---

## 免责声明

本项目为量化信号**研究工具**：
- 不构成任何投资建议
- 所有信号需人工审核确认后方可操作
- 加密合约交易风险极高，可能损失全部本金
- 回测结果不代表未来收益

---

## 版本历史

| 版本 | 日期 | 核心变化 |
|------|------|---------|
| v1.0 | 2026-07 | 基础信号引擎 |
| v2.0 | 2026-09 | 94维评分 + 达摩院 + 方仓向量库 |
| v3.0 | 2026-10-31 | 12步骤透明推理 + 信号治理漏斗 + supercronic稳定性根治 |

---

*梵天设计院 © 2026 · 苏摩111最高批准权*
