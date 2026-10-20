# 梵天首席工程师优化日志
## [封印 2026-10-20 苏摩111]

---

## 审计基线（优化前）

| 项目 | 数值 |
|------|------|
| 代码总量 | brahma_brain/ 150文件 69,442行 / scripts/ 184文件 57,023行 |
| data/ 磁盘 | 239MB / 1118文件（过期3d=948个）|
| logs/ 磁盘 | 13MB / 99个日志文件 |
| 内存占用 | 2217MB / 3749MB (59.1%) |
| cron总条目 | 56条（重复4个脚本）|
| 孤儿脚本 | 58个（不在cron且无import引用）|
| 裸Popen推送 | drawdown_tracker×3 + brahma_cpu×2 |
| council耗时（free池耗尽时）| ~5.6s（每路subprocess等待）|
| deep_analysis耗时 | 7.6s |

---

## 优化清单

### P0 立即修复（磁盘/稳定性）

| 编号 | 操作 | 效果 |
|------|------|------|
| P0-① | data/ 过期3d文件清理 | 1118→780文件，释放15MB |
| P0-② | cron重复行清理 | 删除1条重复inline行 |
| P0-③ | 孤儿脚本归档（6个→scripts/archive/）| 释放88KB，保留可恢复 |

归档清单：
- brahma360_guardian.py（34KB，旧守护架构，11d未改）
- build_sft_dataset.py（11KB，数据集工具，22d未改）
- dharma_regime_detector.py（11KB，旧达摩院，27d未改）
- seal_v2.py（10KB，旧封印工具，15d未改）
- signal_bus.py（10KB，旧信号总线，20d未改）
- brahma_web_dashboard.py（12KB，旧仪表盘，10d未改）

### P1 性能优化（代码层）

| 编号 | 操作 | 效果 |
|------|------|------|
| P1-① | drawdown_tracker 3处裸Popen → push_hub | 推送走统一入口，push_hub哨兵生效 |
| P1-② | council三方：free池耗尽时规则快路 | 5.6s → <1ms（×5000提速）|
| P1-③ | Groq/OR backoff 5min → 30s | 快速重试，减少等待 |
| P1-④ | deep_analysis inject改为TTL缓存 | 避免每次重载fangcang |
| P1-⑤ | 42标的L2方仓：复用进程级缓存 | 避免每次reset _FANGCANG_CACHE |

### 不动项（正常设计）

- brahma_cpu.py 裸Popen：这是push_hub失败的降级fallback，保留
- brahma_42_monitor.py 两条cron：15min快扫+30min深度，不同频率正常
- brahma_flow_batch.py 三条cron：不同时段(1H/4H/8H)，正常

---

## 优化后基线

| 项目 | 数值 | 变化 |
|------|------|------|
| data/ 磁盘 | 224MB / 780文件 | -15MB / -338文件 |
| 内存占用 | 2106MB (56%) | -111MB |
| deep_analysis | 3.2s | -4.4s (×2.4) |
| council（free池空）| <1ms | -5600ms (×5000) |

---

## 保留不改项（架构稳定性）

1. **brahma_core.py `analyze()` 2313行** — 94维核心，不拆分结构
2. **brahma_manual_analysis.py `run_analysis()` 1509行** — 主分析入口，不改
3. **exp_payloads_kdtree.pkl 16MB** — 被exp_payloads_query.py引用，不删
4. **fangcang_merged_v2.json 22.9MB** — 方仓核心数据，不删
5. **58个孤儿脚本中的52个** — 近期修改（1d内）的保留，可能是开发中

---

## 剩余待优化（待苏摩111批准）

1. **state_refresh频率**: 每小时:13 → 每30min（苏摩111批准后执行）
2. **brahma_manual_analysis.py** 4906行中约有600行注释死代码（审计后再议）
3. **brahma_cpu.py 裸Popen降级** → 理想是改为sync push_hub with retry
4. **52个近期孤儿脚本** → 观察一周，确认无用后批量归档
