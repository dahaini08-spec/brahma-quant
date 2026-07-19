# BRAHMA_GUARD.md — 梵天防腐守护宪章
<!-- 设计院 · 苏摩111 · 2026-07-18 封印 -->

## 核心原则：防腐优于修复

> 系统出问题不可怕，可怕的是同一个问题出现两次。
> 每个故障都必须转化为一个检测探针，永久留在系统中。

---

## 五大防腐层

### L1 安全基线层（C1探针）
**保护目标：** 关键常量不被意外修改

| 常量 | 安全值 | 范围 | 文件 |
|------|--------|------|------|
| TIER_1_SCORE | 155 | [145,175] | auto_executor.py |
| TIER_2_SCORE | 138 | [120,154] | auto_executor.py |
| TIER_3_SCORE | 120 | [110,137] | auto_executor.py |
| OI_SCORE_THRESHOLD | 60 | [50,100] | sub_executor.py |
| MAX_POSITIONS | 20 | [10,25] | auto_executor.py |
| MIN_NOTIONAL | 4.5 | [3.0,10.0] | auto_executor.py |
| MIN_SL_PCT | 1.0 | [0.5,2.5] | auto_executor.py |
| MAX_SL_PCT | 5.0 | [3.0,10.0] | auto_executor.py |

**规则：** 任何修改必须通过六方审核 + 苏摩111批准 → git commit封印

---

### L2 数据管道层（C2探针）
**保护目标：** 信号字段完整性

| 字段 | 检测方式 | 报警条件 |
|------|---------|---------|
| timing_badge | 代码层：brahma_engine.py中注入逻辑存在 | 注入逻辑被删除 |
| fill_qty | 代码层：sub_executor.py回查逻辑存在 | sleep+GET回查+fallback任一缺失 |
| valid | 数据层：24H信号valid字段覆盖率 | <70%覆盖 |

**铁律：** 每次 brahma_engine.py 修改后必须运行完整性检查

---

### L3 路径对齐层（C3探针）
**保护目标：** scanner输出路径与executor读取路径一致

| 系统 | scanner写入 | executor读取 |
|------|------------|-------------|
| 暴涨猎手 | dharma/pump_hunter/new_alerts.json | 同上 |
| OI猎手 | data/oi_candidates.json | 同上 |
| 主系统 | data/live_signal_log.jsonl | 同上 |

**铁律：** 任何路径变更必须同时更新 scanner 和 executor 两端

---

### L4 cron稳定层（C4探针）
**保护目标：** 防止gateway频繁重启

| 指标 | 安全阈值 | 当前值 |
|------|---------|-------|
| cron总数 | ≤35 | 34 ✅ |
| 高频cron(≤10m)lightContext | 100% | 100% ✅ |
| 5min窗口并发 | <4 | 5 ⚠️ |

**铁律：** 新增cron前必须评估：① 是否真的需要AI？② 能否寄生现有cron？③ 新增后cron总数是否≤35？

---

### L5 执行安全层（C5/C6探针）
**保护目标：** 防止PIXEL重复建仓、止损噪音、日志污染

| 检测项 | 条件 |
|--------|------|
| 单标的敞口上限 | _max_exposure = NAV×10% 逻辑存在 |
| 止损噪音 | sl_pct ≥ 0.5%（<0.5%=噪音级，ATR×小乘数根因） |
| performance_logger去重 | _dedup_key逻辑存在 |
| wuqu_positions | fill_qty=0脏数据检测 |

---

## 运行方式

```bash
# 日常检查
python3 scripts/brahma_integrity_check.py

# JSON格式（供程序消费）
python3 scripts/brahma_integrity_check.py --json

# 退出码：0=HEALTHY, 1=有ERROR
```

## 触发时机

| 场景 | 是否需要运行完整性检查 |
|------|---------------------|
| gateway重启后 | ✅ 必须 |
| 修改任何 `scripts/` 文件后 | ✅ 必须 |
| 修改 `cron/jobs.json` 后 | ✅ 必须 |
| 部署新功能前 | ✅ 必须 |
| 每日360日报 | ✅ 已集成 |
| 苏摩询问系统状态时 | ✅ 优先运行 |

---

## 历史故障 → 探针映射

| 故障 | 根因 | 探针 |
|------|------|------|
| auto_executor 3天0执行 | AUTO_ENTER_FULL=155永不触发 | C1.TIER_1/2/3_SCORE |
| pump_signal_executor 7日0执行 | __main__无参数打印usage退出 | C3.pump_entry |
| fill_qty全量=0 | MARKET单未回查executedQty | C2.fill_qty |
| timing_badge全量为空 | brahma_engine未注入timing | C2.timing_badge |
| new_alerts.json信号从未执行 | 路径不对齐 | C3.暴涨猎手信号路径 |
| gateway重启19次/11h | fullContext cron并发风暴 | C4.lightContext + C4.cron_count |
| PIXEL重复建仓 | 无单标的敞口上限 | C5.exposure_cap |
| 止损=0.3%噪音 | ATR×小乘数 | C5.sl_calculation |
| performance_log重复写入 | log_trade无dedup | C6.perf_dedup |

---

*本文件由设计院六方联合制定，苏摩111批准，2026-07-18封印。*
*修改本文件需走六方审核流程。*
