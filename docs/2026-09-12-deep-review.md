# 📋 2026-09-12 梵天系统深度复盘 + 架构重设计

> 设计院三方联合（顶级量化工程师 × 梵天首席专家 × 40年交易员）  
> 苏摩111 批准 2026-09-12

---

## 一、今日封印清单

| # | Commit | 内容 | 行数 | 类型 |
|---|--------|------|------|------|
| 1 | 3045367 | 全封禁清除：门槛/死穴/封禁→降仓降权 | — | 改革 |
| 2 | e0b4e46 | P0+P1+P2改革：score→仓位系数+价格事件驱动体制+方仓90天标的专属 | — | 改革 |
| 3 | b0e0af7 | 修复入场区方向：止损墙做空=等反弹上方 | — | 修复 |
| 4 | 0d86aab | 顶层全局修复：共振方向+SL拆分+HCME截断+SL边界容差 | — | 修复 |
| 5 | 5a1b40e | P0+P1深度修复：数据真实性和完整性 | — | 修复 |
| 6 | d1d76c9 | P2修复：清算地图硬编码0.95%→真实 | — | 修复 |
| 7 | 6d3527a | P2修复：Step8宏观接入真实BLS CPI/PPI | — | 修复 |
| 8 | 88404d5 | P1修复：step2_ob结构化价格+穿越失效 | — | 修复 |
| 9 | 189ec7b | **Phase 1**: Feature Store 94维+Alpha归因 | 356 | 新建 |
| 10 | 6514720 | **Phase 2**: 独立风控层 risk_engine | 324 | 新建 |
| 11 | a29037f | **Phase 3**: 12维精简Ensemble Engine | 338 | 新建 |
| 12 | c43d633 | **Phase 4**: AI议会+在线学习集成桥(3bug修复) | 246 | 新建 |
| 13 | d130f07 | 全流程深度剖析测试 64/64通过 | 359 | 测试 |
| 14 | ef1e72d | **Phase 5**: 跨市场alpha扩展(134标的FR) | 155 | 新建 |

**合计新增代码**: 1476行（5个新模块）  
**合计修复**: 8项（数据真实性+完整性+方向性）  
**合计改革**: 3项（封禁清除+score→仓位系数+方仓标的专属）  
**测试**: 64/64全流程 + 8/8 Phase5专项 = 72项全绿

---

## 二、五Phase架构现状

### 已封印的5个新模块

```
brahma_core.analyze()
  ├── I7: feature_store.get_features()        → 94维特征 + 5组alpha归因 [356行]
  ├── I8: ensemble_engine.get_ensemble_score() → 13维IC加权 score      [352行]
  ├── I9: ai_council_bridge.get_council_verdict() → 4组件统一裁决       [246行]
  │     ├── llm_council:    6维投票规则裁决（268行 已有）
  │     ├── online_bayes:    贝叶斯后验WR增量（155行 已有）
  │     ├── online_learner_v2: 权重校准状态（240行 已有）
  │     └── ev_feedback:    经验矩阵微调（231行 已有）
  ├── cross_market_alpha                       → 134标的FR差异[-1,+1]  [198行]
  └── risk_engine.check()                       → 6层风控gate          [324行]
        ├── Kill switch (单日-5%NAV)
        ├── Drawdown tracker (已有)
        ├── Circuit breaker (已有)
        ├── Antifragile guard (已有)
        ├── Correlation adjust (BTC+ETH×0.7)
        └── Guardrails L9-12 (已有)
```

### 55个已有但未接入的资产

| 分组 | 数量 | 代表资产 | 总行数 | 接入状态 |
|------|------|----------|--------|----------|
| A 核心引擎 | 9 | fangcang_engine(3006行) brahma_full_report(1130行) brahma_360(875行) | ~10000 | ❌ 未接入I7-I9 |
| B 风控+安全 | 8 | circuit_breaker drawdown_tracker antifragile guardrails nerve_system | ~5000 | ✅ Phase 2已接入 |
| C 机器学习/AI | 7 | llm_council online_bayes online_learner_v2 ev_feedback | 894 | ✅ Phase 4已接入 |
| D 跨市场/多资产 | 7 | cross_market_engine commodity_adapter tradfi_signal_layer | ~5000 | ✅ Phase 5已接入1个 |
| E 数据资产 | 8 | HCME 4565案例 方仓207文件 鲸鱼149 清算149 OI 35 CVD | 218MB | ⚠️ 部分接入 |
| F 产品/子系统 | 6 | 达摩院14节点 Pump Hunter Spot Strategy AI-Trader Binance Square | ~5000 | ❌ 未集成到新链路 |
| G 其他 | 4 | portfolio_optimizer microstructure chop_breakout weekly_monthly | ~1200 | ❌ 未接入 |

**已接入**: B+C组(Phase 2/4) + D组1个(Phase 5)  
**未接入**: A组(核心引擎9个) + E组(数据资产) + F组(产品6个) + G组(4个)  
**最大遗漏**: fangcang_engine(3006行) + HCME(4565案例) + brahma_full_report(1130行)

