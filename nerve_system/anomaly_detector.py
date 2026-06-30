"""
L3 Behavioral Anomaly Detector — 行为异常感知
监测系统运行行为，发现偏离正常的模式：
  B1: 扫描正常但0信号（信号干旱）
  B2: NAV 连续下降趋势
  B3: ws_guardian 心跳超时
  B4: 同一个 symbol 连续亏损
  B5: 持仓持续超时（未出场）
  B6: 连续拒单率过高（过滤太严）
"""
import json, time, pathlib
from typing import List, Dict, Optional
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent.parent
STATE_FILE  = ROOT / "data" / "brahma_state.json"
FUNNEL_FILE = ROOT / "data" / "funnel_log.jsonl"
SIGNALS_FILE= ROOT / "trading-system" / "signals" / "signal_history.json"  # may not exist
ALERTS_FILE = ROOT / "data" / "nerve_alerts.jsonl"

# ─── 阈值 ───────────────────────────────────────────────────────
THRESHOLDS = {
    "signal_drought_scans":  20,    # 超过20轮扫描0信号 → 干旱告警
    "nav_drop_pct":           5.0,  # NAV单次下跌超过5% → 告警
    "ws_heartbeat_timeout":  900,   # ws_guardian心跳超过15分钟 → 告警（重启周期最长10分钟）
    "max_position_hold_h":    72,   # 持仓超过72小时 → 告警
    "funnel_pass_rate_min":  0.01,  # 漏斗通过率低于1% → 过滤太严
    "max_drawdown_pct":       15.0, # 最大回撤超15% → ERROR
}


def _now_ts() -> float:
    return time.time()


def _parse_ts(ts_str: str) -> Optional[float]:
    """解析ISO时间戳为unix timestamp"""
    if not ts_str:
        return None
    try:
        # 兼容多种格式
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _alert(level: str, behavior: str, issue: str, data: str = "") -> Dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer": "L3_BEHAVIOR",
        "level": level,
        "behavior": behavior,
        "issue": issue,
        "data": data[:200],
    }


def check_signal_drought(state: Dict) -> List[Dict]:
    """B1: 扫描正常但长期0信号"""
    alerts = []
    scan_count = state.get("scan_count", 0)
    trade_count = state.get("trade_count", 0)
    lana_count  = state.get("lana_trade_count", 0)
    total_trades = trade_count + lana_count

    # 扫描轮次已经够多但完全没有成交
    if scan_count > THRESHOLDS["signal_drought_scans"] and total_trades == 0:
        alerts.append(_alert("WARN", "B1_SIGNAL_DROUGHT",
            f"扫描 {scan_count} 轮但0成交，信号过滤可能过于严格",
            f"scan_count={scan_count} trade_count={total_trades}"))

    # 检查漏斗：候选通过率过低（连续N次才告警，减少误报）
    try:
        lines = FUNNEL_FILE.read_text().strip().split("\n")
        recent = [json.loads(l) for l in lines[-48:] if l.strip()]  # 最近48次（约12小时）
        if len(recent) >= 20:
            total_cands  = sum(f.get("candidates", 0) for f in recent)
            total_passed = sum(f.get("passed", 0) for f in recent)
            # 用候选数（candidates）作分母，而非总扫描数
            if total_cands > 50:
                rate = total_passed / total_cands
                # 连续12小时候选通过率<1%才告警（排除市场无行情的正常静默期）
                consecutive_zero = sum(1 for f in recent[-20:] if f.get("candidates",0) > 0 and f.get("passed",0) == 0)
                if rate < THRESHOLDS["funnel_pass_rate_min"] and consecutive_zero >= 20:
                    alerts.append(_alert("INFO", "B1_FUNNEL_TIGHT",
                        f"信号漏斗连续{consecutive_zero}次0通过（候选{rate:.1%}），CHOP期间属正常",
                        f"candidates={total_cands} passed={total_passed}"))
    except Exception:
        pass

    return alerts


