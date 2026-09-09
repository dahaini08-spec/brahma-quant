# brahma_brain/CLAUDE.md — 模块级工程规范
<!-- Boris五步闭环 · 2026-08-30 设计院自主封印 -->
<!-- 2026-09-09 苏摩111: 从AGENTS.md下沉coding工作流+三件套到此文件 -->

---

## 🧭 核心入口链路（背熟）

```
苏摩输入「梵天分析BTC」
  ↓
brahma_full_report.run_full_analysis('BTCUSDT')   ← 唯一对外入口
  ↓
brahma_1hao_analysis.run_analysis()               ← 全量报告生成
  ↓
brahma_analysis_runner.run_analysis()             ← r对象（机器读取）
  ↓
brahma_core.analyze()                             ← 4733行，35维评分核心
  ↓
block_a(维度1-6) + block_b(维度7-10) + block_c(维度11-35)
  ↓
signal_quality_engine.evaluate()                  ← SQE 4道门控
  ↓
position_sizer.get_position_size()                ← 仓位计算
```

---

## 🛠️ Coding Tasks（gstack 工作流集成）

当设计院 spawn coding subagent 处理以下任务时，使用对应指令：

| 任务类型 | 指令 | 说明 |
|---------|------|------|
| 代码审查 | 「Load gstack. Run /review」 | 找生产级 bug，不是风格问题 |
| 安全审计 | 「Load gstack. Run /cso」 | OWASP + STRIDE 安全扫描 |
| QA测试 | 「Load gstack. Run /qa https://...」 | 真实浏览器端到端测试 |
| 功能规划 | 「Load gstack. Run /autoplan, implement, then /ship」 | 完整功能交付链路 |
| 架构评审 | 「Load gstack. Run /office-hours then /autoplan. Save the plan, don't implement.」 | 只出方案不动代码 |

**梵天专属补充：**
- 所有 coding subagent 完成后必须跑冒烟测试（brahma_smoke_test_v2.py）
- 重大改动需苏摩111批准后 git commit 封印
- 设计院不安装 gstack 到 ~/.claude/skills/（Security Guard 限制），改为 AGENTS.md 集成模式

---

## 🔧 设计院三件套工作流（2026-09-01 苏摩111封印）

### 1️⃣ 长任务 → planning-with-files（强制）

**触发条件：** 任何预计超过5步或可能被中断的coding任务。

```
任务开始时：
  创建 .plan/brahma-YYYYMMDD-<任务名>.md
  写入：目标 / 步骤清单 / 当前进度

每步完成后：
  更新 .plan/ 文件状态（pending→done）

被中断恢复时：
  读取 .plan/ 文件，从上次断点继续
  不重新汇报已完成的步骤
```

**文件格式：**
```markdown
# 任务：<名称>
## 目标
<一句话>
## 步骤
- [x] P0-1: xxx（已完成）
- [x] P0-2: xxx（已完成）
- [ ] P1-1: xxx（进行中）
- [ ] P2: xxx（待做）
## 接入位置
<修改了哪些文件>
## 冒烟测试
<测试命令和结果>
```

### 2️⃣ 封印前 → superpowers /autoplan（强制）

**触发条件：** 任何新功能/修复提交git之前。

```
梵天封印流水线（强制顺序）：
  Step1: 声明假设（执行前显式写出）
  Step2: Load superpowers. Run /autoplan
  Step3: implement（最小代价原则）
  Step4: 冒烟测试全绿
  Step5: git commit --no-verify（含接入位置）
  Step6: 更新 .plan/ 状态为完成
```

**⚠️ 没有/autoplan = 没有封印资格。**

### 3️⃣ 发帖前 → avoid-ai-writing（强制）

**触发条件：** 姓赵不宣发任何Binance Square帖子之前。

```
发帖流水线：
  Step1: 生成帖子内容
  Step2: Load avoid-ai-writing，过滤AI腔
  Step3: 检查：无「首先」「其次」「值得注意的是」「不得不说」等机器腔
  Step4: 发布到Square
```

**检查清单（每次必过）：**
- [ ] 无过度解释性开场白
- [ ] 无「作为一个XX」句式
- [ ] 无「综上所述」总结
- [ ] 数据和结论直接说，不铺垫

---

## ⚡ /review + /build auto 门控