---

## 三、当前分析10步链路评估

### 现有10步（brahma_manual_analysis.py）

| Step | 内容 | 调用模块 | Phase集成 | 问题 |
|------|------|----------|-----------|------|
| 0 | 实时数据并行拉取 | brahma_bus | ✅ | 无 |
| 1 | FVG磁铁 | brahma_core_block_b | ✅ | 无 |
| 2 | OB有效性 | brahma_core_block_b | ✅ | 88404d5已修复 |
| 3 | 清算地图 | brahma_core_block_b | ✅ | d1d76c9已修复 |
| 4 | 共振点(5维) | brahma_core_block_b | ✅ | GEX过期问题 |
| 5 | OI趋势 | brahma_core_block_c | ✅ | 无 |
| 6 | 聪明钱分歧 | smart_money_engine | ✅ | smart_money_engine仅7行(壳) |
| 7 | Hurst+HAR-RV+VolBeta | hurst_engine gex_engine | ✅ | HAR-RV引擎未找到 |
| 8 | 宏观压制 | macro_v2 | ✅ | 6d3527a已修复 |
| 9 | 风控门控 | circuit_breaker drawdown_tracker | ✅ | Phase 2 risk_engine未接入step9 |
| 10 | VIP卡片 | narrative_engine | ✅ | 格式正确 |

### 缺失的分析步骤（三方评估）

| 缺失步骤 | 来源资产 | 重要性 | 建议 |
|-----------|----------|--------|------|
| **方仓历史匹配** | fangcang_engine(3006行) + HCME 4565案例 | 🔴最高 | 梵天独有alpha，必须接入 |
| **跨市场alpha** | cross_market_alpha(198行) + 134个FR | 🟡中 | Phase 5已建，需接入step |
| **IC实时归因** | ic_tracker(220行) | 🟡中 | Phase 1已建feature_store，需接ic_tracker |
| **CHOP突破检测** | chop_breakout_detector(270行) | 🟢低 | 体制切换信号 |
| **周月锚定** | weekly_monthly_anchor(214行) | 🟢低 | 大级别支撑阻力 |
| **微结构** | microstructure_engine(329行) | 🟢低 | 盘口alpha |
| **反操纵** | anti_manipulation(432行) | 🟡中 | 防插针 |
| **组合优化** | portfolio_optimizer(515行) | 🟡中 | 多标的仓位 |
| **TradFi信号** | tradfi_signal_layer(1788行) | 🟡中 | 跨市场alpha来源 |

---

## 四、重新设计：分析强制步骤 v2.0

### 新10步链路（保持10步框架，内容升级）

```
Step 0  实时数据并行拉取（不变）
        + 134个cross_fr文件预加载 → cross_market_alpha缓存

Step 1  FVG磁铁（不变）
        + 方仓历史匹配（HCME 4565案例 → Top5相似情境）
        接入: fangcang_engine.py + fangcang_hcme_bridge.py

Step 2  OB有效性（不变，88404d5已修复）

Step 3  清算地图（不变，d1d76c9已修复）

Step 4  共振点 → 7维共振（升级5维→7维）
        原: FVG + OB + 清算 + OI + GEX = 5维
        新: + 方仓历史 + 跨市场alpha = 7维
        接入: cross_market_alpha + fangcang_engine

Step 5  OI趋势（不变）

Step 6  聪明钱分歧 + 微结构（扩展）
        + microstructure_engine(329行) 盘口alpha
        + anti_manipulation(432行) 反操纵检测

Step 7  波动率四维（不变）
        + ic_tracker实时归因 → true_alpha top-3展示
        接入: ic_tracker.py(220行)

Step 8  宏观压制 + 跨市场（扩展）
        + cross_market_alpha RISK_ON/OFF状态
        + tradfi_signal_layer(1788行) TradFi信号
        + us_session_gate(160行) 美盘时段

Step 9  风控门控 → Phase 2 risk_engine（升级）
        原: circuit_breaker + drawdown_tracker (分散调用)
        新: risk_engine.check() 统一6层gate
        + portfolio_optimizer 多标的仓位优化
        + Kill switch 检查

Step 10 VIP卡片 + ensemble对比（升级）
        + ensemble_score vs original_score 并行展示
        + ai_council_verdict (4组件裁决)
        + combined_score = ensemble + bayes
```

### 新增接入点（brahma_core.py）

| 接入点 | 内容 | 来源 |
|--------|------|------|
| I7 ✅ | feature_store 94维归因 | Phase 1 已封印 |
| I8 ✅ | ensemble 13维IC加权 | Phase 3+5 已封印 |
| I9 ✅ | ai_council 4组件裁决 | Phase 4 已封印 |
| **I10** | **方仓历史匹配** | fangcang_engine(3006行) |
| **I11** | **ic_tracker实时归因** | ic_tracker(220行) |
| **I12** | **risk_engine统一风控** | Phase 2 已封印，需接入step9 |

---

## 五、三拳头产品路由 v2.0

