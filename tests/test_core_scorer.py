"""
test_core_scorer.py — 验证core_scorer独立可测试适配层
接入位置：brahma_brain/core_scorer.py
2026-09-07 设计院拆块封印
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from brahma_brain.core_scorer import score_signal, ScoreResult, _normalize_ms


def test_normalize_ms_string_trend():
    """trend字符串→嵌套字典，防止block_a TypeError"""
    ms = {"regime": "BULL_TREND", "trend": "up", "symbol": "BTCUSDT"}
    norm = _normalize_ms(ms)
    assert isinstance(norm["trend"], dict)
    assert "consensus" in norm["trend"]
    assert norm["trend"]["consensus"]["consensus"] in ("LEAN_BULL", "FULL_BULL", "NEUTRAL")
    assert "1h" in norm["trend"]
    assert isinstance(norm["trend"]["1h"]["adx"], float)
    print("✅ normalize: 字符串trend→嵌套字典")


def test_normalize_ms_dict_trend():
    """trend已经是字典，不应被修改"""
    trend = {
        "consensus": {"consensus": "FULL_BEAR"},
        "1h": {"adx": 35.0, "direction": "down"},
        "4h": {"adx": 30.0, "direction": "down"},
        "15m": {"adx": 25.0, "direction": "down"},
    }
    ms = {"regime": "BEAR_TREND", "trend": trend}
    norm = _normalize_ms(ms)
    assert norm["trend"]["consensus"]["consensus"] == "FULL_BEAR"
    print("✅ normalize: 字典trend原样保留")


def test_score_signal_returns_score_result():
    """score_signal必须返回ScoreResult，不能raise"""
    ms = {
        "regime": "BULL_TREND",
        "trend": "up",
        "symbol": "BTCUSDT",
        "rsi_1h": 55.0,
        "rsi_4h": 58.0,
        "price": 80000.0,
    }
    result = score_signal(ms=ms, smc={}, signal_dir="LONG", extra_data={"price": 80000})
    assert isinstance(result, ScoreResult)
    assert isinstance(result.score, float)
    assert result.direction == "LONG"
    assert result.regime == "BULL_TREND"
    print(f"✅ score_signal: score={result.score:.1f} grade={result.grade:.1f} error='{result.error}'")


def test_score_signal_never_raises():
    """即使ms完全空，也不能raise"""
    result = score_signal(ms={}, smc={}, signal_dir="SHORT")
    assert isinstance(result, ScoreResult)
    # 空ms要么返回score=0+error，要么正常运行
    print(f"✅ score_signal(空ms): score={result.score} error='{result.error[:50]}'")


def test_score_result_bucket():
    """ScoreResult.bucket正确分桶"""
    cases = [
        (75.0, "<80"),
        (85.0, "80-100"),
        (110.0, "100-120"),
        (135.0, "120-140"),
        (148.0, "140-155"),
        (160.0, ">=155"),
    ]
    for score, expected in cases:
        r = ScoreResult(score=score)
        assert r.bucket == expected, f"score={score} expected={expected} got={r.bucket}"
    print("✅ ScoreResult.bucket: 所有分桶正确")


if __name__ == "__main__":
    test_normalize_ms_string_trend()
    test_normalize_ms_dict_trend()
    test_score_signal_returns_score_result()
    test_score_signal_never_raises()
    test_score_result_bucket()
    print("\n全部5项core_scorer测试通过 ✅")
