"""
nerve_bus_reader.py — 神经总线读取器 / 反射弧
================================================
读取 nerve_bus.jsonl 事件流，执行本地反射（无需AI）：
- 聚合统计：近N分钟各事件频率
- 异常模式：连续失败、爆发性错误
- 写入 nerve_alerts（供 nerve_core 汇总）
- CRITICAL 事件触发立即告警标记（协调官读取）
"""
import json, time, pathlib
from typing import List, Dict
from collections import defaultdict, Counter
from datetime import datetime, timezone

ROOT       = pathlib.Path(__file__).parent.parent
BUS_FILE   = ROOT / "data" / "nerve_bus.jsonl"
ALERTS_FILE= ROOT / "data" / "nerve_alerts.jsonl"

# ── 反射规则 ─────────────────────────────────────────────────────
REFLEX_RULES = [
    # (条件函数, 告警级别, 告警描述)
    # 1. 30分钟内超过3次下单失败
    {
        "name":    "order_fail_burst",
        "window":  30 * 60,
        "events":  {"ORDER_OPEN_FAIL", "ORDER_TP_FAIL", "ORDER_SL_FAIL"},
        "count":   3,
        "level":   "ERROR",
        "issue":   "下单失败爆发: {count}次/{window}min",
    },
    # 2. 任意 CRITICAL 事件
    {
        "name":    "any_critical",
        "window":  60 * 60,
        "level_filter": "CRITICAL",
        "count":   1,
        "level":   "ERROR",
        "issue":   "实时CRITICAL事件: {events}",
    },
    # 3. 1小时内连续亏损超过5笔（排除批量结算：同分钟≥5笔视为信号结算器批量清算，非实盘异常）
    {
        "name":    "loss_streak",
        "window":  60 * 60,
        "events":  {"SIGNAL_CLOSED_LOSS"},
        "count":   8,          # 提高至8笔（批量结算动辄10+笔）
        "level":   "WARN",
        "issue":   "1小时内连续亏损{count}笔",
        "dedup_window_sec": 60,  # 同分钟内集中发生=batch，不告警
    },
    # 4. 对账告警
    {
        "name":    "reconcile_alert",
        "window":  60 * 60,
        "events":  {"GHOST_POSITION", "MISSING_POSITION"},
        "count":   1,
        "level":   "ERROR",
        "issue":   "持仓对账异常: {events}",
    },
]


def _load_recent_events(window_sec: int) -> List[Dict]:
    """读取最近 window_sec 秒内的事件"""
    if not BUS_FILE.exists():
        return []
    cutoff = time.time() - window_sec
    events = []
    try:
        lines = BUS_FILE.read_text().strip().split("\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                if e.get("ts_ms", 0) / 1000 >= cutoff:
                    events.append(e)
            except Exception:
                continue
    except Exception:
        pass
    return events


def _alert(level: str, name: str, issue: str, data: str = "") -> Dict:
    return {
        "ts":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer":  "L_BUS",
        "level":  level,
        "check":  name,
        "issue":  issue,
        "data":   data[:300],
    }


def run() -> List[Dict]:
    """执行反射弧检查，返回告警列表"""
    alerts = []
    max_window = max(r["window"] for r in REFLEX_RULES)
    events = _load_recent_events(max_window)

    if not events:
        return []

    for rule in REFLEX_RULES:
        window  = rule["window"]
        cutoff  = time.time() - window
        # 过滤时间窗口
        windowed = [e for e in events if e.get("ts_ms", 0)/1000 >= cutoff]

        # 按事件类型过滤
        if "events" in rule:
            matched = [e for e in windowed if e.get("event") in rule["events"]]
        elif "level_filter" in rule:
            matched = [e for e in windowed if e.get("level") == rule["level_filter"]]
        else:
            matched = windowed

        if len(matched) >= rule["count"]:
            # 批量结算去重：如果 dedup_window_sec 内发生 >=5 笔，视为批量，跳过
            dedup_sec = rule.get("dedup_window_sec", 0)
            if dedup_sec > 0:
                from collections import Counter
                bucket = Counter(int(e.get("ts_ms",0)/1000/dedup_sec) for e in matched)
                if max(bucket.values(), default=0) >= 5:
                    continue  # 批量结算，不告警

            event_names = list({e.get("event") for e in matched})
            issue = rule["issue"].format(
                count=len(matched),
                window=window // 60,
                events=event_names[:5],
            )
            data_str = json.dumps([{
                "event": e.get("event"), "module": e.get("module"),
                "msg": e.get("msg","")[:60], "ts": e.get("ts")
            } for e in matched[-5:]], ensure_ascii=False)
            alerts.append(_alert(rule["level"], rule["name"], issue, data_str))

    if alerts:
        ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS_FILE, "a") as f:
            for a in alerts:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")

    return alerts


def get_bus_summary(window_min: int = 60) -> Dict:
    """返回神经总线近期摘要，供协调官报告"""
    events = _load_recent_events(window_min * 60)
    by_type  = Counter(e.get("event") for e in events)
    by_level = Counter(e.get("level") for e in events)
    by_mod   = Counter(e.get("module") for e in events)
    return {
        "window_min":  window_min,
        "total":       len(events),
        "by_level":    dict(by_level),
        "top_events":  dict(by_type.most_common(10)),
        "top_modules": dict(by_mod.most_common(5)),
    }


if __name__ == "__main__":
    results = run()
    print(f"[BUS READER] {len(results)} 个反射告警")
    for r in results:
        print(f"  [{r['level']}] {r['check']}: {r['issue']}")
    print()
    summary = get_bus_summary(60)
    print(f"最近60分钟神经总线: {summary['total']} 个事件")
    for ev, cnt in summary["top_events"].items():
        print(f"  {ev}: {cnt}")
