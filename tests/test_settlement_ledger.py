"""
test_settlement_ledger.py — 验证 SettlementEngine + EquityLedger 三条铁律
2026-09-07 达摩院封印

1. 费用会降低NAV（不是无成本PnL）
2. 同bar止损/止盈冲突 → 记止损（same_bar_priority=stop）
3. entry_ts之前的bar不前瞻（不能用历史K线当未来）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from brahma_os.config import Settings
from brahma_os.contracts import Fill
from brahma_os.costs import CostModel
from brahma_os.ledger import EquityLedger
from brahma_os.settlement import SettlementEngine, SettlementRule


def make_ledger() -> tuple[EquityLedger, CostModel]:
    cfg = Settings(start_nav=10_000.0, taker_fee_bps=4.0, slippage_bps=3.0, funding_default_bps_8h=1.0)
    cost = CostModel(cfg)
    ledger = EquityLedger(settings=cfg, cost=cost)
    return ledger, cost


def make_fill(side: str, price: float, qty: float, ts: float, notional: float) -> Fill:
    fee = notional * 4.0 / 10000
    slip = notional * 3.0 / 10000
    return Fill(
        fill_id=f"test-{int(ts)}",
        intent_id=f"intent-{int(ts)}",
        signal_id="sig-001",
        ts=ts,
        symbol="BTCUSDT",
        side=side,
        qty=qty,
        price=price,
        fee=fee,
        slippage=slip,
        role="ENTRY",
    )


# ── 铁律1：费用会降低NAV ────────────────────────────────────────
def test_fees_reduce_nav():
    """开仓+平仓后，费用必须体现在NAV里。NAV < start_nav when gross_pnl < fees."""
    ledger, cost = make_ledger()
    start = ledger.nav()

    ts = time.time()
    price = 80_000.0
    notional = 1_000.0
    qty = notional / price

    # 开多
    entry_fill = make_fill("LONG", price, qty, ts, notional)
    ledger.apply_entry(entry_fill, stop=78_000.0, target=84_000.0)

    # 平仓：价格微涨0.01%（远小于费用7bps）
    close_price = price * 1.0001
    close_fill = Fill(
        fill_id="close-001", intent_id="intent-close", signal_id="sig-001",
        ts=ts + 3600, symbol="BTCUSDT",
        side="LONG", qty=qty, price=close_price,
        fee=notional * 4.0 / 10000,
        slippage=notional * 3.0 / 10000,
        role="TARGET",
    )
    ledger.apply_exit(entry_fill.fill_id, close_fill, hours_held=1.0, outcome="WIN")

    nav_after = ledger.nav()
    # gross ≈ +$0.08，费用 ≈ -$1.40，NAV应低于起始
    assert nav_after < start, f"费用未体现：start={start:.2f} nav_after={nav_after:.2f}"
    print(f"✅ 铁律1: start={start:.2f} → nav_after={nav_after:.4f} (费用已扣除)")


# ── 铁律2：同bar冲突 → 记止损 ────────────────────────────────────
def test_same_bar_stop_priority():
    """同一根K线同时触及止盈和止损，默认记止损（LOSS）"""
    engine = SettlementEngine(SettlementRule(same_bar_priority="stop"))
    entry_ts = 1000.0
    # 一根bar同时穿越止损（low=77000）和止盈（high=84000）
    bars = [
        (1100.0, 84_100.0, 76_900.0, 80_000.0),  # hit both
    ]
    result = engine.settle(
        side="LONG",
        entry_ts=entry_ts,
        entry=80_000.0,
        stop=77_000.0,
        target=84_000.0,
        bars=bars,
    )
    assert result is not None
    assert result.outcome == "LOSS", f"同bar冲突应记LOSS，得到{result.outcome}"
    assert result.exit_price == 77_000.0
    print(f"✅ 铁律2: 同bar冲突 → {result.outcome} @{result.exit_price}")


# ── 铁律3：entry_ts之前的bar不前瞻 ────────────────────────────────
def test_no_lookahead_before_entry():
    """entry_ts=2000，bar ts=1000（比entry早）不应被结算"""
    engine = SettlementEngine()
    bars = [
        (1000.0, 90_000.0, 60_000.0, 80_000.0),  # 远在entry之前，高低点穿越止盈止损
        (1500.0, 85_000.0, 75_000.0, 80_000.0),  # 还在entry之前
        # 没有entry_ts之后的bar → 应返回None
    ]
    result = engine.settle(
        side="LONG",
        entry_ts=2000.0,
        entry=80_000.0,
        stop=77_000.0,
        target=84_000.0,
        bars=bars,
    )
    assert result is None, f"entry之前的bar不应结算，得到{result}"
    print("✅ 铁律3: entry_ts之前的bar不前瞻 → result=None")


# ── 铁律4：正常WIN路径 ─────────────────────────────────────────────
def test_normal_win():
    """正常多单触及止盈"""
    engine = SettlementEngine()
    entry_ts = 1000.0
    bars = [
        (1100.0, 82_000.0, 79_500.0, 81_000.0),  # 未触及
        (1200.0, 84_100.0, 80_000.0, 84_000.0),  # 触及止盈
    ]
    result = engine.settle("LONG", entry_ts, 80_000.0, stop=77_000.0, target=84_000.0, bars=bars)
    assert result is not None and result.outcome == "WIN"
    assert result.bars_held == 2
    print(f"✅ 铁律4: 正常WIN @{result.exit_price} bars={result.bars_held}")


if __name__ == "__main__":
    test_fees_reduce_nav()
    test_same_bar_stop_priority()
    test_no_lookahead_before_entry()
    test_normal_win()
    print("\n全部4项铁律测试通过 ✅")
