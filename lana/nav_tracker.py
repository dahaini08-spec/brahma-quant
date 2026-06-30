"""
nav_tracker.py — 梵天 NAV 曲线追踪官 v1.0
每次 brahma_cron 运行时追加一条 NAV 快照
支持：时序查询、回撤统计、周期汇总

数据文件: data/nav_history.jsonl（每行一个快照）
"""
import json, time
from pathlib import Path
from typing import Optional, List, Dict

ROOT      = Path(__file__).parent.parent
NAV_F     = ROOT / "data" / "nav_history.jsonl"
STATE_F   = ROOT / "data" / "brahma_state.json"


# ── 写入快照 ──────────────────────────────────────────────────
def record_nav(nav: float, positions: int = 0, note: str = "") -> dict:
    """追加一条 NAV 快照，由 brahma_cron / brahma_core 调用"""
    snap = {
        "ts":       int(time.time()),
        "nav":      round(nav, 6),
        "positions": positions,
        "note":     note,
    }
    NAV_F.parent.mkdir(parents=True, exist_ok=True)
    with open(NAV_F, "a") as f:
        f.write(json.dumps(snap) + "\n")
    return snap


def record_nav_from_state() -> Optional[dict]:
    """从 brahma_state.json 读 NAV 并追加，供外部脚本调用"""
    try:
        state = json.loads(STATE_F.read_text())
        nav = float(state.get("nav") or 0)
        if nav <= 0:
            return None
        positions = len([p for p in state.get("positions", [])
                        if p.get("status") == "OPEN"])
        return record_nav(nav, positions)
    except Exception:
        return None


# ── 查询 ──────────────────────────────────────────────────────
def load_history(limit: int = 0) -> List[dict]:
    if not NAV_F.exists():
        return []
    lines = NAV_F.read_text().splitlines()
    if limit:
        lines = lines[-limit:]
    records = []
    for l in lines:
        try:
            records.append(json.loads(l))
        except Exception:
            _ = None  # 非致命
    return records


def get_stats(days: int = 7) -> dict:
    """计算最近 N 天的 NAV 统计：起始/最新/最高/最低/回撤/涨跌幅"""
    history = load_history()
    if not history:
        return {}

    cutoff = time.time() - days * 86400
    window = [r for r in history if r["ts"] >= cutoff]
    if not window:
        window = history[-48:]  # 兜底最近48条

    navs = [r["nav"] for r in window]
    start_nav = navs[0]
    cur_nav   = navs[-1]
    peak_nav  = max(navs)
    trough    = min(navs)

    # 最大回撤（从峰值到谷值）
    max_dd = 0.0
    peak   = navs[0]
    for n in navs:
        peak   = max(peak, n)
        dd     = (peak - n) / peak * 100
        max_dd = max(max_dd, dd)

    return {
        "period_days":  days,
        "start_nav":    round(start_nav, 4),
        "current_nav":  round(cur_nav, 4),
        "peak_nav":     round(peak_nav, 4),
        "trough_nav":   round(trough, 4),
        "pnl_pct":      round((cur_nav - start_nav) / start_nav * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "data_points":  len(window),
        "updated_at":   int(time.time()),
    }


def format_nav_report(days: int = 7) -> str:
    s = get_stats(days)
    if not s:
        return "NAV 数据不足"
    sign = "📈" if s["pnl_pct"] >= 0 else "📉"
    return (f"{sign} NAV 曲线 · 最近{days}天\n"
            f"  起始: ${s['start_nav']:.2f}  当前: ${s['current_nav']:.2f}\n"
            f"  涨跌: {s['pnl_pct']:+.2f}%  最大回撤: {s['max_drawdown']:.2f}%\n"
            f"  峰值: ${s['peak_nav']:.2f}  谷值: ${s['trough_nav']:.2f}")


# ── CLI ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--record" in sys.argv:
        snap = record_nav_from_state()
        print(f"✅ NAV 快照记录: {snap}")
    else:
        print(format_nav_report(7))
        print(format_nav_report(30))
