"""
L2 Log Sentinel — 日志错误哨兵
扫描关键日志文件最近100行，查找 ERROR / Exception / Traceback
发现问题追加到 nerve_alerts.jsonl
"""
import json, re, time, pathlib
from typing import List, Dict

ROOT = pathlib.Path(__file__).parent.parent
ALERTS_FILE = ROOT / "data" / "nerve_alerts.jsonl"

WATCHED_LOGS = [
    "logs/ws_guardian.log",
    "logs/ws_guardian_watchdog.log",
]

ERROR_PATTERNS = re.compile(r"(ERROR|Exception|Traceback)", re.IGNORECASE)


def _alert(level: str, logfile: str, issue: str, data: str = "") -> Dict:
    return {
        "ts":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer":   "L2_LOG",
        "level":   level,
        "logfile": logfile,
        "issue":   issue,
        "data":    data,
    }


def _append_alerts(alerts: List[Dict]):
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_FILE, "a") as f:
        for a in alerts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def _scan_log(logfile: str) -> List[Dict]:
    path = ROOT / logfile
    alerts = []
    if not path.exists():
        return []  # 文件不存在不告警，可能服务未启动

    try:
        lines = path.read_text(errors="replace").splitlines()
        recent = lines[-100:]  # 最近100行
        matches = [l for l in recent if ERROR_PATTERNS.search(l)]
        if matches:
            last_match = matches[-1][:200]
            issue = f"发现{len(matches)}个ERROR/Exception"
            alerts.append(_alert("WARN", logfile, issue, last_match))
    except Exception as e:
        alerts.append(_alert("WARN", logfile, f"读取日志异常: {e}", ""))
    return alerts


def run() -> List[Dict]:
    alerts = []
    for logfile in WATCHED_LOGS:
        try:
            alerts.extend(_scan_log(logfile))
        except Exception as e:
            alerts.append(_alert("WARN", logfile, f"扫描异常: {e}", ""))
    if alerts:
        _append_alerts(alerts)
    return alerts


if __name__ == "__main__":
    results = run()
    if results:
        print(f"[LOG SENTINEL] {len(results)} 个告警:")
        for r in results:
            print(f"  [{r['level']}] {r['logfile']}: {r['issue']}")
    else:
        print("[LOG SENTINEL] 日志扫描正常，无错误 ✓")
