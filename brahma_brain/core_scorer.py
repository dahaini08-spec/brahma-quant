"""
core_scorer.py — confluence_score() 独立可测试适配层
接入位置：brahma_brain/brahma_core.py → from brahma_brain.core_scorer import score_signal
2026-09-07 设计院拆块封印（苏摩111）

职责：
1. 标准化ms字典格式（防止trend字段类型不一致导致block_a崩溃）
2. 调用confluence_score()并捕获所有异常
3. 返回标准化的ScoreResult，外部无需知道内部block结构
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreResult:
    score: float = 0.0
    grade: float = 0.0
    breakdown: dict = field(default_factory=dict)
    regime: str = "UNKNOWN"
    direction: str = "LONG"
    error: str = ""

    @property
    def is_valid(self) -> bool:
        return not self.error and self.score > 0

    @property
    def bucket(self) -> str:
        if self.score < 80:   return "<80"
        if self.score < 100:  return "80-100"
        if self.score < 120:  return "100-120"
        if self.score < 140:  return "120-140"
        if self.score < 155:  return "140-155"
        return ">=155"


def _normalize_ms(ms: dict) -> dict:
    """
    标准化ms字典，确保block_a/b/c期望的字段存在且类型正确。

    根因（2026-09-07发现）：
    - ms['trend'] 有时是字符串 'up'/'down'，有时是嵌套字典
    - block_a L61: ms['trend']['consensus']['consensus'] → TypeError
    - 修复：若trend是字符串，转换为block_a期望的嵌套字典格式
    """
    ms = dict(ms)  # 不修改原始字典

    trend = ms.get("trend", "")
    if isinstance(trend, str):
        # 将简单字符串趋势转换为block_a期望的嵌套格式
        trend_str = str(trend).lower()
        if "up" in trend_str or "bull" in trend_str:
            consensus_val = "LEAN_BULL"
            adx = 28.0
        elif "down" in trend_str or "bear" in trend_str:
            consensus_val = "LEAN_BEAR"
            adx = 28.0
        else:
            consensus_val = "NEUTRAL"
            adx = 20.0

        ms["trend"] = {
            "consensus": {"consensus": consensus_val},
            "1h":  {"adx": adx, "direction": trend_str},
            "4h":  {"adx": adx, "direction": trend_str},
            "15m": {"adx": adx, "direction": trend_str},
        }
    elif not isinstance(trend, dict):
        ms["trend"] = {
            "consensus": {"consensus": "NEUTRAL"},
            "1h":  {"adx": 20.0, "direction": "neutral"},
            "4h":  {"adx": 20.0, "direction": "neutral"},
            "15m": {"adx": 20.0, "direction": "neutral"},
        }

    # 确保momentum子字典存在（block_a依赖）
    if "momentum" not in ms:
        ms["momentum"] = {
            "rsi_1h":  ms.get("rsi_1h", 50.0),
            "rsi_4h":  ms.get("rsi_4h", 50.0),
            "rsi_15m": ms.get("rsi_15m", 50.0),
            "atr_1h":  ms.get("atr_1h", 0.0),
            "atr_4h":  ms.get("atr_4h", 0.0),
        }

    return ms


def score_signal(
    ms: dict,
    smc: dict,
    signal_dir: str,
    extra_data: dict | None = None,
) -> ScoreResult:
    """
    统一评分入口。替代直接调用confluence_score()。
    - 自动normalize ms格式
    - 捕获所有block级别异常，返回ScoreResult而非裸dict
    - 外部代码测试这个函数，不测试confluence_score内部
    """
    extra_data = extra_data or {}
    regime = str(ms.get("regime", "UNKNOWN"))
    direction = signal_dir or "LONG"

    try:
        ms_norm = _normalize_ms(ms)
        from brahma_brain.brahma_core import confluence_score  # 延迟import防循环
        raw = confluence_score(
            ms=ms_norm,
            smc=smc or {},
            signal_dir=direction,
            extra_data=extra_data,
        )
        score = float(raw.get("score", 0))
        grade = float(raw.get("effective_grade", raw.get("grade_num", 0)))
        breakdown = raw.get("breakdown", {})
        return ScoreResult(
            score=score,
            grade=grade,
            breakdown=breakdown,
            regime=regime,
            direction=direction,
        )
    except Exception as e:
        return ScoreResult(
            score=0.0,
            grade=0.0,
            regime=regime,
            direction=direction,
            error=str(e),
        )
