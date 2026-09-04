# 梵天35维评分地图 (BRAHMA_DIMENSION_MAP.md)
> 生成时间: 2026-08-26 | 维度来源: brahma_core_block_a/b/c.py + brahma_core.py

| 维度 | 名称 | 分值范围 | 所在文件 | 状态 |
|------|------|---------|---------|------|
| s01 | 多时间框架趋势共识（MultiTF Consensus，0~20） |  | block_a | ✅ 活跃 |
| s02 | 关键位精确度-FIB/OB/OTE（0~20） |  | block_a | ✅ 活跃 |
| s03 | RSI+CHOP体制背离（0~15） |  | block_a | ✅ 活跃 |
| s04 | SMC结构支持-BOS/CHoCH/OB（0~20） |  | block_a | ✅ 活跃 |
| s05 | 量能验证-volume spike/OBV（0~20） |  | block_a | ✅ 活跃 |
| s06 | ATR波动率体制调节（奖惩项） |  | block_a | ✅ 活跃 |
| s07 | 清算带密度+OI集中度（0~10） |  | block_b | ✅ 活跃 |
| s08 | 资金费率方向（多空溢价，±5） | ±5 | block_b | ✅ 活跃 |
| s09 | 交易时段权重（亚盘/欧盘/美盘，0~8） |  | block_b | ✅ 活跃 |
| s10 | 谐波PRZ+多周期对齐（新增，0~10） |  | block_b | ✅ 活跃 |
| s11 | 鲸鱼地址+跨市场相关+微观结构（0~10） |  | block_c | ✅ 活跃 |
| s12 | 期权Gamma+CVD订单流+OBI深度（0~10） |  | block_c | ⚠️ 活跃但CVD为K线量能估算，非WebSocket实时订单流 |
| s13 | L2订单簿+贝叶斯+宏观日历（Phase A，0~10） |  | block_c | ✅ 活跃 |
| s14 | XGBoost+在线贝叶斯+滑点+链上WS（Phase B，0~8） |  | block_c | ✅ 活跃 |
| s15 | LSTM+NLP情绪 [DEAD_CODE 封印2026-08] |  | block_c | ⛔ 已封印 |
| s16 | 量能衰竭+多周期背离共振（0~10） |  | block_c | ✅ 活跃 |
| s17 | 资金费率+多空比情绪评分（0~10） |  | block_c | ✅ 活跃 |
| s18 | bull_bear多空辩论加权（0~10） |  | block_c | ✅ 活跃 |
| s19 | 室内情绪+宏观因子合并（0~10） |  | block_c | ✅ 活跃 |
| s20 | 布林带偏离度 [DEAD_CODE 封印2026-08] |  | block_c | ⛔ 已封印 |
| s21 | RSI极值检测 [DEAD_CODE 封印2026-08] |  | block_c | ⛔ 已封印 |
| s22 | 成交量比率-宽松量化（2026-06，0~10） |  | block_c | ✅ 活跃 |
| s23 | 方仓向量库WR增强（fangcang_wr_delta，0~20） |  | brahma_core.py | ✅ 活跃 |
| s24 | P1方仓RSI分层奖惩（RSI_4H+方仓方向，±8/±12） | ±8/ | brahma_core.py | ✅ 活跃 |
| s25 | 三周期RSI共振（1H+4H+1D，P4，±8/±15） | ±8/ | brahma_core.py | ✅ 活跃 |
| s26 | PIPs几何形态（第9维扩展，0~10） |  | brahma_core.py | ✅ 活跃 |
| s27 | Hurst指数体制验证（H>0.6趋势确认，0~8） |  | brahma_core.py | ✅ 活跃 |
| s28 | HAR-RV波动率预测（波动率适配，±5） | ±5 | brahma_core.py | ⚠️ 活跃但只输出RV数值，未转换为「未来Xh价格波动区间$X~$X」 |
| s29 | HCME历史情境匹配（相似情境WR，0~15） |  | brahma_core.py | ⚠️ 活跃但adj=+0.7信号模糊，未输出具体匹配案例时间+幅度 |
| s30 | 死穴封禁检测（BEAR_TREND_LONG等，强制-999） |  | brahma_core.py | ✅ 活跃 |
| s31 | 体制乘数矩阵（regime_config权重调节） |  | brahma_core.py | ✅ 活跃 |
| s32 | SL三档分位（P0 SL宪法，仓位档位S/B-/B+） |  | brahma_core.py | ✅ 活跃 |
| s33 | timing_filter时机门控（READY/WAIT/STANDBY） |  | brahma_core.py | ✅ 活跃 |
| s34 | AI议会final_adj注入（llm_council_bridge） |  | brahma_core.py | ⚠️ 活跃但读llm_council缓存，非每次分析实时调用多模型 |
| s35 | Drawdown Protocol门控（DD≥5%/10%/15%） |  | brahma_core.py | ✅ 活跃 |

## 架构说明

```
brahma_core.analyze()
  ├── block_a: s1~s6  基础技术面（趋势/结构/量能）
  ├── block_b: s7~s10 链上+时段+谐波
  ├── block_c: s11~s22 高级因子（部分DEAD_CODE）
  └── brahma_core: s23~s35 量化增强+风控门控
```

## 因子相关性风险

> ⚠️ RSI相关维度出现在 s3/s24/s25/timing_filter 共4处，等效4x权重
> 建议：月度PCA验证正交性