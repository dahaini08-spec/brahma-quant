"""
太医官 · 梵天神经系统 — nerve_core.py
多层诊脉感知，输出统一告警医案
供 brahma-overseer cron 调用，也可独立运行

用法：
  python3 nerve_system/nerve_core.py          # 完整扫描
  python3 nerve_system/nerve_core.py --fast   # 跳过L4 AST扫描（快速模式）
  python3 nerve_system/nerve_core.py --report # 输出最近N条告警历史
"""
import json, time, sys, pathlib
from typing import List, Dict

STATE_FILE = pathlib.Path(__file__).parent.parent / "data" / "brahma_state.json"

ROOT = pathlib.Path(__file__).parent.parent
ALERTS_FILE = ROOT / "data" / "nerve_alerts.jsonl"

# 延迟导入，避免循环
def _run_l0() -> List[Dict]:
    from nerve_system.process_monitor import run
    return run()

def _run_l1() -> List[Dict]:
    from nerve_system.schema_sentinel import run
    return run()

def _run_l2() -> List[Dict]:
    from nerve_system.log_sentinel import run
    return run()

def _run_l3() -> List[Dict]:
    from nerve_system.anomaly_detector import run
    return run()

def _run_l4() -> List[Dict]:
    from nerve_system.ast_scanner import run
    return run()

def _run_l5() -> List[Dict]:
    from nerve_system.freshness_checker import run
    return run()

def _run_l6() -> List[Dict]:
    from nerve_system.reconciler import run
    return run()

def _run_lbus() -> List[Dict]:
    from nerve_system.nerve_bus_reader import run
    return run()


def get_recent_alerts(n: int = 50, min_level: str = "WARN") -> List[Dict]:
    """读取最近N条告警"""
    level_order = {"INFO": 0, "WARN": 1, "ERROR": 2}
    min_ord = level_order.get(min_level, 1)
    try:
        lines = ALERTS_FILE.read_text().strip().split("\n")
        alerts = [json.loads(l) for l in lines if l.strip()]
        # 按级别过滤
        alerts = [a for a in alerts if level_order.get(a.get("level","INFO"), 0) >= min_ord]
        return alerts[-n:]
    except Exception:
        return []


def _write_nerve_state(all_alerts: List[Dict]):
    """把神经系统状态写回 brahma_state.json（读-改-写原子操作）"""
    try:
        lock_path = STATE_FILE.with_suffix(".lock")
        state: Dict = {}
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())

        errors = [a for a in all_alerts if a.get("level") == "ERROR"]
        warns  = [a for a in all_alerts if a.get("level") == "WARN"]

        if errors:
            status = "RED"
        elif warns:
            status = "YELLOW"
        else:
            status = "GREEN"

        top_issues = []
        for a in (errors + warns)[:5]:
            layer = a.get("layer", "")
            issue = a.get("issue", "")
            top_issues.append(f"{layer}: {issue[:60]}")

        state["nerve"] = {
            "last_check": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status":     status,
            "errors":     len(errors),
            "warns":      len(warns),
            "top_issues": top_issues,
        }

        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        tmp.replace(STATE_FILE)
    except Exception:
        pass  # 写失败不影响主流程


def run_nerve(fast: bool = False) -> Dict:
    """运行神经系统，返回摘要"""
    start = time.time()
    all_alerts: List[Dict] = []

    # L0 进程
    try:
        l0 = _run_l0()
        all_alerts.extend(l0)
    except Exception as e:
        all_alerts.append({"layer": "L0_PROCESS", "level": "ERROR", "issue": str(e)})

    # L1 Schema
    try:
        l1 = _run_l1()
        all_alerts.extend(l1)
    except Exception as e:
        all_alerts.append({"layer": "L1_SCHEMA", "level": "ERROR", "issue": str(e)})

    # L2 日志
    try:
        l2 = _run_l2()
        all_alerts.extend(l2)
    except Exception as e:
        all_alerts.append({"layer": "L2_LOG", "level": "ERROR", "issue": str(e)})

    # L3 Behavior
    try:
        l3 = _run_l3()
        all_alerts.extend(l3)
    except Exception as e:
        all_alerts.append({"layer": "L3_BEHAVIOR", "level": "ERROR", "issue": str(e)})

    # L4 AST (慢，按需运行)
    l4_alerts = []
    if not fast:
        try:
            l4_alerts = _run_l4()
            all_alerts.extend(l4_alerts)
        except Exception as e:
            all_alerts.append({"layer": "L4_AST", "level": "ERROR", "issue": str(e)})

    # L5 新鲜度
    try:
        l5 = _run_l5()
        all_alerts.extend(l5)
    except Exception as e:
        all_alerts.append({"layer": "L5_FRESHNESS", "level": "ERROR", "issue": str(e)})

    # L6 对账（fast模式也执行——对账是核心安全检查，不可跳过）
    l6_alerts = []
    try:
        l6_alerts = _run_l6()
        all_alerts.extend(l6_alerts)
    except Exception as e:
        all_alerts.append({"layer": "L6_RECONCILE", "level": "ERROR", "issue": str(e)})

    # L_KEY 凭证哨兵（缓存5分钟，不影响速度）
    try:
        from nerve_system.key_sentinel import run_checks as _key_checks
        key_alerts = _key_checks()
        all_alerts.extend(key_alerts)
    except Exception as e:
        pass  # 凭证检测失败不阻断主流程

    # L_BUS 实时总线反射（末梢上报的实时事件）
    try:
        lbus = _run_lbus()
        all_alerts.extend(lbus)
    except Exception as e:
        all_alerts.append({"layer": "L_BUS", "level": "ERROR", "issue": str(e)})

    elapsed = time.time() - start

    errors = [a for a in all_alerts if a.get("level") == "ERROR"]
    warns  = [a for a in all_alerts if a.get("level") == "WARN"]

    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(elapsed, 2),
        "total": len(all_alerts),
        "errors": len(errors),
        "warns": len(warns),
        "l0_count": len([a for a in all_alerts if a.get("layer","").startswith("L0")]),
        "l1_count": len([a for a in all_alerts if a.get("layer","").startswith("L1")]),
        "l2_count": len([a for a in all_alerts if a.get("layer","").startswith("L2")]),
        "l3_count": len([a for a in all_alerts if a.get("layer","").startswith("L3")]),
        "l4_count": len(l4_alerts),
        "l5_count": len([a for a in all_alerts if a.get("layer","").startswith("L5")]),
        "l6_count": len(l6_alerts),
        "lbus_count": len([a for a in all_alerts if a.get("layer","") == "L_BUS"]),
        "alerts": all_alerts,
    }

    # 写回 brahma_state
    _write_nerve_state(all_alerts)

    return summary


