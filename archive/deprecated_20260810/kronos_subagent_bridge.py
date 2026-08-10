#!/usr/bin/env python3
"""
kronos_subagent_bridge.py - Kronos x Claude Subagent推断桥接层
设计院自主实现 2026-08-07 | 苏摩111授权封印

背景：
  - kronos_engine.py 依赖本地LightGBM(torch)，容器环境无法安装
  - OmniRoute api_key截断12字符，无法调用openrouter.ai
  - 解决方案：用MEMORY.md铁证规则库实现高质量启发式推断
    + 预留subagent接口，未来可升级为真正的Claude推断

核心：_run_heuristic_direct() 直接接收已解析参数，
      完全绕过prompt字符串re.search的不稳定性
"""

import json
import time
import hashlib
import logging
import pathlib
from typing import Tuple, Optional

logger = logging.getLogger("kronos_subagent")

BASE = pathlib.Path(__file__).parent.parent
CACHE_PATH = BASE / "data" / "kronos_subagent_cache.json"
CACHE_TTL = 1800  # 30分钟

_MEM_CACHE: dict = {}


# ── 缓存 ─────────────────────────────────────────────────────────────

def _cache_key(symbol: str, direction: str, regime: str, rsi_4h: float) -> str:
    bucket = int(rsi_4h // 5) * 5
    raw = f"{symbol}:{direction}:{regime}:{bucket}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _get_cached(key: str) -> Optional[Tuple[float, str]]:
    now = time.time()
    if key in _MEM_CACHE:
        ts, p_up, conf = _MEM_CACHE[key]
        if now - ts < CACHE_TTL:
            return p_up, conf
    try:
        if CACHE_PATH.exists():
            disk = json.loads(CACHE_PATH.read_text())
            if key in disk:
                entry = disk[key]
                if now - entry.get("ts", 0) < CACHE_TTL:
                    p_up = entry["p_up"]
                    conf = entry.get("confidence", "low")
                    _MEM_CACHE[key] = (entry["ts"], p_up, conf)
                    return p_up, conf
    except Exception:
        pass
    return None


def _set_cache(key: str, p_up: float, confidence: str):
    now = time.time()
    _MEM_CACHE[key] = (now, p_up, confidence)
    try:
        disk = {}
        if CACHE_PATH.exists():
            disk = json.loads(CACHE_PATH.read_text())
        disk[key] = {"ts": now, "p_up": p_up, "confidence": confidence}
        CACHE_PATH.write_text(json.dumps(disk, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ── 核心推断：MEMORY.md铁证规则库 ──────────────────────────────────────

def _run_heuristic_direct(
    regime: str,
    direction: str,
    rsi_1h: float = 50.0,
    rsi_4h: float = 50.0,
) -> dict:
    """
    基于MEMORY.md封印铁证的高质量启发式推断。
    直接接收已解析参数，无需从prompt字符串提取。

    铁证来源（MEMORY.md 2026-08-07）：
      BULL_TREND:LONG RSI4H<40  WR=66.7% n=25
      BULL_TREND:LONG RSI4H40-50 WR=78.9% n=28（最优！）
      BULL_TREND:LONG RSI4H50-55 WR=38.9%
      BULL_TREND:LONG RSI4H55-60 WR=29.4%
      BULL_TREND:LONG RSI4H>60  WR=5.9%（死亡区）
      BEAR_TREND:LONG           WR=45%（死穴封禁）
      BEAR_TREND:SHORT RSI4H>60 WR=68.1%（铁证）
      TIGHT压缩猎手             WR=97.5%（n=1600）
    """
    p_up = 0.50
    confidence = "mid"
    regime_alignment = True

    # ── BULL_TREND ──────────────────────────────────────────────────
    if regime == "BULL_TREND":
        if direction == "LONG":
            if rsi_4h < 40:
                p_up, confidence = 0.68, "high"     # WR=66.7%铁证
            elif rsi_4h < 50:
                p_up, confidence = 0.72, "high"     # WR=78.9%最优
            elif rsi_4h < 55:
                p_up, confidence = 0.50, "mid"      # WR=38.9%中性
            elif rsi_4h < 60:
                p_up, confidence = 0.38, "mid"      # WR=29.4%偏弱
            else:
                p_up, confidence = 0.22, "high"     # WR=5.9%死亡区
                regime_alignment = False
        else:  # SHORT in BULL_TREND
            p_up, confidence = 0.30, "mid"          # 逆势，低胜率
            regime_alignment = False

    # ── BEAR_TREND ──────────────────────────────────────────────────
    elif regime == "BEAR_TREND":
        if direction == "LONG":
            p_up, confidence = 0.20, "high"         # 死穴WR=45%封禁
            regime_alignment = False
        else:  # SHORT
            if rsi_4h > 60:
                p_up, confidence = 0.72, "high"     # WR=68.1%铁证
            elif rsi_4h > 50:
                p_up, confidence = 0.62, "mid"
            else:
                p_up, confidence = 0.55, "low"

    # ── BEAR_RECOVERY ────────────────────────────────────────────────
    elif regime == "BEAR_RECOVERY":
        if direction == "LONG":
            if rsi_4h < 45:
                p_up, confidence = 0.62, "mid"      # 恢复阶段做多
            else:
                p_up, confidence = 0.50, "low"
        else:  # SHORT in BEAR_RECOVERY（封禁）
            p_up, confidence = 0.28, "high"
            regime_alignment = False

    # ── CHOP_MID ─────────────────────────────────────────────────────
    elif regime == "CHOP_MID":
        p_up, confidence = 0.48, "low"              # 震荡中性
        if direction == "SHORT":
            p_up = 0.52

    # ── BEAR_EARLY ───────────────────────────────────────────────────
    elif regime == "BEAR_EARLY":
        if direction == "LONG":
            p_up, confidence = 0.35, "mid"
            regime_alignment = False
        else:
            p_up, confidence = 0.60, "mid"

    # ── 未知体制（默认中性）──────────────────────────────────────────
    else:
        if rsi_4h < 45:
            p_up = 0.58 if direction == "LONG" else 0.42
        elif rsi_4h > 60:
            p_up = 0.35 if direction == "LONG" else 0.65
        else:
            p_up = 0.50
        confidence = "low"

    # RSI_1H微调（±0.05，最大不超边界）
    rsi1h_adj = 0.0
    if direction == "LONG":
        if rsi_1h < 30:
            rsi1h_adj = +0.06
        elif rsi_1h < 40:
            rsi1h_adj = +0.03
        elif rsi_1h > 75:
            rsi1h_adj = -0.06
        elif rsi_1h > 65:
            rsi1h_adj = -0.03
    else:
        if rsi_1h > 75:
            rsi1h_adj = +0.06
        elif rsi_1h > 65:
            rsi1h_adj = +0.03
        elif rsi_1h < 30:
            rsi1h_adj = -0.06

    p_up = max(0.05, min(0.95, p_up + rsi1h_adj))

    reason = (
        f"{regime}:{direction} RSI4H={rsi_4h:.0f} RSI1H={rsi_1h:.0f}"
        f" -> p_up={p_up:.3f}({confidence})"
    )

    return {
        "p_up": round(p_up, 3),
        "confidence": confidence,
        "regime_alignment": regime_alignment,
        "reason": reason,
        "source": "heuristic_v2",
    }


# ── 对外主接口（与kronos_engine签名兼容）────────────────────────────────

def get_kronos_score_via_claude(
    symbol: str,
    direction: str,
    regime: str,
    rsi_1h: float = 50.0,
    rsi_4h: float = 50.0,
    price: float = 0.0,
    ob_dist_pct: float = 1.0,
    ema20_gap_pct: float = 0.0,
    klines_15m: list = None,
) -> Tuple[int, str]:
    """
    对外主接口，供brahma_engine s23段调用。
    返回 (score, reason)，score范围 -12 ~ +12
    """
    # ① 缓存命中
    ckey = _cache_key(symbol, direction, regime, rsi_4h)
    cached = _get_cached(ckey)
    if cached:
        p_up, conf = cached
        score = _p_up_to_score(p_up, direction)
        return score, f"kronos_subagent:cache({conf}) p_up={p_up:.3f}"

    # ② 推断
    result = _run_heuristic_direct(
        regime=regime,
        direction=direction,
        rsi_1h=rsi_1h,
        rsi_4h=rsi_4h,
    )

    if not result:
        return 0, "kronos_subagent:failed"

    p_up = max(0.05, min(0.95, float(result.get("p_up", 0.5))))
    confidence = result.get("confidence", "low")

    # ③ 写缓存
    _set_cache(ckey, p_up, confidence)

    # ④ 转分数
    score = _p_up_to_score(p_up, direction)
    reason = result.get("reason", "")
    source = result.get("source", "heuristic")

    return score, f"kronos_subagent:{source}({confidence}) p_up={p_up:.3f} {reason}"


def _p_up_to_score(p_up: float, direction: str) -> int:
    """与kronos_engine._p_up_to_score完全一致"""
    if direction == "LONG":
        if p_up > 0.70:   return +12
        elif p_up > 0.60: return +8
        elif p_up > 0.55: return +4
        elif p_up > 0.45: return 0
        elif p_up > 0.35: return -8
        else:             return -12
    else:
        p_down = 1.0 - p_up
        if p_down > 0.70:   return +12
        elif p_down > 0.60: return +8
        elif p_down > 0.55: return +4
        elif p_down > 0.45: return 0
        elif p_down > 0.35: return -8
        else:               return -12


# ── 冒烟测试 ─────────────────────────────────────────────────────────

def smoke_test():
    print("=== kronos_subagent_bridge v2 冒烟测试 ===\n")

    test_cases = [
        # symbol, dir, regime, rsi1h, rsi4h, expected_p_up_range
        ("BTCUSDT", "LONG",  "BULL_TREND",    45, 44, (0.60, 0.80), "BULL最优区"),
        ("ETHUSDT", "LONG",  "BULL_TREND",    55, 53, (0.40, 0.55), "BULL中性区"),
        ("BTCUSDT", "LONG",  "BULL_TREND",    65, 63, (0.18, 0.35), "BULL死亡区"),
        ("BTCUSDT", "LONG",  "BEAR_TREND",    40, 42, (0.15, 0.30), "BEAR死穴"),
        ("ETHUSDT", "SHORT", "BEAR_TREND",    68, 65, (0.62, 0.80), "BEAR最优空"),
        ("BTCUSDT", "LONG",  "CHOP_MID",      50, 50, (0.40, 0.58), "CHOP中性"),
        ("BTCUSDT", "LONG",  "BEAR_RECOVERY", 38, 42, (0.55, 0.72), "RECOVERY做多"),
    ]

    all_pass = True
    import re
    # 删除磁盘缓存，确保每次新推断
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()

    for sym, direction, regime, rsi1, rsi4, (lo, hi), label in test_cases:
        _MEM_CACHE.clear()

        score, reason = get_kronos_score_via_claude(
            symbol=sym, direction=direction, regime=regime,
            rsi_1h=rsi1, rsi_4h=rsi4, price=64000.0
        )
        m = re.search(r'p_up=([\d.]+)', reason)
        p_up = float(m.group(1)) if m else 0.5

        ok = lo <= p_up <= hi
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False

        print(f"  [{status}] {label} ({sym} {direction} {regime} RSI4H={rsi4})")
        print(f"    p_up={p_up:.3f} (期望{lo:.2f}~{hi:.2f}) score={score:+d}")
        print(f"    {reason}\n")

    total = len(test_cases)
    passed = sum(1 for *_, (lo, hi), _ in test_cases
                 if True)  # count below
    print(f"结论: {'全部通过 ✅' if all_pass else '存在失败 ❌'}")
    return all_pass


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    smoke_test()
