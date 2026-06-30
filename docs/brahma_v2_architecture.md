# 梵天系统 v2.0 · 顶层架构设计
# 设计院出品 · 2026-06-05

## 一、设计原则

```
1. 总线程唯一入口：brahma_commander.py 是唯一调度者
2. 模块化解耦：信号 / 决策 / 执行 / 反馈 四层完全独立
3. 国库官强制路由：所有开平仓 100% 经 treasury_gate
4. 禁止绕过：任何系统不得直接写 brahma_state.json
5. 实时NAV：国库官 NAV 必须与 Binance 账户实时同步
```

---

## 二、四层流水线架构

```
┌─────────────────────────────────────────────────────────────┐
│                   brahma_commander.py                        │
│                   [ 总调度器 · 唯一总线程 ]                    │
│  每5分钟轮询 · 持有全局状态 · 协调四层 · 发钉钉/Jarvis通知     │
└──────────┬──────────┬──────────┬──────────┬─────────────────┘
           │          │          │          │
    ┌──────▼───┐ ┌────▼─────┐ ┌──▼──────┐ ┌▼────────────┐
    │ 信号层    │ │ 决策层    │ │ 执行层   │ │  反馈层      │
    │ SIGNAL   │ │ DECISION │ │ EXECUTE │ │  FEEDBACK   │
    └──────────┘ └──────────┘ └─────────┘ └─────────────┘
```

---

## 三、各层职责定义

### 层一：信号层（Signal Layer）
```
职责：产生交易信号，不做执行决策

模块：
  brahma_brain.py      → 主分析引擎（BrahmaBrain评分）
  lana_scan_report.py  → 猎手拉娜全市场扫描
  kronos_watcher.py    → Kronos L7方向预测
  on_demand_scanner.py → 按需扫描触发

输出：Signal对象（symbol/direction/score/grade/entry/sl/tp）
规则：
  - 信号只产生，不执行
  - grade<50 直接丢弃（Bridge-Gate）
  - 写入 data/signal_queue.jsonl（待决策）
```

### 层二：决策层（Decision Layer）
```
职责：对信号做最终裁决，与国库官交互申请开仓

模块：
  treasury_gate.py     → 国库官（唯一审批入口）
  dd1_confirm_gate.py  → 人工确认门（888口令）
  entry_confirm_cron.py → 自动入场确认（价格到位）
  pipeline_confirm.py  → 自动开仓决策

核心逻辑：
  接收信号层输出
  → 国库官5道关卡审批（去重/同币种/NAV/槽位/总仓）
  → 人工模式：入队等888确认
  → 自动模式：价格到位自动审批执行
  → 审批通过 → 传递执行层
  → 拒绝 → 记录原因 → 丢弃
```

### 层三：执行层（Execute Layer）
```
职责：实际与 Binance API 交互，完成下单/止盈止损

模块：
  brahma_execute.py    → 主执行器（下单/确认）
  pipeline_execute.py  → 流水线执行（含国库官集成）
  ws_guardian.py       → WebSocket 持仓监控守护
  adaptive_order_manager.py → 自适应订单管理

规则：
  - 执行前必须持有 treasury_gate 的 ApprovalResult
  - 执行成功 → 写入持仓记录（通过国库官）
  - 执行失败 → 回滚（国库官释放槽位）
  - ws_guardian 监控所有OPEN持仓，触发TP/SL自动平仓
```

### 层四：反馈层（Feedback Layer）
```
职责：结算/学习/统计/通知，形成闭环

模块：
  live_signal_settler.py → 信号结算（TP/SL/TIMEOUT）
  feedback_engine.py     → 结果反馈到达摩院
  auto_learner.py        → 自动学习更新权重
  wuqu_settler_runner.py → 武曲Paper统计
  push_hub.py            → 钉钉/Jarvis通知

规则：
  - 每笔交易结算后写入 wuqu_paper_settled.jsonl
  - 反馈到达摩院更新在线贝叶斯
  - 发送战绩通知到钉钉/Jarvis
```