def format_report(summary: Dict) -> str:
    """格式化为可读报告"""
    lines = ["🏮 太医官 · 诊断医案"]
    lines.append(f"时间: {summary['ts']}  耗时: {summary['elapsed_s']}s")
    lines.append(f"告警: {summary['errors']} ERROR / {summary['warns']} WARN / 合计 {summary['total']}")
    lines.append(
        f"L0 进程: {summary.get('l0_count',0)}  "
        f"L1 Schema: {summary.get('l1_count',0)}  "
        f"L2 日志: {summary.get('l2_count',0)}  "
        f"L3 行为: {summary.get('l3_count',0)}  "
        f"L4 代码: {summary.get('l4_count',0)}  "
        f"L5 新鲜: {summary.get('l5_count',0)}  "
        f"L6 对账: {summary.get('l6_count',0)}  "
        f"总线: {summary.get('lbus_count',0)}"
    )

    errors = [a for a in summary.get("alerts", []) if a.get("level") == "ERROR"]
    warns  = [a for a in summary.get("alerts", []) if a.get("level") == "WARN"]

    if errors:
        lines.append("\n🔴 ERROR（需立即关注）:")
        for a in errors[:10]:
            layer = a.get("layer", "")
            key   = (a.get("field") or a.get("behavior") or a.get("pattern")
                     or a.get("process") or a.get("logfile") or a.get("check") or "")
            issue = a.get("issue") or ""
            if not issue and a.get("file"):
                issue = f"{a['file']}:{a.get('line','')} — {a.get('snippet','')[:50]}"
            lines.append(f"  [{layer}] {key}: {issue[:100]}")

    if warns:
        lines.append("\n🟡 WARN（需关注）:")
        for a in warns[:10]:
            layer = a.get("layer", "")
            key   = (a.get("field") or a.get("behavior") or a.get("pattern")
                     or a.get("process") or a.get("logfile") or a.get("check") or "")
            issue = a.get("issue") or ""
            if not issue and a.get("file"):
                issue = f"{a['file']}:{a.get('line','')} — {a.get('snippet','')[:50]}"
            lines.append(f"  [{layer}] {key}: {issue[:100]}")

    if summary["total"] == 0:
        lines.append("\n✅ 系统感知正常，无异常信号")

    return "\n".join(lines)


def format_overseer_line(summary: Dict) -> str:
    """为 overseer 报告提供单行状态摘要"""
    e = summary.get("errors", 0)
    w = summary.get("warns", 0)
    if e > 0:
        return f"🔴 神经告警 {e}E/{w}W"
    elif w > 0:
        return f"🟡 神经告警 {w}W"
    else:
        return f"🟢 神经系统正常"


if __name__ == "__main__":
    fast = "--fast" in sys.argv
    report_mode = "--report" in sys.argv

    if report_mode:
        alerts = get_recent_alerts(n=30)
        print(f"最近 {len(alerts)} 条告警:")
        for a in alerts:
            layer = a.get("layer", "")
            level = a.get("level", "")
            key   = a.get("field") or a.get("behavior") or a.get("pattern") or ""
            issue = a.get("issue", "")
            print(f"  [{level}] {layer} {key}: {issue[:80]}")
    else:
        summary = run_nerve(fast=fast)
        print(format_report(summary))
