"""
brahma_v6/apps/signal_adapter.py — 旧版信号结果 → 强类型 SignalScoredEvent
设计院 P1 | 2026-07-08

解决故障1：SignalBusAdapter 产出 raw dict，不是强类型 SignalScoredEvent。
本模块提供 adapt_analysis_result() 做精确字段映射。
"""
from __future__ import annotations
import time
import uuid
from typing import Dict, Any, Optional

from brahma_v6.schemas.events import SignalScoredEvent, make_signal_event


def adapt_analysis_result(result: Dict[str, Any]) -> Optional[SignalScoredEvent]:
    """
    把 brahma_analysis_runner.run_analysis() / brahma_orchestrator.analyze() 的
    输出字典转换为强类型 SignalScoredEvent。

    字段映射（旧 → 新）：
      symbol           → symbol
      signal_dir/direction → direction
      score/score_final → raw_score / final_score
      regime           → regime
      grade            → grade
      blocked/globally_blocked → blocked
      block_reason     → block_reason
      regime_bonus     → regime_bonus
      noise_penalty    → noise_penalty
      confidence       → confidence
      timing_status    → payload["timing_status"]
      price/entry      → payload["entry_price"]
      stop_loss        → payload["stop_loss"]
      tp1              → payload["take_profit"]
      valid_signal     → valid_after_adj
      trace_id         → trace_id (透传)

    返回 None 当结果明显无效（symbol/direction/score 缺失）。
    """
    if not result:
        return None

    symbol = result.get("symbol", "")
    if not symbol:
        return None

    direction = (
        result.get("signal_dir") or
        result.get("direction") or
        result.get("dir") or
        ""
    )
    if not direction or direction not in ("LONG", "SHORT"):
        return None

    raw_score = float(result.get("score", 0) or result.get("raw_score", 0))
    final_score = float(
        result.get("score_final") or
        result.get("final_score") or
        result.get("score") or
        raw_score
    )
    if final_score <= 0:
        return None

    regime = result.get("regime", "UNKNOWN")
    grade = str(result.get("grade", ""))
    blocked = bool(
        result.get("blocked") or
        result.get("globally_blocked") or
        (not result.get("valid_signal", True))
    )
    block_reason = result.get("block_reason", "")
    if blocked and not block_reason:
        block_reason = result.get("action", "") or "blocked"

    regime_bonus = float(result.get("regime_bonus", 0))
    noise_penalty = float(result.get("noise_penalty", 0))
    confidence = float(result.get("confidence", 0) or result.get("council_confidence", 0))

    # 调整追踪
    adjustment_trace: list = result.get("adjustment_trace", [])
    if not adjustment_trace and "score_adjustments" in result:
        adjustment_trace = [str(a) for a in result["score_adjustments"]]

    # 特征归因
    top_pos = result.get("top_positive_features", [])
    top_neg = result.get("top_negative_features", [])

    trace_id = result.get("trace_id", str(uuid.uuid4()))

    sig = make_signal_event(
        symbol=symbol,
        direction=direction,
        raw_score=raw_score,
        final_score=final_score,
        regime=regime,
        grade=grade,
        blocked=blocked,
        block_reason=block_reason,
        regime_bonus=regime_bonus,
        noise_penalty=noise_penalty,
        confidence=confidence,
        top_pos=top_pos,
        top_neg=top_neg,
        adjustment_trace=adjustment_trace,
        source="brahma_analysis_runner",
    )
    sig.trace_id = trace_id  # 透传原始 trace_id

    # 附加 payload（timing、价格、止损）
    sig.payload.update({
        "entry_price":    result.get("price") or result.get("entry") or 0,
        "stop_loss":      result.get("stop_loss", 0),
        "take_profit":    result.get("tp1") or result.get("take_profit") or 0,
        "timing_status":  result.get("timing_status", ""),
        "timing_badge":   result.get("timing_badge", ""),
        "action":         result.get("action", ""),
        "valid_signal":   result.get("valid_signal", not blocked),
    })

    return sig


if __name__ == "__main__":
    # 模拟旧版 run_analysis() 输出
    old_result = {
        "symbol": "BTCUSDT",
        "signal_dir": "LONG",
        "score": 145.0,
        "score_final": 148.5,
        "regime": "BEAR_RECOVERY",
        "regime_cn": "熊市反弹",
        "grade": "🔴A级",
        "blocked": False,
        "valid_signal": True,
        "action": "WATCH",
        "confidence": 0.72,
        "price": 107000.0,
        "stop_loss": 103800.0,
        "tp1": 111400.0,
        "timing_status": "READY",
        "trace_id": "test-trace-001",
    }
    sig = adapt_analysis_result(old_result)
    if sig:
        print(f"✅ 适配成功")
        print(f"  symbol={sig.symbol} dir={sig.direction} score={sig.final_score}")
        print(f"  regime={sig.regime} blocked={sig.blocked}")
        print(f"  trace_id={sig.trace_id}")
        print(f"  payload keys={list(sig.payload.keys())}")
    else:
        print("❌ 适配失败")
