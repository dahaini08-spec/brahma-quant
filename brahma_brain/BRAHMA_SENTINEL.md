# BRAHMA SENTINEL — 主动健康感知系统
<!-- 设计院 v1.0 · 2026-06-11 -->

## 问题根因

今日7个Bug的共同根因：**系统没有主动感知层，只能靠人发现才修补。**

| 根因类型 | 典型案例 | 代价 |
|---------|---------|------|
| 静默失败 | DharmaBridge grade未定义 → except:pass吞掉 | 信号TTL从未写入 |
| 无可观测性 | state-refresh skipped → 33分钟无人知晓 | 体制数据失效 |
| 无频率保护 | 43次API → IP ban 35分钟 | 全系统瘫痪 |
| 无健康契约 | ws_guardian双写不一致 | 状态混乱 |

## 设计原则

**主动 > 被动。探针 > 日志。契约 > 信任。**

系统不应等待人发现问题，应该**自己每10分钟体检一次**，发现异常立刻告警。

---

## 架构：三层哨兵

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Health Contract（健康契约）                      │
│  每个子系统声明自己的"存活证明"指标                           │
│  → sentinel_core.py 的 HEALTH_CONTRACTS 字典              │
├─────────────────────────────────────────────────────────┤
│  Layer 2: Active Probe（主动探针）                         │
│  每10分钟执行全量体检                                        │
│  → sentinel_runner.py，注册为 brahma-sentinel-10m         │
├─────────────────────────────────────────────────────────┤
│  Layer 3: Escalation（升级机制）                           │
│  WARN → 记录；ERROR → 立即推送Jarvis；CRITICAL → 自愈+推送  │
│  → scripts/sentinel_alert.py                            │
└─────────────────────────────────────────────────────────┘
```

---

## Layer 1: 健康契约定义

```python
HEALTH_CONTRACTS = {
    # 数据文件新鲜度契约
    "brahma_state":    {"file": "data/brahma_state.json",      "max_age_min": 8,   "level": "CRITICAL"},
    "ws_guardian":     {"file": "data/ws_guardian_state.json", "max_age_min": 3,   "level": "CRITICAL"},
    "signal_queue":    {"file": "data/signal_queue.jsonl",     "max_age_min": 60,  "level": "WARN"},
    "soma_quota":      {"file": "data/soma_state.json",        "max_age_min": 60,  "level": "WARN"},

    # 进程存活契约
    "ws_guardian_proc": {"process": "ws_guardian.py",         "level": "CRITICAL"},
    "watchdog_proc":    {"process": "watchdog_guardian",      "level": "ERROR"},

    # API健康契约（每小时检测一次，不消耗配额）
    "binance_fapi":    {"url": "https://fapi.binance.com/fapi/v1/ping", "timeout": 5, "level": "CRITICAL"},

    # 配额预警契约
    "soma_quota_pct":  {"type": "quota", "warn_pct": 70, "critical_pct": 90, "level": "ERROR"},

    # 信号链路健康契约
    "signal_ttl":      {"type": "signal_ttl", "max_no_signal_hours": 6, "level": "WARN"},
    "dharma_bridge":   {"type": "code_probe", "target": "dharma_data_bridge", "level": "ERROR"},
}
```

---

## Layer 2: 主动探针

每10分钟运行一次，**无AI消耗**（纯Python），只在发现问题时触发AI告警。

```
探针检查项（10分钟一次）:
  ✓ 数据文件新鲜度（5个文件）
  ✓ 进程存活（ws_guardian + watchdog）
  ✓ API连通性（fapi ping，每小时一次，有缓存）
  ✓ 配额使用率
  ✓ 信号队列健康
  ✓ brahma_state.regime 合理性

探针输出:
  全部正常 → 写入 data/sentinel_status.json，不发消息
  任意异常 → 分级处理
```

---

## Layer 3: 升级机制

| 级别 | 触发条件 | 处理方式 |
|------|---------|---------|
| WARN | 数据稍旧、配额>70% | 写日志，不发消息 |
| ERROR | 进程挂/API不通/配额>90% | 发Jarvis告警（中文，一行摘要） |
| CRITICAL | state文件>8min/ws_guardian宕机 | 立即自愈尝试+发告警 |

**自愈动作库:**
- `heal_state_refresh()` → 直接运行 brahma_state_refresh.py
- `heal_ws_guardian()` → pgrep+重启 ws_guardian.py
- `heal_watchdog()` → 重启 watchdog_guardian.sh
- `heal_api_rate_limit()` → 暂停 full-cycle-scan，发告警

---

## 与现有系统关系

```
现有: brahma-self-heal-30m  → 被动，30分钟才检查，AI消耗
新增: brahma-sentinel-10m  → 主动，10分钟体检，零AI（只探针）
                              → 发现问题才消耗AI告警

现有 brahma360_guardian     → 留存，作为第二道防线
Sentinel                    → 第一道防线，更快、更全面
```

---

## 文件结构

```
scripts/
  sentinel_core.py      # 健康契约定义 + 探针引擎
  sentinel_runner.py    # Cron入口，每10分钟调用
  sentinel_alert.py     # 告警格式化+推送

data/
  sentinel_status.json  # 最新体检结果（机器可读）
  sentinel_history.jsonl # 历史告警记录
```
