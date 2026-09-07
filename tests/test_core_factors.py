"""
test_core_factors.py — 多周期因子层测试（第四刀）
接入位置：brahma_brain/core_factors.py
2026-09-07 设计院拆块封印
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from brahma_brain.core_factors import (
    apply_tf_factors, apply_causal_factors,
    TfFactorResult, CausalFactorResult,
)


def test_tf_factors_returns_result():
    """apply_tf_factors必须返回TfFactorResult，不能raise"""
    result = apply_tf_factors(
        score=142.0, breakdown={}, symbol="BTCUSDT",
        signal_dir="LONG", primary_tf="1h", signal_source="default",
    )
    assert isinstance(result, TfFactorResult)
    assert result.score_in == 142.0
    assert isinstance(result.score_out, float)
    # 外部模块不存在时应该静默（ok=False但不raise）
    print(f"✅ apply_tf_factors: score_in={result.score_in} score_out={result.score_out} ok={result.ok} err='{result.error[:40]}'")


def test_tf_factors_never_raises():
    """空参数不能raise"""
    r = apply_tf_factors(score=0.0, breakdown={}, symbol="", signal_dir="SHORT",
                          primary_tf="", signal_source="")
    assert isinstance(r, TfFactorResult)
    print(f"✅ 空参数TF: ok={r.ok}")


def test_tf_factor_result_delta():
    """delta属性正确"""
    r = TfFactorResult(score_in=140.0, score_out=145.0, tf_boost=5)
    assert r.delta == 5.0
    print(f"✅ TfFactorResult.delta={r.delta}")


def test_causal_skips_low_score():
    """score < 100 直接跳过，不调用外部模块"""
    r = apply_causal_factors(score=85.0, signal_dir="LONG",
                              regime="BULL_TREND", cf_dict={})
    assert isinstance(r, CausalFactorResult)
    assert r.skipped is True
    assert r.score_adj == 0
    print(f"✅ score<100 跳过因果归因: skipped={r.skipped}")


def test_causal_returns_result():
    """score >= 100 时返回CausalFactorResult，不能raise"""
    r = apply_causal_factors(score=120.0, signal_dir="LONG",
                              regime="BULL_TREND", cf_dict={"score": 120})
    assert isinstance(r, CausalFactorResult)
    print(f"✅ apply_causal_factors: adj={r.score_adj} verdict={r.verdict} ok={r.ok} err='{r.error[:40]}'")


def test_causal_breakdown_entry():
    """breakdown_entry格式正确"""
    r = CausalFactorResult(score_adj=-5, verdict="BEARISH", causal_ratio=0.42)
    entry = r.breakdown_entry
    assert entry is not None
    assert "-5" in entry
    assert "BEARISH" in entry
    assert "42%" in entry
    print(f"✅ breakdown_entry: '{entry}'")


def test_causal_zero_adj_no_entry():
    """score_adj=0时breakdown_entry=None"""
    r = CausalFactorResult(score_adj=0)
    assert r.breakdown_entry is None
    print("✅ score_adj=0 → breakdown_entry=None")


if __name__ == "__main__":
    test_tf_factors_returns_result()
    test_tf_factors_never_raises()
    test_tf_factor_result_delta()
    test_causal_skips_low_score()
    test_causal_returns_result()
    test_causal_breakdown_entry()
    test_causal_zero_adj_no_entry()
    print("\n全部7项core_factors测试通过 ✅")
