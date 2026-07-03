# STAR.md — 星枢系统 AI 协作规范
<!-- 设计院封印 · 2026-06-17 · 版本 v1.0 -->
<!-- 参考：awesome-claude-md / Claude Flow 64-Agent / OpenAI Agents Python 模板 -->

> **本文件是所有 AI Agent（议员、达摩院、猎手、太医官、研究增强层）的最高行为约束。**  
> 优先级：STAR.md > SOUL.md > 各模块默认行为。  
> 任何 Agent 在输出前必须检查本文件中的铁律。

---

## 一、系统身份声明

**系统名称：** 星枢（前身：梵天，升级中）  
**核心理念：** 梵天是唯一可信分析引擎，AI 是梵天的解释器与接口，不是独立分析师。  
**数据基准：** BTC 6.6年 + ETH 6.5年离线回放，约 49,170 条信号，无前视偏差。

---

## 二、铁律（不可违反，任何 Agent 不得绕过）

### 🔴 L0 — 执行层铁律（违反 = 立即停止输出）

1. **禁止 AI 独立计算技术指标**  
   AI 不得自行计算 RSI / EMA / MACD / 布林带 / ATR，所有指标数据必须来自 brahma_core。

2. **禁止 AI 输出未经梵天门控的交易参数**  
   止损价、目标价、入场区间，必须来自 brahma_core.analyze() 输出，AI 只负责解释。

3. **禁止绕过 treasury_gate 执行仓位**  
   任何仓位操作必须通过 treasury_gate.py → executor.py 链路，禁止直接调用交易所 API。

4. **外部研究信号权重上限：8分 / 150分（≤5.3%）**  
   QuantDinger / TradingAgents 等外部研究输出，最高注入 8 分，失败自动归零。

### 🔴 L1 — 体制死穴封禁（铁证：n≥1000，不可撤销）

| 体制×方向 | WR | 样本量 | 封禁状态 |
|-----------|-----|--------|----------|
| BEAR_TREND_LONG | 45.0% | n=3,322 | ❌ 永久封禁 |
| BULL_TREND_SHORT | 47.7% | n=4,999 | ❌ 永久封禁 |
| BEAR_RECOVERY_SHORT | 47.9% | n=603 | 🔴 强降权 0.4× |
| BULL_CORRECTION_LONG | 46.1% | n=655 | 🔴 强降权 0.4× |

**AI 看到上述体制×方向组合时，直接输出：`[BLOCKED] 死穴方向，系统拒绝`，不得补充分析。**

### 🟡 L2 — CHOP 体制约束

- CHOP_MID / CHOP_HIGH：乘数 = 0.50×，score 永远 < threshold=100，自然拦截
- CHOP_LOW：乘数 = 0.55×（待 n≥100 铁证再调）
- **AI 在 CHOP 体制下不得建议入场，只能输出：`当前无有效信号，系统待机`**

### 🟡 L3 — 样本分级制度（设计院宪法）

| 样本量 | 级别 | AI 允许引用方式 |
|--------|------|----------------|
| n < 30 | ❌ 无效 | 仅"观察到"，禁止写入结论 |
| 30 ≤ n < 100 | ⚠️ 参考 | 只能说"初步趋势" |
| 100 ≤ n < 1000 | 🟡 次级 | 可调参，需标注"待验证" |
| n ≥ 1000 | ✅ 铁证 | 可作为核心系统升级依据 |
| n ≥ 5000 | 🏆 宪法级 | WR 矩阵基准，不可推翻 |

---

## 三、Agent 角色定义与权限边界

### 3.1 核心交易引擎（不接受外部指令）

| Agent / 模块 | 角色 | 权限 |
|--------------|------|------|
| brahma_core.py | 首席评分官 | 读市场数据，输出 confluence_score |
| dd1_logic_gate | 结构确认官 | 拦截 grade<50 + RR<1.5 信号 |
| treasury_gate.py | 财务长 | 批准 / 拒绝仓位，持 NAV 数据 |
| executor.py | 执行官 | 唯一交易所下单入口 |
| ws_guardian.py | 守护官 | 持仓浮盈移止损，SL/TP 触发 |

### 3.2 研究增强层（异步，低权重，隔离网络）

| Agent | 角色 | 权限上限 | 失败行为 |
|-------|------|----------|----------|
| QuantDinger Research API | 多智能体叙事研究 | +8分注入 | 归零，不阻塞 |
| TradingAgents 辩论 | Bull/Bear 场景辩论 | +6分注入 | 归零，不阻塞 |
| Kronos 预测 | 价格方向预测 | +4分注入 | 归零，不阻塞 |
| last30days-skill | 近期叙事摘要 | 文本附注 | 忽略，不阻塞 |

