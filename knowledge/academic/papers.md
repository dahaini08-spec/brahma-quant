# 量化金融学术论文核心结论库
<!-- 2026-08-27 设计院封印 | 三层知识架构 Layer2 -->

## 一、必读经典论文

### 1. Lil'Log: LLM Powered Autonomous Agents（Weng, 2023）
**核心结论：** AI Agent = Planning + Memory + Action 三要素
**梵天映射：**
- Planning → brahma_analysis_runner.run_analysis()
- Memory → Qdrant向量库 + MEMORY.md
- Action → auto_executor.py

### 2. Attention Is All You Need（Vaswani et al., 2017）
**核心结论：** Transformer架构，自注意力机制捕捉序列长程依赖
**梵天相关：** Kronos时序预测模型的底层架构；HAR-RV的注意力扩展

### 3. Bitcoin: A Peer-to-Peer Electronic Cash System（Nakamoto, 2008）
**核心结论：** 去中心化信任机制，基于工作量证明的共识
**梵天相关：** 理解BTC减半机制和供应上限对长期价值的支撑

### 4. Momentum（Jegadeesh & Titman, 1993）
**核心结论：** 过去3-12个月表现好的股票，未来3-12个月继续跑赢
**加密实证：** 动量效应在加密市场更强（高波动放大动量）
**梵天映射：** 体制识别本质是动量因子的制度化

### 5. The Anatomy of a Trading Strategy（Lo, 2017）
**核心结论：** 成功策略必须有：明确的信号生成→风险管理→执行的完整链路
**梵天映射：** 35维评分→SQE门控→auto_executor 正是这个链路

---

## 二、加密市场专项研究

### 6. Cryptocurrency Trading: A Comprehensive Survey（2021）
**核心结论：**
- 技术分析在加密市场短期有效性高于传统市场（市场效率较低）
- 动量策略在1-7天窗口表现最优
- 链上数据具有显著的预测价值

**梵天启示：** 验证了梵天35维评分中技术面占比合理

### 7. Bitcoin and the Future of Digital Payments（Böhme et al., 2015）
**核心结论：** BTC价格受网络效应驱动，梅特卡夫定律适用
**梵天相关：** 活跃地址数增长 → 网络价值提升 → 长期看涨因子

### 8. The Bitcoin Halving and Crypto Market Cycles（多篇，2020-2024）
**综合结论：**
- 减半前100天到减半后500天是历史最强上涨区间
- 每次减半后的涨幅递减（1st: 100x, 2nd: 30x, 3rd: 8x）
- 机构化程度上升导致周期性弱化

**梵天应用：** 宏观周期仅作背景，不作主要交易依据

---

## 三、风险管理经典

### 9. When Genius Failed（Lowenstein, 2000，关于LTCM）
**核心教训：**
- 极高杠杆 + 极度相关的头寸 = 系统性风险
- 历史WR=99%的策略，在黑天鹅事件中可以归零
- 流动性假设在危机中失效

**梵天封印：** 这正是梵天MAX_POSITIONS=20 + 单笔NAV×2%上限的根本原因

### 10. The Kelly Criterion and the Stock Market（Thorp, 2011）
**核心结论：**
- 凯利公式在长期最大化对数财富
- 实践中用半凯利（×0.5）更稳健，避免波动过大
- 负EV的赌注无论多小都不应下注

**梵天封印：** WR×RR决定EV，EV≤0直接封禁，这是凯利公式的直接推论

---

## 四、AI与量化交易前沿

### 11. Deep Learning for Stock Market Prediction（多篇，2019-2024）
**综合结论：**
- LSTM/Transformer在价格预测上准确率通常仅55-60%（barely above random）
- 特征工程比模型选择更重要
- 过拟合是最大风险，OOS验证必须严格

**梵天启示：** 为什么梵天不用纯DL预测价格，而用规则+WR矩阵

### 12. Reinforcement Learning for Trading（多篇，2020-2025）
**综合结论：**
- RL策略在回测中表现优秀，但实盘中因非平稳性严重退化
- 市场微观结构变化（做市商行为变化）会使RL策略快速失效
- 最佳实践：RL用于参数优化，不用于直接交易决策

**梵天结论：** WR矩阵+规则系统在当前NAV规模下优于RL

### 13. Agents in Finance: A Survey（2024）
**核心结论：**
- 多智能体系统在金融决策中优于单智能体（多视角对抗减少偏见）
- 角色分化（乐观/悲观/中性）提升决策质量
- 人类监督仍然必要，尤其在体制切换点

**梵天验证：** AI议会4专家架构与此结论完全一致；苏摩111是人类监督的制度化
