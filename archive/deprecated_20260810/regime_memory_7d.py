#!/usr/bin/env python3
"""
regime_memory_7d.py
梵天 v5.1 · 中期体制记忆层（KDA三速记忆类比）
苏摩111封印 2026-07-19

功能：
  - 记录过去7天每次体制切换
  - 统计每种体制切换后的胜率
  - 为35维矩阵提供中期记忆加分/减分

数据文件：data/regime_memory_7d.json
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
MEMORY_FILE = BASE / "data" / "regime_memory_7d.json"
RETENTION_DAYS = 7


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text())
        except Exception:
            pass
    return {
        "version": "1.0",
        "updated_at": "",
        "transitions": [],   # 体制切换历史
        "stats": {},         # 体制胜率统计
    }


def save_memory(data: dict):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def record_transition(old_regime: str, new_regime: str, score: float):
    """记录一次体制切换"""
    mem = load_memory()
    now = time.time()

    # 清理7天前的记录
    mem["transitions"] = [
        t for t in mem["transitions"]
        if now - t.get("ts", 0) < RETENTION_DAYS * 86400
    ]

    # 写入新切换
    mem["transitions"].append({
        "ts": now,
        "ts_iso": datetime.now(timezone.utc).isoformat(),
        "old_regime": old_regime,
        "new_regime": new_regime,
        "score": score,
        "result": None,  # 后续由outcome_tracker填写
        "duration_h": None,
    })

    save_memory(mem)
    return len(mem["transitions"])


def record_outcome(regime: str, won: bool, pnl_pct: float):
    """记录一个体制下信号的结果"""
    mem = load_memory()
    stats = mem.get("stats", {})

    if regime not in stats:
        stats[regime] = {"total": 0, "wins": 0, "pnl_sum": 0.0}

    stats[regime]["total"] += 1
    if won:
        stats[regime]["wins"] += 1
    stats[regime]["pnl_sum"] += pnl_pct

    mem["stats"] = stats
    save_memory(mem)


def get_regime_score_adjustment(current_regime: str, direction: str) -> tuple[float, str]:
    """
    根据中期记忆返回score调整值
    返回: (adjustment, reason)
    """
    mem = load_memory()
    stats = mem.get("stats", {})
    transitions = mem.get("transitions", [])

    adjustment = 0.0
    reasons = []

    # ① 当前体制历史胜率
    if current_regime in stats:
        s = stats[current_regime]
        total = s.get("total", 0)
        wins = s.get("wins", 0)
        if total >= 5:
            wr = wins / total
            if wr >= 0.70:
                adjustment += 8
                reasons.append(f"体制历史WR={wr:.0%}(n={total})+8")
            elif wr >= 0.60:
                adjustment += 4
                reasons.append(f"体制历史WR={wr:.0%}(n={total})+4")
            elif wr < 0.45:
                adjustment -= 6
                reasons.append(f"体制历史WR={wr:.0%}(n={total})-6")

    # ② 体制稳定性：7天内切换次数
    now = time.time()
    recent_transitions = [
        t for t in transitions
        if now - t.get("ts", 0) < 7 * 86400
    ]
    switch_count = len(recent_transitions)

    if switch_count <= 1:
        adjustment += 5
        reasons.append(f"体制稳定(7天{switch_count}次切换)+5")
    elif switch_count >= 4:
        adjustment -= 8
        reasons.append(f"体制震荡(7天{switch_count}次切换)-8")

    # ③ 最近切换时间（刚切换的体制不稳定）
    if recent_transitions:
        latest = max(recent_transitions, key=lambda x: x.get("ts", 0))
        hours_since = (now - latest.get("ts", now)) / 3600
        if hours_since < 2:
            adjustment -= 5
            reasons.append(f"体制刚切换{hours_since:.1f}H前-5")
        elif hours_since < 6:
            adjustment -= 2
            reasons.append(f"体制切换{hours_since:.1f}H前-2")

    reason_str = " | ".join(reasons) if reasons else "无中期记忆数据"
    return round(adjustment, 1), reason_str


def get_summary() -> str:
    """返回中期记忆摘要"""
    mem = load_memory()
    stats = mem.get("stats", {})
    transitions = mem.get("transitions", [])

    lines = [f"📊 中期记忆(7天) | 切换记录:{len(transitions)}次"]
    for regime, s in stats.items():
        total = s.get("total", 0)
        wins = s.get("wins", 0)
        wr = wins / total if total > 0 else 0
        lines.append(f"  {regime}: WR={wr:.0%} n={total}")

    return "\n".join(lines)


if __name__ == "__main__":
    # 初始化/查看当前记忆
    print(get_summary())
    adj, reason = get_regime_score_adjustment("BULL_TREND", "LONG")
    print(f"\nBULL_TREND LONG调整: {adj:+.1f} | {reason}")