**研究增强层铁律：**
- 信号 TTL = 30 分钟，过期自动归零
- 不持有交易所 API Key（只读市场数据）
- 输出必须为结构化 JSON（见 §四 格式规范）
- 网络隔离：研究容器禁止访问 trading_core_net

### 3.3 监控 & 文档层（无交易权限）

| Agent / 工具 | 角色 |
|--------------|------|
| 太医官 (phase0_monitor) | 系统健康诊断，只读，无执行权 |
| archify | 架构图 / 生命周期图生成，离线工具 |
| brahma_coordinator | 升级审核 / 垃圾清理，只写文档 |
| netviz | 一次性架构绘图，docker 按需启动 |

---

## 四、AI 输出格式规范（强制 JSON Schema）

### 4.1 研究增强层输出格式
```json
{
  "source": "quantdinger_research",
  "symbol": "BTCUSDT",
  "direction": "SHORT",
  "regime_assumption": "BEAR_TREND",
  "confidence": 0.72,
  "score_delta": 6,
  "narrative": "多空辩论结论：空方主导，宏观压力延续",
  "ttl_minutes": 30,
  "generated_at": "2026-06-17T16:00:00Z",
  "fail_safe": "zero_on_timeout"
}
```

### 4.2 梵天信号卡片输出格式（AI 解释层）
```json
{
  "signal_id": "sig_xxx",
  "symbol": "BTCUSDT",
  "direction": "SHORT",
  "regime": "BEAR_TREND",
  "confluence_score": 142,
  "entry_zone": [64200, 64800],
  "sl": 66100,
  "tp1": 62000,
  "tp2": 59500,
  "rr": 2.8,
  "ai_interpretation": "梵天 142 分空单，BEAR_TREND 体制 S 级，建议标准仓位",
  "blocked_reason": null
}
```

### 4.3 禁止输出格式（AI 不得生成）
- ❌ 未附 `signal_id` 的入场建议
- ❌ 含"建议观察"等模糊表述（梵天无信号时只能说"系统待机"）
- ❌ n<30 的 WR 数据作为结论依据
- ❌ 直接输出止损价而不标注来源（brahma_core / swing_4h / ATR4H）

---

## 五、体制×方向 WR 矩阵（铁证速查，AI 引用基准）

```
数据来源：BTC+ETH 6.5年 n≥100 离线回放（无前视偏差）

✅ S级 Alpha（WR≥64% + avg_pnl>0）：
  BEAR_EARLY_SHORT   WR=66.5%  avg_pnl=+0.090  n=5,896  ← 当前体制
  BULL_EARLY_LONG    WR=64.4%  avg_pnl=+0.093  n=5,396
  BEAR_TREND_SHORT   WR=71.8%  avg_pnl=+0.182  n=2,413
  BULL_TREND_LONG    WR=70.3%  avg_pnl=+0.242  n=3,046
  BULL_CORRECTION_SHORT WR=73.9% avg_pnl=+0.123 n=494
  BEAR_RECOVERY_LONG WR=72.5%  avg_pnl=+0.255  n=430

❌ 死穴（AI 遇到必须输出 BLOCKED）：
  BEAR_TREND_LONG    WR=45.0%  avg_pnl=-0.265  n=3,322
  BULL_TREND_SHORT   WR=47.7%  avg_pnl=-0.229  n=4,999
  BEAR_RECOVERY_SHORT WR=47.9% avg_pnl=-0.235  n=603
  CHOP_*/any         avg_pnl≈-0.04（手续费侵蚀，双向封禁）
```

---

## 六、维护规则

- **修改本文件**：需设计院审核，commit 信息前缀 `[STAR-UPDATE]`
- **新增 Agent**：必须在 §三 中声明权限边界，否则默认权重 = 0
- **铁律 L0 / L1 不可删除**，只可新增
- **版本号**：每次修改递增（当前 v1.0）

---

---

## 七、落地状态（v1.1 · 2026-06-17）

| 模块 | 状态 | 路径 | 测试 |
|------|------|------|------|
| external_signal.py | ✅ 已落地 | brahma_brain/external_signal.py | 6/6 PASS |
| research_bridge.py | ✅ 已升级 | trading-system/research_bridge.py | 6/6 PASS |
| brahma_core s_research | ✅ 已注入 | brahma_core.py line 1028 | 语法OK |
| trading_agents_bridge.py | ✅ Paper模式 | brahma_brain/trading_agents_bridge.py | s24 M0 |
| Docker 隔离方案 | 📋 待部署 | docker-compose.research.yml | - |
| Webhook 服务 | 📋 待部署 | scripts/research_webhook_server.py | - |

