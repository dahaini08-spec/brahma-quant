"""
regime_health_guard.py
[P2修复 2026-07-15 设计院×梵天×达摩院封印]

职责：将信号结算结果(WIN/LOSS)回写至 data/regime_health_outcomes.jsonl
      让体制系统能够真正学习胜率，形成自我进化能力。

根因：upgrade_v2 目录不存在 → import 静默失败 → BULL_TREND outcome全为null
修复：创建该模块，实现 record_outcome() 正确写入 + get_regime_stats() 统计读取
"""

import json
import time
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUTCOMES_FILE = BASE / "data" / "regime_health_outcomes.jsonl"

WIN_OUTCOMES  = {"WIN", "win", "profit", "TP1", "TP2"}
LOSS_OUTCOMES = {"LOSS", "loss", "SL", "STOP"}


def record_outcome(symbol: str, regime: str, direction: str, outcome: str,
                   pnl_pct: float = 0.0, score: int = 0) -> bool:
    """
    记录一笔信号的结算结果到 regime_health_outcomes.jsonl

    Args:
        symbol:    标的 e.g. "BTCUSDT"
        regime:    体制 e.g. "BULL_TREND"
        direction: 方向 e.g. "LONG" / "SHORT"
        outcome:   结果 e.g. "WIN" / "LOSS"
        pnl_pct:   盈亏百分比 e.g. 1.23
        score:     信号评分

    Returns:
        True if written successfully
    """
    if not regime or not outcome:
        return False

    # 标准化 outcome
    if outcome in WIN_OUTCOMES:
        normalized = "WIN"
    elif outcome in LOSS_OUTCOMES:
        normalized = "LOSS"
    else:
        normalized = outcome  # TIMEOUT / MISS_WIN / MISS_LOSS 等保留原值

    record = {
        "ts":        time.time(),
        "ts_str":    time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "symbol":    symbol,
        "regime":    regime,
        "direction": direction,
        "outcome":   normalized,
        "pnl_pct":   round(pnl_pct, 4),
        "score":     score,
    }

    try:
        os.makedirs(OUTCOMES_FILE.parent, exist_ok=True)
        with open(OUTCOMES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print(f"[regime_health_guard] 写入失败: {e}")
        return False


def get_regime_stats(min_samples: int = 5) -> dict:
    """
    读取并统计各体制下的胜率

    Returns:
        {
          "BULL_TREND":  {"total": 45, "WIN": 32, "LOSS": 13, "WR": 71.1},
          "BEAR_TREND":  {"total": 30, "WIN": 28, "LOSS":  2, "WR": 93.3},
          ...
        }
    """
    stats: dict = {}

    if not OUTCOMES_FILE.exists():
        return stats

    try:
        with open(OUTCOMES_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return stats

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        regime  = rec.get("regime", "UNKNOWN")
        outcome = rec.get("outcome", "")

        if regime not in stats:
            stats[regime] = {"total": 0, "WIN": 0, "LOSS": 0, "WR": 0.0}

        stats[regime]["total"] += 1
        if outcome == "WIN":
            stats[regime]["WIN"] += 1
        elif outcome == "LOSS":
            stats[regime]["LOSS"] += 1

    # 计算胜率
    for regime, s in stats.items():
        tot = s["total"]
        win = s["WIN"]
        s["WR"] = round(win / tot * 100, 1) if tot >= min_samples else 0.0

    return stats


def get_regime_wr(regime: str, direction: str = None, min_samples: int = 5) -> float:
    """
    快速获取某体制（可选方向）的胜率

    Returns:
        float 胜率(0~100)，样本不足返回 0.0
    """
    if not OUTCOMES_FILE.exists():
        return 0.0

    wins = 0
    total = 0

    try:
        with open(OUTCOMES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("regime") != regime:
                    continue
                if direction and rec.get("direction") != direction:
                    continue
                total += 1
                if rec.get("outcome") == "WIN":
                    wins += 1
    except Exception:
        return 0.0

    if total < min_samples:
        return 0.0
    return round(wins / total * 100, 1)


if __name__ == "__main__":
    # 自测
    print("=== regime_health_guard 自测 ===")
    ok = record_outcome("BTCUSDT", "BULL_TREND", "LONG", "WIN", pnl_pct=1.5, score=162)
    print(f"写入测试: {'✅' if ok else '❌'}")
    stats = get_regime_stats()
    for r, s in stats.items():
        print(f"  {r}: total={s['total']} WIN={s['WIN']} LOSS={s['LOSS']} WR={s['WR']}%")
