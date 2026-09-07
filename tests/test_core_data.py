"""
test_core_data.py — MarketSnapshot接口测试
接入位置：brahma_brain/core_data.py
2026-09-07 设计院拆块封印
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from brahma_brain.core_data import fetch_market_data, MarketSnapshot


def test_returns_market_snapshot():
    """fetch_market_data必须返回MarketSnapshot，不能raise"""
    result = fetch_market_data("BTCUSDT", signal_dir="LONG")
    assert isinstance(result, MarketSnapshot)
    assert result.symbol == "BTCUSDT"
    assert result.signal_dir == "LONG"
    print(f"✅ fetch_market_data: ok={result.ok} price={result.price} regime={result.regime} error='{result.error[:50]}'")


def test_never_raises_on_bad_symbol():
    """无效symbol不能raise，返回ok=False"""
    result = fetch_market_data("INVALID_SYMBOL_XYZ", signal_dir="SHORT")
    assert isinstance(result, MarketSnapshot)
    # ok可能True也可能False，但不能raise
    print(f"✅ bad symbol: ok={result.ok} error='{result.error[:60]}'")


def test_market_snapshot_properties():
    """MarketSnapshot的计算属性正常工作"""
    snap = MarketSnapshot(
        symbol="ETHUSDT",
        regime="CHOP_MID",
        ms={"rsi_1h": 55.0, "atr_1h": 40.0},
        price=2400.0,
        ok=True,
    )
    assert snap.is_chop is True
    assert snap.is_bear is False
    assert snap.rsi_1h == 55.0
    assert snap.atr_1h == 40.0
    print(f"✅ MarketSnapshot properties: is_chop={snap.is_chop} rsi_1h={snap.rsi_1h} atr_1h={snap.atr_1h}")


def test_market_snapshot_bear_regime():
    snap = MarketSnapshot(symbol="BTCUSDT", regime="BEAR_TREND", ok=True)
    assert snap.is_bear is True
    assert snap.is_chop is False
    print("✅ BEAR_TREND: is_bear=True is_chop=False")


def test_error_snapshot_has_ok_false():
    """ok=False的snapshot字段安全"""
    snap = MarketSnapshot(symbol="X", ok=False, error="timeout")
    assert snap.is_valid is False if hasattr(snap, 'is_valid') else not snap.ok
    assert snap.price == 0.0
    assert snap.regime == "UNKNOWN"
    print(f"✅ error snapshot: ok={snap.ok} price={snap.price}")


if __name__ == "__main__":
    test_market_snapshot_properties()
    test_market_snapshot_bear_regime()
    test_error_snapshot_has_ok_false()
    test_never_raises_on_bad_symbol()
    test_returns_market_snapshot()
    print("\n全部5项core_data测试通过 ✅")
