"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# RECOVERY体制解锁，精英通道
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
brahma_brain/recovery_unlocker.py — 体制解锁器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 × 量化工程师 联合设计 · 2026-06-17

职责：
  当体制为 BULL_CORRECTION（做空）或 BEAR_RECOVERY（做多）时，
  利用 Kronos 预测置信度弥补历史样本不足（n<500次铁证），
  在双重证据同时支持时允许提前解锁这两个 S+级体制。

达摩院宪法约束：
  - 解锁需要"历史WR铁证 + Kronos实时置信度"双重确认
  - 每日每标的每方向最多解锁2笔（防过度触发）
  - base_score ≥ 88 才考虑解锁（高门槛保护）
  - fail-safe：任何异常返回未解锁状态，不影响主流程

数据层级（达摩院铁证备注）：
  BULL_CORRECTION_SHORT  WR=73.9%  n=494  【次铁证·待n≥500】
  BEAR_RECOVERY_LONG     WR=72.5%  n=430  【次铁证·待n≥500】
  → 两者均为系统最高WR体制，被n<500规则压制
  → Kronos置信度作为"第23+维统计证明"弥补样本缺口
"""

import time
import logging
from typing import Dict, Tuple, Any

logger = logging.getLogger("recovery_unlocker")

# ── 解锁规则（达摩院封印）────────────────────────────────────────
UNLOCK_RULES: Dict[str, Dict] = {
    "BULL_CORRECTION_SHORT": {
        "regime":            "BULL_CORRECTION",
        "direction":         "SHORT",
        "kronos_p_min":      0.65,       # p_down > 65%（Kronos预测方向置信度）
        "base_score_min":    88,          # 原始分≥88（高门槛，避免弱信号解锁）
        "s23_unlock_bonus":  12,          # 解锁奖励（推过threshold=100门槛）
        "historical_wr":     0.739,       # 历史WR备注（次铁证·n=494）
        "max_daily_unlocks": 2,           # 每日最多2次（防过度触发）
        "note":              "最高WR体制73.9%，被n<500压制，Kronos双确认后解锁",
    },
    "BEAR_RECOVERY_LONG": {
        "regime":            "BEAR_RECOVERY",
        "direction":         "LONG",
        "kronos_p_min":      0.65,        # p_up > 65%
        "base_score_min":    88,
        "s23_unlock_bonus":  12,
        "historical_wr":     0.725,       # 历史WR备注（次铁证·n=430）
        "max_daily_unlocks": 2,
        "note":              "第二高WR体制72.5%，被n<500压制，Kronos双确认后解锁",
    },
}

# ── 每日解锁计数器 ────────────────────────────────────────────────
# {symbol_regime_dir: {date_str: count}}
_daily_counter: Dict[str, Dict[str, int]] = {}


def _get_today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _check_daily_limit(symbol: str, rule_key: str, max_count: int) -> bool:
    """返回True表示未超限（可以解锁），False表示已达上限"""
    counter_key = f"{symbol}:{rule_key}"
    today = _get_today()
    counts = _daily_counter.setdefault(counter_key, {})
    return counts.get(today, 0) < max_count


def _increment_counter(symbol: str, rule_key: str) -> None:
    counter_key = f"{symbol}:{rule_key}"
    today = _get_today()
    counts = _daily_counter.setdefault(counter_key, {})
    counts[today] = counts.get(today, 0) + 1


def check_unlock(
    regime: str,
    direction: str,
    base_score: float,
    kronos_meta: Dict[str, Any],
    symbol: str = "UNKNOWN",
) -> Dict[str, Any]:
    """
    检查是否满足体制解锁条件

    Args:
        regime:       当前体制标签（如 "BULL_CORRECTION"）
        direction:    信号方向（"LONG" | "SHORT"）
        base_score:   梵天当前评分（注入s23前的分数）
        kronos_meta:  Kronos引擎返回的元数据字典
                      必须包含: p_up (float), direction_conflict (bool, optional)
        symbol:       标的符号（用于计数器）

    Returns:
        {
          "unlocked":    bool,
          "s23_bonus":   int (0 if not unlocked),
          "regime":      str,
          "reason":      str,
          "rule_key":    str,
        }
    """
    _null = {"unlocked": False, "s23_bonus": 0, "regime": regime,
             "reason": "no_unlock_rule", "rule_key": ""}

    try:
        # ① 查找匹配的解锁规则
        rule_key = f"{regime}_{direction}"
        rule = UNLOCK_RULES.get(rule_key)
        if rule is None:
            return _null

        # ② base_score 门槛检查
        if base_score < rule["base_score_min"]:
            return {**_null, "reason": f"base_score_low:{base_score:.0f}<{rule['base_score_min']}"}

        # ③ 从 kronos_meta 提取方向置信概率
        p_up = kronos_meta.get("p_up", 0.5)
        if p_up is None:
            p_up = 0.5

        # 根据方向计算置信概率
        if direction == "LONG":
            p_signal = float(p_up)
        else:  # SHORT
            p_signal = 1.0 - float(p_up)

        # ④ Kronos 置信度门槛检查
        if p_signal < rule["kronos_p_min"]:
            return {
                **_null,
                "reason": f"kronos_low:{p_signal:.2f}<{rule['kronos_p_min']}",
            }

        # ⑤ 每日解锁次数限制
        if not _check_daily_limit(symbol, rule_key, rule["max_daily_unlocks"]):
            return {**_null, "reason": f"daily_limit_reached:{rule['max_daily_unlocks']}"}

        # ⑥ 所有条件满足 → 解锁！
        _increment_counter(symbol, rule_key)
        reason = (
            f"UNLOCKED:{regime}_{direction}"
            f"|p_signal={p_signal:.2f}"
            f"|base={base_score:.0f}"
            f"|hist_wr={rule['historical_wr']:.3f}"
        )
        logger.info(f"[UnlockEngine] {symbol} {reason}")
        return {
            "unlocked":   True,
            "s23_bonus":  rule["s23_unlock_bonus"],
            "regime":     regime,
            "reason":     reason,
            "rule_key":   rule_key,
        }

    except Exception as e:
        logger.warning(f"[UnlockEngine] {symbol} 解锁检查异常: {e}")
        return {**_null, "reason": f"exception:{str(e)[:40]}"}


# ── 单元测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== recovery_unlocker.py 单元测试 ===\n")

    test_cases = [
        # (regime, direction, base_score, p_up, symbol, 期望解锁)
        ("BULL_CORRECTION", "SHORT", 90, 0.25, "BTCUSDT", True),   # p_down=0.75>0.65 ✅
        ("BULL_CORRECTION", "SHORT", 90, 0.40, "ETHUSDT", False),  # p_down=0.60<0.65 ❌
        ("BULL_CORRECTION", "SHORT", 80, 0.25, "SOLUSDT", False),  # base_score<88 ❌
        ("BEAR_RECOVERY",   "LONG",  91, 0.70, "BTCUSDT", True),   # p_up=0.70>0.65 ✅
        ("BEAR_RECOVERY",   "LONG",  88, 0.60, "ETHUSDT", False),  # p_up=0.60<0.65 ❌
        ("BEAR_EARLY",      "SHORT", 95, 0.25, "BTCUSDT", False),  # 无规则 ❌
        ("CHOP_MID",        "SHORT", 95, 0.25, "BTCUSDT", False),  # 无规则 ❌
    ]

    passed = 0
    for regime, direction, base_score, p_up, symbol, expected in test_cases:
        meta = {"p_up": p_up}
        result = check_unlock(regime, direction, base_score, meta, symbol)
        ok = result["unlocked"] == expected
        status = "✅" if ok else "❌"
        if ok:
            passed += 1
        print(f"{status} {regime}_{direction} base={base_score} p_up={p_up:.2f} sym={symbol}")
        print(f"   → unlocked={result['unlocked']} bonus={result['s23_bonus']} reason={result['reason']}")

    print(f"\n通过: {passed}/{len(test_cases)}")

    # 每日限制测试
    print("\n--- 每日限制测试 ---")
    for i in range(4):
        meta = {"p_up": 0.20}
        r = check_unlock("BULL_CORRECTION", "SHORT", 92, meta, "LIMITTEST")
        print(f"  第{i+1}次: unlocked={r['unlocked']} reason={r['reason']}")
