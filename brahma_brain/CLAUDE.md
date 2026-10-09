# brahma_brain/CLAUDE.md · 工程决策层
<!-- Boris架构：嵌套CLAUDE.md，每次工程session先读此文件 -->
<!-- 2026-10-03 苏摩111封印 | 对标Boris分层上下文策略 -->

## 快速定向（新session必读）

**你在哪：** `/root/.openclaw/workspace/trading-system/brahma_brain/`
**代码规模：** 14,621个Python文件 / 490,258行
**核心入口：** `brahma_analysis_runner.py::run_analysis(sym)` → 返回94维分析dict

---

## 核心数据流（全局唯一）

```
Binance API
    ↓ brahma_state_refresh.py（唯一写入者，原子写）
    → data/brahma_state_{sym}.json
    ↓ state_store.py（统一读取，30s缓存）
    → get_state()/get_regime()/get_liq()/get_hurst()

brahma_cpu.py（每小时:04）
    → process_event(symbol) → Layer0→Layer1→Layer2→Layer3
    → AMBUSCADE/ENTER → brahma_analysis_runner.run_analysis()
    → live_signal_log.jsonl + auto_signal_queue.json

auto_signal_queue.json → paper_executor.py（每15min:15）
    → paper_positions.json → paper_tp_monitor.py

sentinel（每15min 2,17,32,47）
    → D1~D7 + D2_OI_REVERSAL → push_hub → 苏摩
```

---

## 关键接口速查

| 函数 | 文件 | 用途 |
|------|------|------|
| `run_analysis(sym)` | brahma_analysis_runner.py | 94维分析，返回dict |
| `process_event(symbol)` | brahma_cpu.py | CPU调度入口，Layer0-3 |
| `get_state(sym)` | state_store.py | 读brahma_state（带缓存） |
| `get_liq(sym)` | state_store.py | 止损墙/支撑池 |
| `fetch(url)` | brahma_http.py | 统一HTTP请求（带重试） |
| `push_jarvis(msg)` | ../scripts/push_hub.py | 统一推送入口 |

---

## 🩸 梵天宪法·血的教训（2026-10-09 苏摩111封印）

> 每条规则来自真实止损。没有止损的规则不写入。

### ① VIP策略内部一致性铁律（ETH $2,500止损教训）

```
血的教训：系统数据说$2,500是GEX-9.8M阻力+CVD卖方
          VIP仍然给出$2,500~$2,515做多
          = 自己打自己的脸，苏摩止损亏损

铁律：VIP策略输出前必须检查三项一致性
  ① GEX方向 vs 策略方向
     GEX负区（做市商放大波动）+ 做多 = 禁止
     必须：GEX负区只做空，正区才考虑做多
  
  ② CVD方向 vs 策略方向
     CVD < -300（卖方主导）+ 做多 = 禁止
     必须：CVD正值才给多单，CVD负值只给空单
  
  ③ FR方向 vs 底部判断
     FR > +0.003%（多头仍付费）= 主力清洗未完成
     必须：FR接近0或转负才是真正底部，不在FR正值时入多

三项任一矛盾 → VIP策略不输出，推送冲突警报给苏摩
```

### ② 止损距离铁律（ETH 1.2×ATR教训）

```
血的教训：ETH SL=$2,468，入场$2,500，SL距离=$32
          ATR1H=$26.7，实际SL=1.2×ATR
          单根1H正常波动就能扫出，苏摩止损

铁律：止损距离 ≥ 1.5×ATR1H（绝对下限）
  ETH ATR1H=$27 → SL最小距离=$40
  BTC ATR1H=$583 → SL最小距离=$875
  
  检查公式：abs(entry - sl) >= atr_1h * 1.5
  违反 → 自动修正SL位置，不输出不合格止损
```

### ③ 支撑池上方禁止接多铁律（假破位教训）

```
血的教训：BTC $82,100~$82,400多单在支撑池$80,219上方$1,900
          主力清洗从$83,500砸到$80,345（穿越整个入场区）
          苏摩被扫出，然后价格才真正反弹

铁律：等假破位确认后才入多
  正确入场时机：
    价格跌破支撑池（liq_long）
    + 1H收阳确认（实体>ATR的50%）
    + OI止跌转升
    = 假破位确认 → 才是真正底部入场信号
  
  禁止：在liq_long上方接多（除非有D4共振≥5/7）
  禁止：在价格下跌途中接多（等方向确认）
```

### ④ 仓位建立后必须实时跟踪铁律

