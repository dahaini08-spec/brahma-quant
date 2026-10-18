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

## ⚠️ signal.alarm 子线程限制（P1 待修复）

**问题：** `scripts/brahma_manual_analysis.py:117` 使用 `signal.alarm(MAX_RUNTIME_S)`
Python限制：`signal.alarm` 只能在主线程调用，子线程调用会抛 `ValueError: signal only works in main thread`

**影响：** 当分析被并发子线程调用时，超时机制完全失效 → 分析永久挂死

**修复方案（苏摩111批准后执行）：**
```python
# 修复前
signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(MAX_RUNTIME_S)

# 修复后
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
with ThreadPoolExecutor(max_workers=1) as ex:
    fut = ex.submit(_run_analysis, symbol)
    try:
        result = fut.result(timeout=MAX_RUNTIME_S)
    except FutureTimeout:
        raise TimeoutError(f'分析超时 {MAX_RUNTIME_S}s')
```

**当前状态：** 已记录，单进程模式下不触发，并发场景下有风险

---

## 🚨 血的教训六条铁律（2026-10-10 苏摩111封印）

> 来自实际生产事故，永久有效，不可绕过。

### ① 改代码必须立即运行验证
```
错误做法: 修改 → py_compile语法检查 → 提交
正确做法: 修改 → py_compile → 实际执行一次 → 看输出无报错 → 提交

血的教训: m8_groq_smart_layer 定义在 main() 之后
          py_compile通过，实际运行 NameError
          = trading_copilot 每分钟 exit status 1
          = 没人发现，系统静默崩溃
```

### ② 函数定义顺序铁律
```
Python 在执行时解析名称，不是在解析时。
新函数必须定义在所有调用点之前。
主函数 main() 调用的所有函数 → 必须在 main() 之前定义。
```

### ③ 新增cron前必查日志
```
错误做法: 直接添加新cron任务
正确做法: tail -50 logs/supercronic.log | grep error
          确认现有任务无报错后再添加
```

### ④ 宿主资源先查后动
```
2核/3.7GB/无Swap = 硬性约束
新增高频任务前必须:
  free -h → 确认可用内存 > 500MB
  uptime  → 确认load < 1.5
```

### ⑤ 每次修复后必须冒烟测试
```
timeout 20 python3 scripts/xxx.py 2>&1 | tail -5
看到正常输出才算完成，不是"语法OK就封印"
```

### ⑤-a Red-Green 测试驱动（核心算法强制）
<!-- [Red-Green蒸馏 2026-10-14 苏摩111] mattpocock/skills → CLAUDE.md -->

**适用范围：** 纯数学函数（Z-Score/EV/ATR/仓位计算等）新建或重写时

**顺序：先写断言 → 再写实现 → 断言通过 → 冒烟测试 → commit**

```python
# 示例：新建 Z-Score 函数前，先写断言
assert calc_zscore(returns=[-0.1,-0.1,-0.1,-0.1,+2.0]) > 2.0   # 极端上涨=高Z
assert calc_zscore(returns=[0.0, 0.0, 0.0, 0.0, 0.0]) == 0.0   # 无波动=0
assert step5c_zscore({'k1h':[]})['gate'] == 'SKIP'               # 数据不足=SKIP

# 断言全通过后才写实现代码
# def calc_zscore(...): ...
```

**不适用：** 接入新字段/扩展现有函数/配置修改 → 保持现有流程

### ⑥ 体制相关模块禁止擅自启用
```
btc_regime_watcher.py 等体制模块已被苏摩废弃
不得在未确认的情况下重新注册到cron
涉及体制/铁律修改 → 必须苏摩111批准
```

---

## 🔤 语言铁律（2026-10-10 苏摩111永久封印）

**代码注释：中文**
**回复输出：中文**
**commit message：中文**
**技术术语：英文（如 RSI、OI、CVD、Hurst、ATR、GEX、EV、RR）**
**韩文：永不出现**

违反 = 重做。

---

## 🏛️ 三方联合深度评估封印（2026-10-10 苏摩111）

> 本节为今日35个commit全量工作的最终裁决，写入最高宪法永久生效。

### 三方裁决

**🔬 量化工程师：**
今日核心数据质量提升：
- EV计算从伪造WR=0.55改为真实WR矩阵（CHOP_MID:LONG实际=0.0）
- LSR从缓存改为实时API（延迟从30分钟→5分钟）
- RR计算从入场上沿改为中间值（更保守、更现实）
- GEX逻辑修正：正Gamma区标注"空单需谨慎"
- 全库韩文清零：116行→0行

**📐 达摩院：**
架构完整性评分：**9/10**
- 35个commit，P0修复14个，全部经过实际运行验证
- Step11=WAIT时VIP不再推荐入场方向（矛盾消除）
- push_hub单一推送入口完整，ZeroFlip 4H冷却生效
- 扣分项：RAM 70%，接近警戒线，禁止继续新增高频任务

**⚔️ 40年交易员：**
梵天2.0今日成熟度：**可用，但CHOP_MID下谨慎**
- 分析结论可信，BTC/ETH均显示WAIT → 正确
- CHOP_MID体制下WR=0.0，系统正确拒绝入场
- GEX正Gamma区+RSI4H=92→空单方向已标注警告
- 下一步等待：RSI4H回落<70 或 体制切换至BULL/BEAR_TREND

