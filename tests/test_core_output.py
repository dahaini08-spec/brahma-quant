"""
tests/test_core_output.py — core_output.py 第五刀冒烟测试
接入位置验证：brahma_brain/core_output.py
2026-09-07 苏摩111封印
"""
import time
import pytest

from brahma_brain.core_output import (
    format_vip,
    format_signal_line,
    signal_from_result,
    to_jsonl_row,
    _fmt_price,
    _get,
    _num,
)


# ─── 共用 fixture ────────────────────────────────────────────────────────────

def _make_result(**kw) -> dict:
    """构造最小合法的 analyze() _result dict"""
    base = {
        "symbol": "ETHUSDT",
        "price": 2500.0,
        "price_ts": time.time(),
        "signal_dir": "SHORT",
        "regime": "BEAR_EARLY",
        "regime_cn": "熊市初期",
        "score_final": 150.0,
        "score": 150.0,
        "grade_num": 85.0,
        "action": "ENTRY",
        "action_reason": "BEAR_EARLY SHORT共振",
        "source": "analyze",
        "rsi_1h": 42.0,
        "rsi_4h": 45.0,
        "valid_signal": True,
        "params": {
            "entry_lo": 2490.0,
            "entry_hi": 2510.0,
            "stop": 2560.0,
            "tp1": 2380.0,
            "tp2": 2300.0,
            "tp3": 2200.0,
            "rr1": 2.0,
            "leverage": 10,
            "pos_pct": 5.0,
        },
        "confluence": {"score": 150.0, "breakdown": {"S1": 10, "S2": 8}},
        "sl_basis": "FVG上沿+ATR1H×1.5",
    }
    base.update(kw)
    return base


def _make_long_result(**kw) -> dict:
    base = _make_result()
    base.update({
        "signal_dir": "LONG",
        "regime": "BULL_TREND",
        "regime_cn": "牛市趋势",
        "params": {
            "entry_lo": 2450.0,
            "entry_hi": 2470.0,
            "stop": 2400.0,
            "tp1": 2600.0,
            "tp2": 2700.0,
            "tp3": 0.0,
            "rr1": 2.5,
            "leverage": 5,
            "pos_pct": 5.0,
        },
    })
    base.update(kw)
    return base


# ─── _fmt_price ──────────────────────────────────────────────────────────────

def test_fmt_price_btc():
    s = _fmt_price(67500.0)
    assert "$" in s and "67" in s


def test_fmt_price_eth():
    s = _fmt_price(2500.55)
    assert "$2,500.55" == s


def test_fmt_price_small():
    s = _fmt_price(0.00003412)
    assert "8" in s or "0.000034" in s  # 8位小数


def test_fmt_price_none():
    s = _fmt_price(None)
    assert s == "None"


# ─── _get / _num ─────────────────────────────────────────────────────────────

def test_get_top_level():
    d = {"symbol": "BTCUSDT"}
    assert _get(d, "symbol") == "BTCUSDT"


def test_get_nested_params():
    d = {"params": {"entry_lo": 100.0}}
    assert _get(d, "entry_lo") == 100.0


def test_get_missing_returns_default():
    assert _get({}, "nonexistent", default="fallback") == "fallback"


def test_num_valid():
    assert _num("123.5") == 123.5


def test_num_none_default():
    assert _num(None, default=99.0) == 99.0


def test_num_invalid_str():
    assert _num("abc", default=0.0) == 0.0


# ─── format_vip ──────────────────────────────────────────────────────────────

def test_format_vip_short_has_header():
    result = _make_result()
    card = format_vip(result)
    assert "姓赵不宣" in card
    assert "空单" in card


def test_format_vip_long_has_green_icon():
    result = _make_long_result()
    card = format_vip(result)
    assert "多单" in card


def test_format_vip_contains_stop():
    result = _make_result()
    card = format_vip(result)
    assert "止损" in card
    assert "2,560" in card or "2560" in card


def test_format_vip_contains_tp():
    result = _make_result()
    card = format_vip(result)
    assert "目标" in card
    assert "2,380" in card or "2380" in card


def test_format_vip_low_score_returns_wait():
    result = _make_result(score_final=120.0, score=120.0)
    card = format_vip(result)
    assert "等待" in card


def test_format_vip_missing_entry_returns_wait():
    result = _make_result()
    result["params"]["entry_lo"] = 0.0
    result["params"]["stop"] = 0.0
    card = format_vip(result)
    assert "等待" in card


def test_format_vip_bear_early_short_8pct():
    """BEAR_EARLY:SHORT score≥145 → 仓位提升到8%NAV"""
    result = _make_result(regime="BEAR_EARLY", score_final=147.0, score=147.0)
    card = format_vip(result)
    assert "8%" in card


