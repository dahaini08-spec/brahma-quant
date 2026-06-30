# 🏛️ 梵天系统 v9.4（五层架构 + JCP多Agent议会）

## 系统架构

```
【雷达层】RADAR
异动信号监控
radar.py
  ↓
【OI层】OI_TRACKER
合约持仓异动
oi_tracker.py
  ↓
【瞄准层】SCANNER
多时间框架分析 + 筹码层
scanner.py
  ↓
【议会层】MULTI_AGENT_COUNCIL ← NEW（JCP风格）
4 Agent 交叉验证
  📈 技术分析师（4时间框架共振）
  💎 筹码专家（POC/VAH/VAL）
  📊 衍生品分析师（资金费率/多空比/OI）
  🛡️ 风控官（一票否决权）
  → 3/4通过 + 风控PASS 才允许发出信号
  ↓
【扣扳机层】EXECUTOR + PROMPT_ENHANCER ← NEW
精确策略生成 + 提示词质检增强
executor.py + prompt_enhancer.py
  ↓
【记忆层】SYMBOL_MEMORY ← NEW（JCP风格）
按币种隔离的历史记忆
symbol_memory.py
  ↓
【发帖层】AUTO_POSTER + SQUARE
Binance Square 自动发布
auto_poster.py
```

## 核心模块说明

| 文件 | 功能 |
|------|------|
| `radar.py` | 雷达层：24h涨幅/量比异动扫描 |
| `scanner.py` | 瞄准层：4时间框架 + 筹码层(POC/VAH/VAL/HVN/LVN) |
| `executor.py` | 扣扳机层：入场区/止损/目标/R:R计算 |
| `oi_tracker.py` | OI合约持仓异动 + 多空比 |
| `multi_agent_council.py` | 🆕 4Agent议会：技术/筹码/衍生品/风控交叉验证 |
| `symbol_memory.py` | 🆕 按币种隔离记忆：历史信号/胜率/关键事实 |
| `prompt_enhancer.py` | 🆕 提示词增强+质检：发帖前内容验证和优化 |
| `strategy_manager.py` | 🆕 策略管理器：自动识别市场环境切换策略预设 |
| `auto_poster.py` | Binance Square 发帖 |
| `stats_tracker.py` | 历史信号胜率统计 |
| `coinglass.py` | CoinGlass 数据（恐贪/清算/ETF流向/OI等） |
| `legend_signal.py` | 极端市场信号（传奇入场识别器） |
| `self_learner.py` | 自我学习模块 |
| `regime_engine.py` | 市场体制识别 |

## 策略预设系统

```
BULL_TREND  🟢 牛市策略    - 只做多，仓位≤4%，议会评分≥70
BEAR_TREND  🔴 熊市策略    - 优先做空，仓位≤2.5%，议会评分≥80  
SIDEWAYS    ⚪ 震荡策略    - 4框架全对齐，仓位≤2%，议会评分≥85
HIGH_VOL    🔥 极端波动    - 超保守，仓位≤1%，R:R≥3:1
DEFAULT     ⚖️ 默认策略    - 平衡配置

# 自动切换
python3 strategy_manager.py auto

# 手动切换
python3 strategy_manager.py set BULL_TREND
```

## 发帖流程（v9.4）

```
信号 → 议会审议 → 提示词增强 → 质检 → Binance Square

质检项：
  ✅ 内容长度 ≥ 200字
  ✅ 包含入场/止损/目标/R:R/仓位
  ✅ R:R ≥ 2:1
  ✅ 仓位 ≤ 当前策略上限
  ✅ 无违禁词（必涨/稳赚等）
  ✅ 包含免责声明
```

## 使用方法

```bash
# 全自动扫描（使用当前策略预设）
python main.py

# 指定币种分析
python main.py BTCUSDT ETHUSDT

# 查看统计
python main.py --stats

# 记忆系统
python symbol_memory.py stats          # 全局记忆统计
python symbol_memory.py show BTCUSDT   # 查看BTC记忆

# 议会统计
python multi_agent_council.py BTCUSDT LONG  # 测试议会

# 策略管理
python strategy_manager.py status      # 当前状态
python strategy_manager.py auto        # 自动检测更新
python strategy_manager.py list        # 查看所有策略

# 提示词增强
python prompt_enhancer.py education    # 获取教育帖内容
```

## 数据目录

```
trading-system/
├── data/
│   ├── memory/           # 按币种记忆（JCP风格）
│   │   ├── BTCUSDT.json
│   │   └── ETHUSDT.json
│   └── strategy_config.json  # 策略配置
├── signals/              # 历史信号
└── logs/
    ├── council_log.jsonl # 议会讨论日志
    └── ...
```

## 核心标准（v5.0）

- 合约胜率目标 **≥ 90%**
- 日增粉丝目标 ≥ 300
- 创作者日收益目标 ≥ 100U
- 多时间框架共振（15m/1h/4h/1d）
- R:R ≥ 2:1（牛市允许 1.8:1）
- 高波动标的（日ATR>20%）自动切换极保守
- **议会 4 Agent 全部通过**才发出信号

---

*梵天系统 v9.4 | Blueprint v3.10 | 借鉴 JCP AI 韭菜盘多Agent架构 | 自主学习 + 自我纠正*
