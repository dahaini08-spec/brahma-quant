"""
L0 Process Monitor — 进程存活监控
检查关键进程是否运行，挂了则告警并追加到 nerve_alerts.jsonl

[v13.1 UP-021] 修复：
  原实现 pgrep -f 在 sandbox isolated 子代理中看不到 host 进程，导致误报。
  新实现：优先读心跳文件（ws_guardian_state.json），心跳>5分钟才告警。
  pgrep 作为备用方案，不是主要判断核心。
"""
import json, subprocess, time, pathlib, datetime
from typing import List, Dict

ROOT = pathlib.Path(__file__).parent.parent
ALERTS_FILE = ROOT / "data" / "nerve_alerts.jsonl"

# 守护进程配置
# heartbeat_file: 第一优先，读心跳文件判断
# heartbeat_max_age_s: 心跳超过此秒数才告警（防止cron短暂空窗误报）
# pattern: pgrep 备用方案
WATCHED_PROCESSES = [
    {
        "name":               "ws_guardian",
        "pattern":            "ws_guardian.py",
        "critical":           True,
        "heartbeat_file":     "data/ws_guardian_state.json",
        "heartbeat_ts_field": "last_ping",
        "heartbeat_max_age_s": 300,   # 心跳>5分钟才告警
        "pid_field":          "pid",
    },
]


def _alert(level: str, process: str, issue: str, data: str = "") -> Dict:
    return {
        "ts":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer":   "L0_PROCESS",
        "level":   level,
        "process": process,
        "issue":   issue,
        "data":    data,
    }


def _check_by_heartbeat(proc: dict) -> tuple:
    """通过心跳文件判断进程是否存活
    返回 (alive: bool, age_s: int)
    """
    hb_file  = ROOT / proc.get("heartbeat_file", "")
    ts_field = proc.get("heartbeat_ts_field", "last_ping")
    max_age  = proc.get("heartbeat_max_age_s", 300)

    try:
        state = json.loads(hb_file.read_text())
        ts_raw = state.get(ts_field, "")
        if not ts_raw or len(str(ts_raw)) < 10:
            return False, 99999
        t = datetime.datetime.fromisoformat(str(ts_raw))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        age_s = int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds())
        return age_s <= max_age, age_s
    except Exception:
        return False, 99999


def _check_by_pgrep(pattern: str) -> bool:
    """备用：通过pgrep判断（sandbox环境可能看不到host进程，结果不可靠）"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _append_alerts(alerts: List[Dict]):
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_FILE, "a") as f:
        for a in alerts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def run() -> List[Dict]:
    alerts = []
    for proc in WATCHED_PROCESSES:
        try:
            # 心跳文件优先：sandbox/host 均可靠
            if proc.get("heartbeat_file"):
                alive, age_s = _check_by_heartbeat(proc)
                detail = (f"心跳{age_s}s前（阈值{proc['heartbeat_max_age_s']}s）"
                          if age_s < 99999 else "心跳文件读取失败")
            else:
                # 备用 pgrep（仅局域 host 直接运行有效）
                alive = _check_by_pgrep(proc["pattern"])
                age_s = 0
                detail = proc["pattern"]

            if not alive:
                level = "ERROR" if proc["critical"] else "WARN"
                alerts.append(_alert(level, proc["name"],
                    f"进程未运行 — {detail}", proc["pattern"]))
        except Exception as e:
            alerts.append(_alert("WARN", proc["name"], f"检查进程时异常: {e}", ""))
    if alerts:
        _append_alerts(alerts)
    return alerts


if __name__ == "__main__":
    results = run()
    if results:
        print(f"[PROCESS MONITOR] {len(results)} 个告警:")
        for r in results:
            print(f"  [{r['level']}] {r['process']}: {r['issue']}")
    else:
        print("[PROCESS MONITOR] 所有进程运行正常 ✓")
