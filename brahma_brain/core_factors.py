"""
core_factors.py — Step5-6 多周期因子层适配（第四刀）
接入位置：brahma_brain/brahma_core.py → from brahma_brain.core_factors import apply_tf_factors, apply_causal_factors
2026-09-07 设计院拆块封印（苏摩111）

职责：
1. 封装 confluence_tf_weights + confluence_by_tf（多周期权重调整 + 共振奖励）
2. 封装 counterfactual_score_check（维度因果归因）
3. 捕获所有import/运行异常 —— 这两层都是fail-safe设计，失败不影响主流程
4. 返回 TfFactorResult / CausalFactorResult dataclass，替代裸dict+side-effect修改
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────
#  多周期因子层
# ─────────────────────────────────────────────────────────

@dataclass
class TfFactorResult:
    """多周期权重调整 + 共振奖励的统一出口"""
    score_in: float = 0.0
    score_out: float = 0.0          # 调整后分数
    tf_weight_mult: float = 1.0     # 权重乘数（L4小币=0.85等）
    tf_boost: int = 0               # 共振奖励 (+0~+8)
    tf_summary: str = ""            # "1H+4H双周期共振"
    tf_meta: dict = field(default_factory=dict)
    ok: bool = True
    error: str = ""

    @property
    def delta(self) -> float:
        return self.score_out - self.score_in

    @property
    def breakdown_entries(self) -> dict:
        """直接注入analyze()的breakdown dict"""
        entries = {}
        if abs(self.tf_weight_mult - 1.0) > 0.01:
            entries['TF权重调整'] = f'×{self.tf_weight_mult:.2f} {self.score_in:.0f}→{self.score_in * self.tf_weight_mult:.0f}'
        if self.tf_boost > 0:
            entries['TF共振奖励'] = f'+{self.tf_boost} [{self.tf_summary}]'
        return entries


def apply_tf_factors(
    score: float,
    breakdown: dict,
    symbol: str,
    signal_dir: str,
    primary_tf: str,
    signal_source: str,
    extra_data: dict | None = None,
) -> TfFactorResult:
    """
    统一应用多周期因子（TF权重 + TF共振奖励）。
    替代 analyze() 里两段嵌套 try/except import。
    """
    result = TfFactorResult(score_in=score, score_out=score)

    # Step5-A: TF权重调整（L4/L5小币分周期加权）
    try:
        from confluence_tf_weights import get_score_multiplier as _get_tf_mult
        mult = _get_tf_mult(symbol, score, primary_tf, signal_source)
        if abs(mult - 1.0) > 0.01:
            result.tf_weight_mult = mult
            result.score_out = round(score * mult, 1)
    except Exception as e:
        result.error = f"tf_weights:{e}"

    # Step5-B: 多周期共振奖励 (+0~+8)
    try:
        from confluence_by_tf import apply_tf_confluence as _apply_tf_cf
        adj_score, tf_meta = _apply_tf_cf(
            float(result.score_out), breakdown, symbol, signal_dir, primary_tf, signal_source
        )
        boost = tf_meta.get('tf_boost', 0)
        if boost > 0:
            result.tf_boost = boost
            result.tf_summary = tf_meta.get('summary', '')
            result.tf_meta = tf_meta
            result.score_out = adj_score
            if extra_data is not None:
                extra_data['tf_confluence'] = tf_meta
    except Exception as e:
        # 追加错误而非覆盖
        result.error = f"{result.error}|tf_confluence:{e}".lstrip('|')

    result.ok = result.error == ""
    return result


# ─────────────────────────────────────────────────────────
#  因果归因层
# ─────────────────────────────────────────────────────────

@dataclass
class CausalFactorResult:
    """维度因果归因出口"""
    score_adj: int = 0
    verdict: str = "NEUTRAL"        # BULLISH / BEARISH / NEUTRAL
    causal_ratio: float = 0.0       # 因果维度占比
    raw: dict = field(default_factory=dict)
    ok: bool = True
    error: str = ""
    skipped: bool = False           # score < 100 时直接跳过

    @property
    def breakdown_entry(self) -> str | None:
        if self.score_adj == 0:
            return None
        return (f'{self.score_adj:+d}(因果归因:{self.verdict} '
                f'因果维度{self.causal_ratio:.0%})')


def apply_causal_factors(
    score: float,
    signal_dir: str,
    regime: str,
    cf_dict: dict,
    extra_data: dict | None = None,
    timeout_ms: int = 80,
) -> CausalFactorResult:
    """
    封装 counterfactual_score_check。
    score < 100 直接跳过（与原逻辑一致）。
    """
    if score < 100:
        return CausalFactorResult(skipped=True)

    try:
        from counterfactual_score_check import check as _cfc_check
        result_raw = _cfc_check(cf_dict, signal_dir, regime, timeout_ms=timeout_ms)
        adj   = result_raw.get('score_adj', 0)
        vrdt  = result_raw.get('verdict', 'NEUTRAL')
        cratio = result_raw.get('causal_ratio', 0.0)

        if extra_data is not None:
            extra_data['counterfactual'] = result_raw

        return CausalFactorResult(
            score_adj=adj,
            verdict=vrdt,
            causal_ratio=cratio,
            raw=result_raw,
            ok=True,
        )
    except Exception as e:
        return CausalFactorResult(ok=False, error=str(e))