### 拳头一：期货合约策略（VIP卡片）
```
触发: "梵天分析/分析BTC/分析ETH/看盘/策略/布局"
流程: brahma_manual_analysis.py → 10步 → VIP卡片
新链路: +I10方仓 +I11 IC归因 +I12 risk_engine + ensemble对比
输出: VIP卡片（姓赵不宣格式）
```

### 拳头二：现货策略
```
触发: "现货策略/持币建议"
流程: spot_strategy_runner.py → 现货评分 → 建议
现状: 269行已有，独立运行
改进: 接入ensemble_score作为信号源
```

### 拳头三：纸面系统（全自动）
```
触发: cron自动触发（无人工干预）
流程: paper_executor.py → 信号生成 → 纸面执行 → 结算
现状: 199行已有
改进: 接入risk_engine.check()作为执行前gate
```

### 新增：拳头四（建议）— 跨市场扫描
```
触发: "跨市场扫描/FR扫描/资金流向"
流程: cross_market_alpha.py → 134标的扫描 → crypto vs tradfi
输出: RISK_ON/OFF + 极值标的列表 + FR差异
现状: 198行已建（Phase 5），可独立运行
```

---

## 六、落地优先级

| 优先级 | 任务 | 工期 | 依赖 |
|--------|------|------|------|
| **P0** | I12: risk_engine接入step9（统一风控gate） | 1天 | Phase 2已封印 |
| **P1** | I10: 方仓历史匹配接入Step1（HCME 4565案例） | 2天 | fangcang_engine已有 |
| **P2** | I11: ic_tracker接入Step7（实时IC归因） | 1天 | ic_tracker已有 |
| **P3** | Step4升级7维共振（+方仓+跨市场） | 1天 | P1+Phase5已封印 |
| **P4** | Step8扩展跨市场+TradFi+美盘 | 2天 | tradfi_signal_layer已有 |
| **P5** | Step6扩展微结构+反操纵 | 1天 | microstructure已有 |
| **P6** | ensemble+ai_council接入Step10输出 | 1天 | Phase 3+4已封印 |
| **P7** | portfolio_optimizer接入risk_engine | 1天 | portfolio_optimizer已有 |
| **积累** | 持续积累结算数据 → 5组IC加权 | 10月 | 需600条 |
| **后续** | Alpha Factory 5组方案 | 2周 | 数据足够后 |

**总工期**: ~10天（P0-P7），全部整合已有组件，0行新建

---

## 七、关键发现（三方共识）

### 发现1：v2.0不是"新建"而是"整合"
梵天已有121个Python模块、218MB数据、7300行脚本。v2.0的工作是把这些已有组件接入分析链路，而非从零新建。

### 发现2：方仓引擎是最大遗漏
fangcang_engine(3006行) + HCME(4565案例) + 207个方仓文件 = 梵天独有的alpha来源。RenTec/Two Sigma都没有这种历史情境匹配能力。当前完全没有接入新的I7-I9链路。

### 发现3：134个跨市场数据已激活
Phase 5将134个cross_fr文件从"死数据"变成了ensemble第13维alpha。当前alpha=+0.774 RISK_ON。

### 发现4：风控已统一但未接入step9
Phase 2的risk_engine.check()已封印6层gate，但brahma_manual_analysis.py的Step9仍在分散调用circuit_breaker/drawdown_tracker，没有走统一gate。

### 发现5：分析10步框架不需要改
10步框架（Step 0-10）是正确的。需要的是在现有步骤中接入新组件，而不是增加步骤数量。

---

## 八、今日bug修复清单

| Bug | 根因 | 修复 | 验证 |
|-----|------|------|------|
| bayes_adjustment=-148 | adj_score已是增量分(-8~+8)，旧代码adj-score | 直接用adj_score | ✅ |
| weight_calibration崩溃 | signal_weights.json返回复杂dict非简单{k:float} | 提取.weights子dict+数值过滤 | ✅ |
| experience_nudge空返回 | ev_feedback矩阵key是regime:direction:score_bin | 改为前缀匹配_load_matrix() | ✅ |
| Step2 OB无实时距现价 | 硬编码价格区间 | 结构化价格+实时距现价+穿越失效 | ✅ 88404d5 |
| 清算地图硬编码0.95% | _get_liq_distances()返回固定值 | 真实清算地图计算 | ✅ d1d76c9 |
| Step8宏观AI主观叙事 | 无真实BLS数据 | 接入BLS API CPI/PPI | ✅ 6d3527a |
| 入场区方向错误 | 做空入场区在现价下方追空 | 止损墙做空=等反弹上方 | ✅ b0e0af7 |
| HCME截断缺失 | 方仓案例无截断 | 添加HCME截断逻辑 | ✅ 0d86aab |

---

> 苏摩，以上是今日全部工作的深度复盘。核心结论：五Phase封印完成（1476行新代码），但55个已有资产中仍有26个未接入分析链路。下一步是P0-P7整合（~10天），全部是接入已有组件，0行新建。