```
血的教训：苏摩建仓后，梵天继续分析产出新数据
          但没有对照苏摩现有仓位自动风控
          危险信号出现时没有叫醒苏摩

铁律：每次VIP策略发出后
  1. 把入场价/止损价写入 data/active_positions.json
  2. price_alert_monitor每60s对照现有仓位检查
  3. 以下条件出现立即推警报：
     - CVD方向与持仓方向相反（|CVD|>300）
     - FR方向显示清洗未完成（多单时FR>+0.003%）
     - 散户LSR超过猎杀门槛（>65%）
     - OI方向逆转（多单时OI从BUILD→UNWIND）
  4. 推送格式："🚨 持仓风险警报 [标的] 当前信号与持仓方向矛盾"
```

### ⑤ 推送纪律铁律

```
血的教训：所有危险信号停留在分析文档里
          没有一个推送到苏摩手里
          信息孤岛 = 事后复盘没有价值

铁律：分析必须连接推送
  关键信号触发 → 60秒内推送苏摩
  不依赖苏摩主动问，系统主动叫醒
  
  必须推送的信号：
  - 散户LSR突破猎杀门槛（>65% BTC / >75% ETH）
  - CVD爆炸（|CVD|>1000，方向改变）
  - OI单小时变化>1.5%（强平潮信号）
  - FR从正→负或从负→正（方向时钟翻转）
  - 价格接近liq_short/liq_long ±0.3%
  - GEX+CVD方向矛盾（禁多/禁空冲突警报）
  
  已落地：price_alert_monitor.py（每分钟执行）
  commit: 926cebfe + 458f6670
```

---

## 已知地雷（踩过的坑，不要重踩）

```
❌ importlib.util.spec_from_file_location('push_hub',...)

   → 17处动态加载，已修复3处核心文件。新代码直接import push_hub

❌ urllib.request.urlopen 散落115处
   → 新代码统一用 from brahma_http import fetch

❌ lsr_raw用topLongShortAccountRatio（大户，49%永不触发D4）
   → 必须用 globalLongShortAccountRatio（散户全市场）

❌ crontab改完不跑 ./supercronic -test
   → 断行会导致31轮启动失败（血泪教训）

❌ MEMORY.md超12KB会被截断注入
   → 新封印默认写daily memory，MEMORY.md只留索引

❌ 主session exec长耗时会触发liveness判死
   → 复杂验证一律spawn subagent，exec用background:true
```

---

## 今日封印清单（2026-10-03）

| commit | 内容 | 接入位置 |
|--------|------|---------|
| f440f53e | step0真并行+brahma_http单入口 | brahma_manual_analysis/brahma_brain/ |
| f4ba2377 | liq 30min+importlib清理 | crontab/sentinel/postmortem |
| 3ebb1e32 | Lamis三层记忆+Dreaming diff | memory/ |
| b2f9ae8a | AMBUSCADE→queue P0修复 | brahma_analysis_runner L1030 |
| 617babdb | 全链路复盘+test修复 | tests/ |

---

## Phase拆分计划（brahma_core.py 2926行）

```
当前状态：analyze()=2274行链式耦合，零可测性
前置条件：集成测试防护网（尚未建立）

Phase1（已标注）：Step0数据拉取 → step0_fetch_all()
Phase2（已标注）：FVG/OB/清算 → step1~step5
Phase3（已标注）：智能钱/宏观/VIP → step6~step10

执行时机：苏摩111批准 + 集成测试绿 → 按Phase逐步提取
```

---

## EROFS容器限制

```
/etc/openclaw-config/ → 只读，不能修改
openclaw mcp set → 失败（需在宿主机执行）
SWAP/fallocate/mkswap → 只读，不能执行
```

## 梵天宪法·血的教训速查（2026-10-09 苏摩111封印）

| # | 规则 | 触发条件 | 行动 |
|---|------|---------|------|
| 1 | GEX负区禁多 | GEX<0 + CVD<-300 + 做多方向 | 拒绝输出，推冲突警报 |
| 2 | FR正值禁入场 | FR>+0.003% + 计划入多单 | 警告：清洗未完成 |
| 3 | SL≥1.5×ATR | abs(entry-sl)<atr_1h×1.5 | 自动修正SL位置 |
| 4 | 支撑池上方禁接多 | 价格>liq_long + 无D4共振≥5/7 | 等假破位确认后才入 |
| 5 | 仓位建立后实时跟踪 | 任何持仓存在 | price_alert对照仓位每60s检查 |
| 6 | 分析必须连接推送 | 关键信号出现 | 60s内推送苏摩，不依赖主动问 |