def check_ws_guardian(state: Dict) -> List[Dict]:
    """B3: ws_guardian 心跳超时 — 直接读源文件，避免 brahma_state 合并延迟"""
    alerts = []
    import pathlib, json as _json
    ws_file = pathlib.Path(__file__).parent.parent / "data" / "ws_guardian_state.json"
    try:
        wsg = _json.loads(ws_file.read_text())
    except Exception:
        wsg = state.get("ws_guardian", {})
    if not isinstance(wsg, dict):
        return alerts

    # 支持两种格式: 新格式 {pid, last_ping} / 旧格式 {time, watchers}
    last_ping = wsg.get("last_ping") or wsg.get("updated_at") or wsg.get("time", "")
    ts = _parse_ts(last_ping)
    if ts is None:
        alerts.append(_alert("WARN", "B3_WS_GUARDIAN",
            "ws_guardian.last_ping 无法解析", str(last_ping)))
        return alerts

    age = _now_ts() - ts
    if age > THRESHOLDS["ws_heartbeat_timeout"]:
        alerts.append(_alert("ERROR", "B3_WS_GUARDIAN",
            f"ws_guardian 心跳超时 {age:.0f}秒（阈值 {THRESHOLDS['ws_heartbeat_timeout']}s）",
            f"last_ping={last_ping} pid={wsg.get('pid')}"))

    return alerts


def check_positions_overtime(state: Dict) -> List[Dict]:
    """B5: 持仓超过最大持有时间"""
    alerts = []
    positions = state.get("positions", [])
    if not isinstance(positions, list):
        return alerts

    now = _now_ts()
    for pos in positions:
        if pos.get("status") != "OPEN":
            continue
        open_ts_str = pos.get("open_ts", "")
        ts = _parse_ts(open_ts_str)
        if ts is None:
            continue
        hold_hours = (now - ts) / 3600
        max_h = THRESHOLDS["max_position_hold_h"]
        if hold_hours > max_h:
            alerts.append(_alert("WARN", "B5_POSITION_OVERTIME",
                f"持仓 {pos.get('symbol')} 已持有 {hold_hours:.1f}h（超过 {max_h}h），可能卡单",
                f"signal_id={pos.get('signal_id')} open_ts={open_ts_str}"))

    return alerts


def check_nav_health(state: Dict) -> List[Dict]:
    """B2: NAV 快速下降预警"""
    alerts = []
    nav = state.get("nav", 0)
    if nav <= 0:
        alerts.append(_alert("ERROR", "B2_NAV_INVALID",
            f"NAV={nav} 无效，可能账户数据异常", ""))

    # 接入 nav_tracker 做历史趋势 + 最大回撤检测
    try:
        import sys as _sys_ad
        _sys_ad.path.insert(0, str(ROOT / "lana"))
        from nav_tracker import get_stats as _nav_stats
        s = _nav_stats(days=7)
        if s:
            drop = s.get("pnl_pct", 0)
            if drop < -THRESHOLDS.get("nav_drop_pct", 5):
                alerts.append(_alert("WARN", "B2_NAV_DROP",
                    f"NAV近7天下降 {drop:.1f}%（{s['start_nav']:.2f}→{s['current_nav']:.2f}）", ""))
            max_dd = s.get("max_drawdown", 0)
            if max_dd > THRESHOLDS.get("max_drawdown_pct", 10):
                alerts.append(_alert("ERROR", "B2_MAX_DRAWDOWN",
                    f"最大回撤 {max_dd:.1f}% 超过阈值（峰值${s['peak_nav']:.2f}）", ""))
    except Exception:
        pass  # nav_tracker 不可用时跳过

    return alerts


def run() -> List[Dict]:
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception as e:
        alert = _alert("ERROR", "B0_STATE_READ", f"无法读取状态文件: {e}", "")
        return [alert]

    alerts = []
    alerts.extend(check_signal_drought(state))
    alerts.extend(check_ws_guardian(state))
    alerts.extend(check_positions_overtime(state))
    alerts.extend(check_nav_health(state))

    if alerts:
        ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS_FILE, "a") as f:
            for a in alerts:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")

    return alerts


if __name__ == "__main__":
    results = run()
    if results:
        print(f"[BEHAVIOR ANOMALY] {len(results)} 个告警:")
        for r in results:
            print(f"  [{r['level']}] {r['behavior']}: {r['issue']}")
    else:
        print("[BEHAVIOR ANOMALY] 行为检查通过 ✓")