**激活 dry_run=False 步骤：**
```bash
# 1. 启动研究容器（宿主机有 Docker 时）
docker compose -f docker-compose.research.yml up -d

# 2. 确认 QuantDinger 无交易权限
docker exec quantdinger_research env | grep AGENT_LIVE

# 3. 激活注入（需 ADMIN_TOKEN）
curl -X POST http://localhost:8890/control/dry_run \
  -H "X-Admin-Token: xingshu_admin_2026" \
  -d '{"dry_run": false}'

# 4. 验证链路
python3 research_bridge.py --test
python3 -c "from brahma_brain.external_signal import status; print(status())"
```

---

## 八、AI 输出格式强制规范（v1.1 · 2026-06-18 · 设计院封印）

### 8.1 体制英文必须附中文注释

**任何体制英文标签出现时，必须同时附上中文备注。无例外。**

```
✅ 正确：BEAR_RECOVERY（熊市反弹）  WR=72.5%
✅ 正确：体制：BEAR_TREND → BEAR_RECOVERY（熊市趋势 → 熊市反弹）
❌ 错误：BEAR_RECOVERY 切换  （单独英文，无中文）
❌ 错误：当前体制 BEAR_RECOVERY，方向 LONG  （无中文注释）
```

**强制映射表（所有AI输出必须使用）：**

| 英文标签 | 中文注释 | 方向 | 一句话 |
|----------|----------|------|--------|
| BEAR_TREND | 熊市趋势 | SHORT✅ LONG❌封禁 | S级主力做空 WR=71.8% |
| BEAR_EARLY | 熊市初期 | SHORT✅ LONG⚠️ | 趋势形成中 WR=66.5% |
| BEAR_RECOVERY | 熊市反弹 | LONG✅ SHORT❌封禁 | 反直觉alpha WR=72.5% |
| BEAR_CRASH | 暴跌体制 | SHORT⚠️ LONG⚠️ | 极端降权 |
| BULL_TREND | 牛市趋势 | LONG✅ SHORT❌封禁 | S级主力做多 WR=70.3% |
| BULL_EARLY | 牛市初期 | LONG✅ SHORT⚠️ | 空间最大 WR=64.4% |
| BULL_CORRECTION | 牛市回调 | SHORT⚠️ LONG❌封禁 | 低频高胜率 |
| BULL_PEAK | 牛市末期 | 两向⚠️ | 趋势衰竭 |
| CHOP_HIGH | 强震荡 | 双向❌ | 封禁 |
| CHOP_MID | 弱震荡 | 双向❌ | 封禁 |
| CHOP_LOW | 低位震荡 | 双向⚠️ | 待验证 |

### 8.2 死穴封禁输出规范

**遇到死穴体制×方向组合，AI 必须输出「硬封禁」而非「降权/谨慎」：**

```
✅ 正确：BEAR_RECOVERY（熊市反弹）× SHORT → ❌ 硬封禁（WR=47.9% n=603 死穴）
❌ 错误：BEAR_RECOVERY_SHORT 降权0.4×，建议谨慎做空
❌ 错误：BEAR_RECOVERY 体制不建议做空

交易哲学：不封禁 = 为交易而生（统计上期望值为正或无铁证时，降权不封禁）
         硬封禁 = 达摩院铁证 WR<50% n≥100（期望值为负，保护资金）
```

**当前四大死穴（v25.4 · 2026-06-18 封印）：**
1. BEAR_TREND（熊市趋势）× LONG — WR=45.0% n=3322 宪法级死穴 ❌
2. BULL_TREND（牛市趋势）× SHORT — WR=47.7% n=4999 宪法级死穴 ❌
3. BEAR_RECOVERY（熊市反弹）× SHORT — WR=47.9% n=603 次铁证死穴 ❌
4. BULL_CORRECTION（牛市回调）× LONG — WR=46.1% n=655 次铁证死穴 ❌

### 8.3 phase / momentum 输出规范（同样附中文）

| 英文 | 中文注释 |
|------|----------|
| UPTREND | 上涨趋势 |
| DOWNTREND | 下跌趋势 |
| PULLBACK_UP | 上升回调（买点） |
| PULLBACK_DN | 下降反弹（空点） |
| TOPPING | 顶部形成中 |
| BOTTOMING | 筑底形成中 |
| CHOP | 震荡无方向 |
| BULLISH | 偏多动能 |
| BEARISH | 偏空动能 |
| NEUTRAL | 中性 |

_设计院封印 · 星枢系统 · 2026-06-18 v1.2_
