"""
L5 Freshness Checker — 数据新鲜度检查
检查关键数据文件和字段的时间戳，超过阈值则告警
"""
import json, time, pathlib
from typing import List, Dict, Optional, Any

ROOT = pathlib.Path(__file__).parent.parent
ALERTS_FILE = ROOT / "data" / "nerve_alerts.jsonl"
BRAHMA_STATE = ROOT / "data" / "brahma_state.json"

FRESHNESS_RULES = [
    {"name": "brahma_state",  "file": "data/brahma_state.json",       "max_age_min": 30,  "level": "ERROR"},
    {"name": "binance_sync",  "field_path": "last_binance_sync",       "max_age_min": 60,  "level": "WARN"},
    # ws_guardian 直接读源文件，避免 brahma_state 合并延迟导致误报
    {"name": "ws_guardian",   "file": "data/ws_guardian_state.json",    "max_age_min": 15,  "level": "ERROR"},
    {"name": "scan_active",   "field_path": "last_scan_ts",            "max_age_min": 120, "level": "WARN"},  # [v13.1] 主策略非固定频率
    {"name": "funnel_log",    "file": "data/funnel_log.jsonl",         "max_age_min": 180, "level": "WARN"},
    # 设计院新增（2026-05-17）
    {"name": "nav_history",   "file": "data/nav_history.jsonl",        "max_age_min": 200, "level": "WARN"},
    {"name": "attribution",   "file": "data/attribution_log.jsonl",    "max_age_min": 1440,"level": "WARN"},
    # [v13.1 UP-021] 达摩院训练数据新鲜度
    {"name": "dharma_data",   "file": "dharma/data/live_signals.jsonl", "max_age_min": 1440,"level": "WARN"},
]


def _alert(level: str, check: str, issue: str, data: str = "") -> Dict:
    return {
        "ts":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer": "L5_FRESHNESS",
        "level": level,
        "check": check,
        "issue": issue,
        "data":  data,
    }


def _append_alerts(alerts: List[Dict]):
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_FILE, "a") as f:
        for a in alerts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def _get_nested(obj: Any, field_path: str) -> Optional[Any]:
    """通过点分路径取嵌套字段值，如 'ws_guardian.last_ping'"""
    parts = field_path.split(".")
    cur = obj
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _parse_ts(value: Any) -> Optional[float]:
    """尝试把各种时间戳格式转为 Unix epoch float"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # 毫秒 vs 秒：>1e12 认为是毫秒
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    if isinstance(value, str):
        import datetime
        # Try fromisoformat first (handles +00:00, microseconds, etc.)
        try:
            dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
        except (ValueError, AttributeError):
            pass
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.datetime.strptime(value, fmt)
                return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
            except ValueError:
                continue
    return None


def run() -> List[Dict]:
    alerts = []
    now = time.time()

    # 加载 brahma_state（field_path 规则需要它）
    state: Dict = {}
    try:
        state = json.loads(BRAHMA_STATE.read_text())
    except Exception:
        pass  # 若读不到，field_path 规则会各自返回缺失

    for rule in FRESHNESS_RULES:
        name      = rule["name"]
        max_age   = rule["max_age_min"] * 60
        level     = rule["level"]
        file_path = rule.get("file")
        field_path = rule.get("field_path")

        try:
            ts: Optional[float] = None
            raw_val = ""

            if file_path:
                # 检查文件 mtime
                p = ROOT / file_path
                if not p.exists():
                    alerts.append(_alert("WARN", name, f"文件不存在: {file_path}", ""))
                    continue
                ts = p.stat().st_mtime
                raw_val = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

            elif field_path:
                raw_val = _get_nested(state, field_path)
                if raw_val is None:
                    alerts.append(_alert("WARN", name, f"字段不存在: {field_path}", ""))
                    continue
                ts = _parse_ts(raw_val)
                if ts is None:
                    alerts.append(_alert("WARN", name, f"时间戳格式无法解析: {raw_val}", str(raw_val)))
                    continue

            if ts is None:
                continue

            age_s = now - ts
            age_min = age_s / 60.0
            if age_s > max_age:
                issue = f"数据过期 {age_min:.1f}min（阈值{rule['max_age_min']}min）"
                alerts.append(_alert(level, name, issue, f"{field_path or file_path}={raw_val}"))

        except Exception as e:
            alerts.append(_alert("WARN", name, f"检查异常: {e}", ""))

    if alerts:
        _append_alerts(alerts)
    return alerts


if __name__ == "__main__":
    results = run()
    if results:
        print(f"[FRESHNESS CHECKER] {len(results)} 个告警:")
        for r in results:
            print(f"  [{r['level']}] {r['check']}: {r['issue']}")
    else:
        print("[FRESHNESS CHECKER] 数据新鲜度检查通过 ✓")