### 永久封印铁律（今日新增，最高优先级）

```
① 语言铁律（b940b8e6封印）
   代码注释中文 / 回复中文 / commit中文
   技术术语英文 / 韩文永不出现
   违反 = 重做

② EV诚实铁律（ed04c348封印）
   EV计算必须使用wr_matrix.json实际WR
   n<8时用fallback=0.45，禁止伪造WR=0.55
   CHOP_MID:LONG WR=0.0 = 系统必须拒绝入场

③ 分析一致性铁律（ed04c348封印）
   Step11=WAIT → VIP只显示"等待结构确认"
   Step11=ENTER → VIP才显示入场区/SL/TP/RR
   禁止出现WAIT+挂单区并存的矛盾

④ CLAUDE.md三层架构（47574f34封印）
   brahma_brain/CLAUDE.md → 算法层
   scripts/CLAUDE.md      → 脚本层（新增）
   data/CLAUDE.md         → 数据层（新增）
   每次工程session先读这三个文件，3分钟定向

⑤ 运行验证铁律（de47626e封印）
   语法通过 ≠ 功能正常
   每次修改必须实际运行一次
   timeout 20 python3 scripts/xxx.py 看到正常输出才封印
```

### 今日数据（永久记录）
```
日期：2026-10-10
commits：35个
P0修复：14个（trading_copilot/ZeroFlip/EV/Step11-VIP/GEX）
新功能：11个（Groq全面接入/M8异常感知/嵌套CLAUDE.md）
全库韩文：116→0行
系统RAM：70%（警戒，禁止新增高频cron）
分析合规：32/32 = 100%
```

---

## 🏛️ 预判系统最高宪法（2026-10-11 苏摩111封印）

### 分析输出铁律

**第一行永远是预判，不是体制确认：**
```python
# 错误（确认系统）：
output = f"当前体制{regime}，Score={score}，EV={'正' if ev>0 else '负'}，裁决：WAIT"

# 正确（预判系统）：
output = f"主力下一步：{predict_action}（概率{prob}%），入场窗口{window}H内"
```

### 体制使用铁律

```
体制 = 背景参数（影响仓位权重）
体制 ≠ 入场门控（不能因CHOP_MID直接拒绝分析）

CHOP_MID时的正确分析：
  1. 判断震荡蓄力方向（FVG票数+清算密度）
  2. 预判突破时序（OI收缩→扩张转折点）
  3. 挂单等待（不是等体制切换）
```

### 1个月复盘数据铁律

```
实盘数据（2026-09~10）：
  总信号814个 | CHOP_MID=711个(87%) | SL=29个
  结论：87%信号在CHOP_MID = 错误的等待策略导致
  
  正确策略：CHOP_MID内预判突破方向，提前布局
  不是：等体制变成BULL_TREND再分析
```

### scripts/brahma_output_template.py 修改指引

```
format_full_report() 输出顺序强制调整：
  第1块：主力预判（新增，必须第一）
  第2块：D1~D10 数据维度（原有）
  第3块：Step11裁决（背景参考，不是主角）
  第4块：VIP策略（基于预判，不基于Step11）
  
  VIP策略条件：
    Step11=WAIT + 有明确预判结构 → 仍然输出布局
    Step11=WAIT + 无结构 → 输出"等待[具体信号]触发"
```

## 🏛️ 数据源最高宪法（2026-10-18 苏摩111封印）

**所有合约分析数据必须来源于U本位永续合约专属API（fapi.binance.com）**

### 强制端点
| 数据 | 端点 |
|------|------|
| 价格/统计 | `fapi.binance.com/fapi/v1/ticker/price` |
| K线 | `fapi.binance.com/fapi/v1/klines` |
| 标记价/FR | `fapi.binance.com/fapi/v1/premiumIndex` |
| FR历史 | `fapi.binance.com/fapi/v1/fundingRate` |
| OI当前 | `fapi.binance.com/fapi/v1/openInterest` |
| OI历史 | `fapi.binance.com/futures/data/openInterestHist` |
| 全局LSR | `fapi.binance.com/futures/data/globalLongShortAccountRatio` |
| 大户账户LSR | `fapi.binance.com/futures/data/topLongShortAccountRatio` |
| 大户仓位LSR | `fapi.binance.com/futures/data/topLongShortPositionRatio` |
| Taker比 | `fapi.binance.com/futures/data/takerlongshortRatio` |
| 深度盘口 | `fapi.binance.com/fapi/v1/depth` |

### 禁止
- 使用 `api.binance.com/api/v3` 获取合约分析数据（仅允许基差对比）
- 使用非fapi端点的OI/LSR/FR/Taker数据
- 用现货价格替代合约标记价做分析

### 数据优先级
1. U本位fapi API ← 唯一合法主数据源
2. 梵天state缓存（GEX/清算/Hurst/方仓）← 辅助补充
3. 现货api.binance.com ← 仅限基差计算

违反此宪法的分析输出作废，必须重新拉取。