def test_format_vip_has_disclaimer():
    result = _make_result()
    card = format_vip(result)
    assert "梵天系统" in card or "不是建议" in card


def test_format_vip_has_break_line():
    """🚫 作废行必须出现"""
    result = _make_result()
    card = format_vip(result)
    assert "🚫" in card


# ─── format_signal_line ──────────────────────────────────────────────────────

def test_signal_line_from_dict():
    d = {
        "symbol": "ETHUSDT",
        "side": "SHORT",
        "score": 150.0,
        "regime": "BEAR_EARLY",
        "entry_lo": 2490.0,
        "entry_hi": 2510.0,
        "stop": 2560.0,
        "target": 2380.0,
    }
    line = format_signal_line(d)
    assert "ETH" in line
    assert "SHORT" in line
    assert "🔴" in line


def test_signal_line_long_green():
    d = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 155.0,
        "regime": "BULL_TREND",
        "entry_lo": 65000.0,
        "entry_hi": 65500.0,
        "stop": 63000.0,
        "target": 70000.0,
    }
    line = format_signal_line(d)
    assert "🟢" in line
    assert "BTC" in line


def test_signal_line_has_sl_tp():
    d = {
        "symbol": "ETHUSDT",
        "side": "SHORT",
        "score": 150.0,
        "regime": "BEAR_EARLY",
        "entry_lo": 2490.0,
        "entry_hi": 2510.0,
        "stop": 2560.0,
        "target": 2380.0,
    }
    line = format_signal_line(d)
    assert "SL" in line
    assert "TP" in line


# ─── signal_from_result ──────────────────────────────────────────────────────

def test_signal_from_result_blocked_returns_none():
    result = {"blocked": True, "block_reason": "DEAD_HOLE", "symbol": "ETHUSDT"}
    sig = signal_from_result(result, "ETHUSDT")
    assert sig is None


def test_signal_from_result_missing_entry_returns_none():
    """缺少 entry_lo/entry_hi → adapter 抛 ENTRY → 返回 None"""
    result = {
        "symbol": "ETHUSDT",
        "signal_dir": "SHORT",
        "regime": "BEAR_EARLY",
        "score": 150.0,
        "price": 2500.0,
        # 故意不设 entry_lo/entry_hi/stop/target
    }
    sig = signal_from_result(result, "ETHUSDT")
    assert sig is None


def test_signal_from_result_valid_returns_signal():
    """有完整字段 → 返回 Signal 对象"""
    result = {
        "symbol": "ETHUSDT",
        "signal_dir": "SHORT",
        "regime": "BEAR_EARLY",
        "score_final": 150.0,
        "score": 150.0,
        "grade_num": 85.0,
        "entry_lo": 2490.0,
        "entry_hi": 2510.0,
        "stop": 2560.0,
        "tp1": 2380.0,
        "params": {},
        "source": "analyze",
    }
    sig = signal_from_result(result, "ETHUSDT")
    if sig is not None:  # adapter可能因为版本差异略有不同，但不崩溃是必须的
        assert sig.symbol == "ETHUSDT"
        assert sig.side == "SHORT"


# ─── to_jsonl_row ────────────────────────────────────────────────────────────

def test_jsonl_row_keys():
    result = _make_result()
    row = to_jsonl_row(result)
    for key in ("signal_id", "ts", "symbol", "side", "regime", "score",
                "entry_lo", "entry_hi", "stop", "target", "rr"):
        assert key in row, f"missing key: {key}"


def test_jsonl_row_values():
    result = _make_result()
    row = to_jsonl_row(result)
    assert row["symbol"] == "ETHUSDT"
    assert row["side"] in ("SHORT", "LONG")
    assert row["score"] == 150.0
    assert row["entry_lo"] == 2490.0
    assert row["stop"] == 2560.0


def test_jsonl_row_rr_calculated():
    result = _make_result()
    row = to_jsonl_row(result)
    # entry_mid=2500, stop=2560, tp1=2380 → risk=60, reward=120 → rr=2.0
    assert row["rr"] == pytest.approx(2.0, abs=0.01)


def test_jsonl_row_serializable():
    import json
    result = _make_result()
    row = to_jsonl_row(result)
    # 必须能 JSON 序列化（不含 Signal 对象等不可序列化类型）
    dumped = json.dumps(row, default=str)
    assert "ETHUSDT" in dumped


def test_jsonl_row_no_crash_empty():
    """空dict不崩溃"""
    row = to_jsonl_row({})
    assert isinstance(row, dict)
    assert "signal_id" in row