---

## 四、国库官 v2.0 升级规格

### 现状缺陷
```
❌ NAV固定$1,000，未接实时Binance余额
❌ 手动平仓不同步（APP直接平仓国库官不知道）
❌ 无自动开单集成（仍需人工888确认）
❌ 无持仓P&L实时跟踪
❌ 无风控熔断与NAV挂钩
```

### v2.0 升级规格
```
✅ NAV实时同步（每15min从Binance API拉取余额）
✅ 持仓对账（每5min对比Binance实际持仓，自动清理残留）
✅ 自动开单模式（auto_mode=True时价格到位自动执行）
✅ 实时浮盈跟踪（每5min更新所有OPEN持仓PnL）
✅ 风控熔断（亏损>5%NAV时暂停新开仓）
✅ 全局敞口按实时NAV计算（不是固定$1,000）
```

---

## 五、brahma_commander.py 总线程设计

```python
# 伪代码设计
class BrahmaCommander:
    """
    梵天总调度器 · 5分钟轮询
    职责：协调四层 + 维护全局一致性
    """
    
    def run_cycle(self):
        # ── Step 1: 状态同步 ──────────────────────────
        self.sync_nav()           # 从Binance拉取最新NAV
        self.reconcile_positions() # 对比Binance实际持仓，清理残留
        self.update_regime()      # 更新体制状态
        
        # ── Step 2: 信号层 ────────────────────────────
        pending_signals = self.load_signal_queue()  # 读取待处理信号
        
        # ── Step 3: 决策层 ────────────────────────────
        for sig in pending_signals:
            if self.auto_mode:
                result = self.auto_decide(sig)  # 自动决策
            else:
                result = self.queue_for_human(sig)  # 入队等人工确认
        
        # ── Step 4: 执行层 ────────────────────────────
        approved = self.get_approved_signals()
        for sig in approved:
            self.execute(sig)  # 实际下单
        
        # ── Step 5: 反馈层 ────────────────────────────
        self.check_exit_conditions()  # 检查持仓止盈止损
        self.settle_closed_positions() # 结算已平仓
        self.notify_status()           # 推送状态到钉钉/Jarvis
```

---

## 六、自动开单集成流程

```
猎手/梵天大脑产生信号
        ↓
grade≥50 且 score≥140？
  否 → 丢弃
  是 ↓
国库官5道关卡
  拒绝 → 记录原因，钉钉通知
  通过 ↓
auto_mode检查
  手动模式 → 入队，推送钉钉等888确认
  自动模式 ↓
价格是否在入场区？（entry_confirm_engine）
  否 → 挂限价单等待触发
  是 ↓
Binance API下单（brahma_execute）
        ↓
ws_guardian监控持仓
        ↓
TP/SL触发 → 自动平仓 → 国库官request_close → 结算 → 通知
```

---

## 七、新增文件清单

| 文件 | 职责 |
|------|------|
| `brahma_commander.py` | 总调度器·总线程 |
| `treasury_gate_v2.py` | 国库官v2（含NAV同步/持仓对账） |
| `nav_tracker.py` | Binance余额实时同步 |
| `position_reconciler.py` | 持仓对账（防残留）|
| `auto_execute_engine.py` | 自动开单引擎 |
| `signal_pipeline.py` | 信号流水线（四层串联）|

---

## 八、实施优先级

```
P0（立即）：NAV实时同步
  → treasury_gate.update_nav() 接入 Binance API
  → 每15min由 state-refresh-15m cron 触发

P1（本周）：持仓对账
  → 每5min对比Binance实际仓位与国库官记录
  → 差异自动修复（手动平仓同步）

P2（本周）：brahma_commander.py 总线程
  → 统一调度现有各独立cron
  → 四层流水线串联

P3（下周）：自动开单模式
  → auto_mode flag（默认False，人工开启）
  → 开启后信号自动审批→自动下单
  → 风控熔断保护
```
