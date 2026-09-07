"""
test_core_extra.py — ExtraData适配层测试（第三刀）
接入位置：brahma_brain/core_extra.py
2026-09-07 设计院拆块封印
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from brahma_brain.core_extra import build_extra_data, ExtraData


def test_returns_extra_data():
    """build_extra_data必须返回ExtraData，不能raise"""
    result = build_extra_data(
        symbol="BTCUSDT", ms={"regime": "BULL_TREND", "price": 80000},
        smc={}, signal_dir="LONG", price=80000.0,
    )
    assert isinstance(result, ExtraData)
    assert result.symbol == "BTCUSDT"
    assert result.signal_dir == "LONG"
    print(f"✅ build_extra_data: ok={result.ok} elapsed={result.elapsed_ms}ms error='{result.error[:50]}'")


def test_never_raises():
    """空参数不能raise"""
    result = build_extra_data(symbol="", ms={}, smc={}, signal_dir="SHORT", price=0.0)
    assert isinstance(result, ExtraData)
    print(f"✅ 空参数: ok={result.ok} error='{result.error[:60]}'")


def test_dict_compat():
    """ExtraData兼容dict接口：.get() / __contains__ / __getitem__"""
    ed = ExtraData(
        symbol="ETHUSDT", price=2400.0,
        raw={"fear_greed": 65, "volume": {"score": 8}, "coinglass": {"oi": 1e9}},
        fear_greed=65, volume_score=8.0, ok=True,
    )
    assert ed.get("fear_greed") == 65
    assert ed.get("missing_key", "default") == "default"
    assert "coinglass" in ed
    assert ed["fear_greed"] == 65
    assert ed.coinglass == {"oi": 1e9}
    print("✅ dict兼容: .get() / in / [] 全部正常")


def test_extra_data_properties():
    """属性访问安全"""
    ed = ExtraData(raw={"_klines_1h": {"o": [1, 2]}, "_k4h_closes": [100, 200]}, ok=True)
    assert ed.klines_1h == {"o": [1, 2]}
    assert ed.k4h_closes == [100, 200]
    assert ed.macro == {}       # 不存在时返回空dict
    assert ed.liq_snap == {}
    print("✅ 属性访问: klines_1h / k4h_closes / macro / liq_snap")


def test_ok_false_safe():
    """ok=False时所有字段安全访问"""
    ed = ExtraData(symbol="X", ok=False, error="timeout")
    assert ed.price == 0.0
    assert ed.fear_greed == 50
    assert ed.volume_score == 0.0
    assert ed.get("anything") is None
    print(f"✅ ok=False安全: price={ed.price} fear_greed={ed.fear_greed}")


if __name__ == "__main__":
    test_returns_extra_data()
    test_never_raises()
    test_dict_compat()
    test_extra_data_properties()
    test_ok_false_safe()
    print("\n全部5项core_extra测试通过 ✅")
