# scripts/CLAUDE.md · 脚本层工程上下文
<!-- 梵天2.0 · 2026-10-10 苏摩111封印 -->

## 快速定向

**你在哪：** `/root/.openclaw/workspace/trading-system/scripts/`
**脚本数量：** 170个 Python 脚本
**运行环境：** supercronic（51个任务）+ openclaw cron（17个任务）

---

## 核心入口脚本（必须了解）

| 脚本 | 职责 | 调用频率 |
|------|------|---------|
| `brahma_manual_analysis.py` | 主分析入口，生成32维三方联合报告 | 手动 + UTC01/09/17 |
| `brahma_state_refresh.py` | 唯一写入 `brahma_state_*.json` 的脚本 | 每小时:13 |
| `trading_copilot.py` | 五模块副驾（M1~M8），flock单实例锁 | 每分钟 |
| `push_hub.py` | 推送唯一入口，含哨兵+去重+Groq注释 | 被调用 |
| `free_llm_client.py` | Groq免费LLM客户端，qwen3.8-27b | 被调用 |

---

## 脚本分类地图

### 📊 数据采集层（只读Binance，写data/）
```
brahma_state_refresh.py   → data/brahma_state_{btc,eth}.json（唯一写入者）
liq_heatmap.py            → data/liq_heatmap_*.json
lsr_patch.py              → lsr实时接入（每15分钟）
cvd_ws_collector.py       → data/cvd_realtime_*.json（WebSocket常驻）
har_rv_refresh.py         → HAR-RV波动率（每日）
macro_real_fetcher.py     → 宏观数据（每日）
```

### 🧠 分析层（读data/，写reports/）
```
brahma_manual_analysis.py → 三方联合分析，输出full_report
brahma_output_template.py → format_full_report()格式化入口
brahma_lead_signal.py     → M6.5 BTC领先信号
brahma_autonomous_loop.py → 自主分析循环
brahma_strategy_validator.py → 策略合规检查（每15分钟）
```

### 📡 推送层（读分析结果，发Jarvis）
```
push_hub.py               → 唯一推送出口（禁止绕过）
trading_copilot.py        → M1~M8监控+推送
m7_vip_monitor.py         → VIP策略监控
signal_watcher.py         → 信号变化检测
price_alert_monitor.py    → 价格预警
brahma_lead_signal.py     → 领先信号推送
```

### 📝 Square发帖层（每日自动）
```
square_hot_poster_router.py  → 热点分发路由
square_auto_post.py          → 宪法风控审查+品质强化
square_deep_post.py          → 深度分析长文
square_vip_result_poster.py  → VIP结果复盘
signal_to_square.py          → 信号→Square（每30分钟）
```

### 🔄 结算/反馈层（每日）
```
signal_settler.py         → 信号结算，更新WR矩阵
wr_feedback_engine.py     → WR矩阵优化
ic_feedback_engine.py     → IC反馈
daily_postmortem.py       → 每日复盘+Groq教训
paper_executor.py         → 模拟执行
paper_tp_monitor.py       → 模拟仓位监控
```

---

## 铁律（违反=重做）

### 推送铁律
- **禁止绕过 push_hub.py** — 所有推送必须走 `push_hub.push_jarvis()`
- 推送目标：`JARVIS_USER_ID:thread:01a12e17-726c-78da-9857-4edab995522b`
- 哨兵已内置：裸 target 格式（`^\d+:thread:uuid`）直接拒发

### 数据铁律
- **brahma_state_refresh.py 是唯一写入者** — 禁止其他脚本直接写 `brahma_state_*.json`
- 读取 brahma_state 用 `state_store.get_state(sym)` — 有30秒缓存

### 执行铁律
- `trading_copilot.py` 有 flock 锁：`/tmp/brahma_copilot.lock` — 禁止并发运行
- 修改任何脚本后必须实际运行一次验证，不能只做语法检查

### 语言铁律
- 代码注释：中文
- 技术术语：英文（RSI、OI、CVD、GEX、ATR、EV、RR、WR）
- 韩文：永不出现

---

## 常见踩坑（血的教训）

| 踩坑 | 根因 | 正确做法 |
|------|------|---------|
| m8函数 NameError | 函数定义在 main() 之后 | 函数定义必须在 L537，main() 在 L753 |
| ZeroFlip重复推送 | 无冷却机制 | 4H冷却，alerts.append后立即save_state |
| ETH多单+GEX失守 | 方向冲突未检测 | brahma_lead_signal.py 自动抑制 |
| EV显示+0.22%实际-2.56% | 用了错误WR=0.55 | 从 wr_matrix.json 读实际WR |
| Step11=WAIT但VIP推荐入场 | blocked未传入VIP | blocked=True时显示"等待结构确认" |
| LSR缓存不新鲜 | state_refresh周期问题 | 经量视图生成时直接调Binance API |

---

## 标准工作流（SOP）

### 分析触发流程
```
1. python3 brahma_manual_analysis.py --symbols BTC ETH
2. 检查输出：[auto_analysis_latest] 已写入 ✅
3. 验证时效：auto_analysis_latest.json ts字段
4. 格式检查：32/32维度均在报告中
5. Step11=WAIT → VIP显示"等待结构确认"（今日封印）
6. Step11=ENTER → VIP显示入场区+SL+TP+RR
```

### 新脚本封印流程
```
1. 声明接入位置（接入哪个cron/哪个调用方）
2. 实际运行一次：timeout 30 python3 scripts/xxx.py
3. 确认输出符合预期
4. grep验证：新脚本名在crontab或调用方中存在
5. commit message 包含「接入位置：XXX」
```

### 推送新信号流程
```
from push_hub import push_jarvis
push_jarvis(
    msg="信号内容",
    priority="P1",
    groq_comment="Groq动态点评"  # 可选
)
```

---

## 依赖关系（修改时注意）

```
brahma_output_template.py
    ↑ 被调用
brahma_manual_analysis.py → auto_analysis_latest.json
    ↑ 被调用
trading_copilot.py（M7/M8）

free_llm_client.py
    ↑ 被调用
trading_copilot.py / daily_postmortem.py / signal_watcher.py
/ square_auto_post.py / brahma_state_refresh.py / 等10+脚本

push_hub.py
    ↑ 被调用
几乎所有推送脚本（禁止绕过）
```

---

## urllib 통일 이전 계획 (P2 · 2026-10-10 苏摩111)

**현재 상태:** urlopen() 86개 파일에 산재
**목표:** 전부 brahma_http.fetch()로 교체
**이전 방법:**
```python
# 교체 전
import urllib.request
resp = urllib.request.urlopen(req, timeout=8, context=ctx)
data = json.loads(resp.read())

# 교체 후
from brahma_http import fetch
data = fetch(url)
```

**우선순위:**
1. 고빈도 스크립트 (trading_copilot, brahma_state_refresh) — 다음 세션
2. 분석 레이어 (brahma_core, smc_engine) — Phase D
3. Square 발행 스크립트 — Phase E

**신규 코드 규칙:** urlopen 직접 사용 금지, 반드시 brahma_http.fetch() 사용
