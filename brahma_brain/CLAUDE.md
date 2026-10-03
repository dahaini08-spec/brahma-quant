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
