# 梵天领域术语表 (CONTEXT.md)
<!-- 果蝇架构Phase 3 | 2026-09-13 苏摩111 | 从mattpocock/skills借鉴 -->

> 共享语言 = 让任何AI代理读一个文件就能理解梵天全部术语
> 变量/函数/文件命名以此为准，不允许漂移

---

## 评分维度 (s1-s22 + s5b)

| 维度 | 名称 | 说明 | Block |
|------|------|------|-------|
| s1 | 趋势方向 | MA20/MA50交叉 + ADX确认 | A |
| s2 | 动量确认 | RSI + MACD信号 | A |
| s3 | 波动率体制 | Hurst指数 + BB宽度 | A |
| s4 | 体制最优信号 | 当前体制下历史最优信号命中 | A |
| s5 | 量价配合 | 成交量×价格方向一致性 | A |
| s5b | 量价增强 | 量价配合的增强版(突破+burst) | A |
| s6 | 结构突破 | BOS/CHoCH | A |
| s7 | 局部对称 | symbol局部特征 | A |
| s8 | FVG磁铁 | Fair Value Gap磁铁效应 | A |
| s9 | OB有效性 | Order Block age<50bars且未穿越 | A |
| s10 | 跨市场共振 | BTC/ETH/山寨联动 | A |
| s11 | 布林带 | BB上下沿反弹 | B |
| s12 | K线形态 | PIPs几何形态第9维 | B |
| s13 | 日线动量 | 1D RSI + MACD | B |
| s14 | 4H动量 | 4H RSI + MACD | B |
| s15 | 15M触发 | 15分钟级触发信号 | B |
| s16 | 1H趋势 | 1H MA趋势确认 | B |
| s17 | 量能 | 成交量趋势 | B |
| s18 | 时间循环 | 周期+时段 | B |
| s19 | 距离 | 价格到关键位距离 | B |
| s20 | TARDIS | 时空回溯(历史最优窗口) | C |
| s21 | 方仓匹配 | Qdrant检索历史相似案例 | C |
| s22 | GEX | Gamma Exposure期权伽马暴露 | C |

### 扩展维度 (不在DAG稀疏激活中)

| 维度 | 名称 | 说明 |
|------|------|------|
| s23 | VolBeta | HAR-RV波动率+IV溢价 |
| s25 | Reasoning | LLM议会推理(多模型投票) |
| s26 | OI | Open Interest趋势+CVD |
| s27 | Gap Up | 跳空信号 |
| s28 | Signal Quality | 信号质量门控 |
| s29 | First Red Day | 首日红K |

---

## SMC术语 (Smart Money Concepts)

| 术语 | 全称 | 说明 |
|------|------|------|
| FVG | Fair Value Gap | 价格缺口，磁铁效应。Bull FVG=磁铁向上，Bear FVG=磁铁向下 |
| OB | Order Block | 订单块，支撑阻力。age<50bars且未被穿越才有效 |
| BOS | Break of Structure | 结构突破，趋势确认 |
| CHoCH | Change of Character | 性格转换，趋势反转信号 |
| FVG三价位 | 下沿/中点/上沿 | 下沿=支撑阻力，中点=最强阻力，上沿=失效边界 |
| 清算地图 | Liquidation Map | 止损墙(上方) + 止损池(下方) = 主力猎杀目标 |
| 共振点 | Confluence | FVG中点+有效OB+清算集群 三者交叉 = VIP精度策略 |

---

## 体制 (Regime)

| 体制 | 说明 | 做空× | 做多× |
|------|------|-------|-------|
| BEAR_TREND | 熊市趋势 | 1.60 | 0.50 |
| BEAR_EARLY | 熊市初期 | 1.20 | 0.50 |
| CHOP_MID | 震荡市 | 0.88 | 0.50 |
| BULL_TREND | 牛市趋势 | 0.50 | 1.30(ETH)/1.20(BTC) |
| BEAR_RECOVERY | 熊市恢复 | 0.50 | 1.15(ETH)/1.25(BTC) |
| BULL_EARLY | 牛市初期 | 0.50 | 1.15 |

### 体制判定依据
- Hurst指数: >0.5=趋势，<0.5=均值回归，~0.5=随机
- ADX: >25=趋势，<20=震荡
- BB宽度: 收缩=震荡，扩张=趋势

---

## 量化术语

| 术语 | 全称 | 说明 |
|------|------|------|
| HCME | Historical Context Matching Engine | 历史情境匹配引擎 |
| HAR-RV | Heterogeneous Autoregressive Realized Volatility | 异质自回归已实现波动率 |
| GEX | Gamma Exposure | 期权伽马暴露，正=支撑，负=阻力 |
| CVD | Cumulative Volume Delta | 累积成交量差，主动买-主动卖 |
| OI | Open Interest | 持仓量，增仓+做空=SHORT_BUILD |
| ATR | Average True Range | 真实波动幅度，SL计算基准 |
| Kelly | Kelly Criterion | 凯利公式，仓位 sizing |
| IC | Information Coefficient | 信息系数，维度预测能力 |
| WR | Win Rate | 胜率 |
| PF | Profit Factor | 盈亏比 |
| PIPs | Perceptually Important Points | 几何形态识别 |
| Qdrant | Vector Search Engine | 方仓向量检索引擎 |

---

## 果蝇架构术语

| 术语 | 说明 |
|------|------|
| 稀疏激活 | 按体制选择性激活维度，不激活的不计算不评分 |
| DAG | Directed Acyclic Graph，维度依赖图 |
| 端口 | 模块的输入/输出声明 |
| 封印 | 代码完成+调用验证+full_report可见+冒烟测试全绿 |
| dim_trace | 维度级可观测性，每个维度的计算过程可追溯 |
| module_registry | 27模块+10孤儿的注册表 |
| scoring_config | 6体制×2方向的稀疏激活规则 |

---

## 交易术语

| 术语 | 说明 |
|------|------|
| NAV | Net Asset Value，净资产价值 |
| SL | Stop Loss，止损 |
| TP | Take Profit，止盈 |
| RR | Risk-Reward，风险回报比 |
| position_mult | 仓位系数(0.3~1.5)，由score决定 |
| 苏摩111 | 最高批准权，发送111=立即执行 |
| VIP卡片 | 策略输出格式，姓赵不宣签名 |

---

## 文件索引

| 文件 | 说明 |
|------|------|
| brahma_brain/brahma_core.py | 核心评分引擎(4840行) |
| brahma_brain/dag_executor.py | DAG稀疏激活执行器(193行) |
| brahma_brain/dim_trace_writer.py | 维度级trace写入器(114行) |
| brahma_brain/brahma_mcp_server.py | MCP服务器(152行) |
| data/scoring_config.json | 6体制×2方向稀疏激活配置 |
| data/module_registry.json | 27模块+10孤儿注册表 |
| scripts/module_check.py | 模块健康检查 |
| MEMORY.md | 长期记忆(封印记录) |