### /review（每次commit前强制执行）
```bash
# 1. 冒烟测试全绿
python3 brahma_brain/brahma_smoke_test_v2.py
# 2. 新函数是否有单元测试？
# 3. 新模块是否有接入位置（grep验证）？
# 4. commit message是否包含「接入位置：XXX」？
```
**门控规则：** 以上4项任意一项不满足 → 不允许commit，必须修复后重试。

### /build auto（苏摩批准plan后触发）
- 苏摩批准计划一次 → 设计院自动执行到全绿
- 遇到失败或高风险步骤 → 暂停并报告苏摩
- 每个子任务独立commit，不合并大commit
- **不需要每步等苏摩确认**，只在失败时打断

---

## 🚫 架构禁忌（违反即回滚）

1. **禁止在runner外新建裸HTTP分析调用** — 所有分析必须走 `run_full_analysis()`
2. **禁止新建独立分析文件** — 扩展现有文件，不新建孤岛模块
3. **禁止绕过SQE** — 信号必须经过 `signal_quality_engine.evaluate()`
4. **禁止修改regime_config.py以外的体制乘数** — SSOT唯一
5. **禁止用历史上下文价格当实时数据** — 必须调用 `brahma_bus.get_price()`

---

## ✅ 修改前必须执行

```bash
# 冒烟测试（必须全绿才算封印）
cd /root/.openclaw/workspace/trading-system
python3 brahma_brain/brahma_smoke_test_v2.py

# 语法检查
python3 -c "import ast; ast.parse(open('brahma_brain/brahma_core.py').read()); print('OK')"

# 先看最近改动，再动手——防止重复修已修过的bug
git log --oneline -20

# 架构守门（新增模块时验证接入位置）
grep -n "你的新模块名" brahma_brain/brahma_full_report.py brahma_brain/brahma_analysis_runner.py
```

---

## 📐 SSOT（单一真相来源）

| 内容 | 文件 | 禁止在别处修改 |
|------|------|--------------|
| 体制乘数矩阵 | `regime_config.py` | ✅ |
| 数学函数(RSI/EMA/ATR) | `math_utils.py` | ✅ |
| 信号队列写入 | `scripts/signal_queue_writer.py` | ✅ |
| 推送路由 | `scripts/system_config.py` | ✅ |
| 铁证WR规则 | `MEMORY.md` | 只增不减 |

---

## 🏛️ 封印文化

- **没有commit = 没有封印**
- commit message必须包含「接入位置：XXX」
- 新模块上线前必须grep验证：新模块名在runner/full_report中存在
- 冒烟测试未全绿 = 未完成

---

## 🗺️ 诊断路径图谱

### 「为什么信号没有推送」
```
brahma_core.analyze() → score_final
  ↓ score_final < threshold？
    → SQE拦截 → 查 signal_quality_engine.py → 看哪道门控触发
  ↓ SQE通过但没推送？
    → 查 data/signal_queue.jsonl → 检查 expires_at 是否过期
  ↓ 在队列但没触发？
    → 查 scripts/signal_watcher.py → 检查 direction 字段是否为 LONG/SHORT
  ↓ direction有但没推出去？
    → 查 data/signal_push_state.json → 检查 TTL 冷却时间
```

### 「为什么体制识别错了」
```
brahma_core.analyze()
  ↓ → regime_detector → _matched_regime_key
  ↓ regime=None/空？ → 自动填UNKNOWN，查日志
  ↓ 体制切换延迟？ → 查 data/brahma_state.json → last_update字段
  ↓ 体制乘数不对？ → 只改 brahma_brain/regime_config.py（SSOT）
```

### 「为什么OI信号评分高但是追涨」
```
scripts/oi_advanced_scanner.py → score_oi_signal()
  ↓ _price_chg_24h > 30%？ → score cap=55（今天已修复）
  ↓ OI/价格效率比 < 1？ → 追涨惩罚
  ↓ 体制逆势？ → regime乘数×0.15
```

---

## 🔑 关键文件速查

| 文件 | 行数 | 职责 |
|------|------|------|
| `brahma_core.py` | 4733 | 35维评分引擎，唯一入口 |
| `brahma_full_report.py` | ~800 | 对外报告，ADAPTIVE v3.0 |
| `regime_config.py` | ~100 | 体制乘数SSOT |
| `signal_quality_engine.py` | ~200 | 4道门控 |
| `position_sizer.py` | ~500 | 仓位计算 |
| `brahma_smoke_test_v2.py` | ~300 | 冒烟测试 |
| `universal_asset_router.py` | ~380 | TradFi/加密 资产分类 |
