"""
tests/test_reality_models.py
Reality Models 全覆盖测试
设计院 · 2026-07-09
"""
from __future__ import annotations
import math, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from brahma_v6.reality.models import RealityCost
from brahma_v6.reality.fee_model import FeeModel, OrderSide
from brahma_v6.reality.slippage_model import SlippageModel
from brahma_v6.reality.funding_model import FundingModel, Direction
from brahma_v6.reality.impact_model import ImpactModel
from brahma_v6.reality.reality_engine import RealityEngine, MarketContext


# ─── RealityCost ──────────────────────────────────────────────────────────────

def test_reality_cost_total_drag():
    cost = RealityCost(fee_drag=-10.0, slippage_drag=-5.0,
                       funding_drag=-2.0, impact_drag=-3.0)
    assert cost.total_drag == pytest.approx(-20.0)

def test_reality_cost_total_cost_positive():
    cost = RealityCost(fee_drag=-10.0, slippage_drag=-5.0,
                       funding_drag=2.0, impact_drag=-3.0)
    assert cost.total_cost == pytest.approx(abs(-10 - 5 + 2 - 3))

def test_reality_cost_funding_can_be_positive():
    """空头收资金费 → funding_drag > 0 合法。"""
    cost = RealityCost(fee_drag=-5.0, slippage_drag=-2.0,
                       funding_drag=3.0, impact_drag=-1.0)
    cost.validate()   # must not raise

def test_reality_cost_validate_rejects_positive_fee():
    with pytest.raises(ValueError, match="fee_drag"):
        RealityCost(fee_drag=1.0, slippage_drag=0.0,
                    funding_drag=0.0, impact_drag=0.0).validate()

def test_reality_cost_validate_rejects_positive_slippage():
    with pytest.raises(ValueError, match="slippage_drag"):
        RealityCost(fee_drag=0.0, slippage_drag=1.0,
                    funding_drag=0.0, impact_drag=0.0).validate()

def test_reality_cost_validate_rejects_positive_impact():
    with pytest.raises(ValueError, match="impact_drag"):
        RealityCost(fee_drag=0.0, slippage_drag=0.0,
                    funding_drag=0.0, impact_drag=1.0).validate()


# ─── FeeModel ────────────────────────────────────────────────────────────────

def test_fee_reduces_net_pnl():
    """fee_drag 必须为负（成本）。"""
    fee = FeeModel().estimate(notional=100_000)
    assert fee < 0

def test_fee_taker_greater_than_maker():
    fm = FeeModel()
    assert abs(fm.estimate(100_000, OrderSide.TAKER)) > abs(fm.estimate(100_000, OrderSide.MAKER))

def test_fee_scales_with_notional():
    fm = FeeModel()
    assert abs(fm.estimate(200_000)) == pytest.approx(abs(fm.estimate(100_000)) * 2)

def test_fee_zero_notional():
    assert FeeModel().estimate(0) == 0.0

def test_fee_rejects_negative_notional():
    with pytest.raises(ValueError):
        FeeModel().estimate(-1)

def test_fee_one_way_is_half_round_trip():
    fm = FeeModel()
    assert fm.estimate_one_way(100_000) == pytest.approx(fm.estimate(100_000) / 2)


# ─── SlippageModel ────────────────────────────────────────────────────────────

def test_slippage_reduces_net_pnl():
    slip = SlippageModel().estimate(notional=100_000)
    assert slip < 0

def test_slippage_increases_with_notional():
    sm = SlippageModel()
    assert abs(sm.estimate(200_000)) > abs(sm.estimate(100_000))

def test_slippage_increases_with_low_liquidity():
    sm = SlippageModel()
    high_liq = sm.estimate(100_000, avg_daily_volume_usd=1_000_000_000)
    low_liq  = sm.estimate(100_000, avg_daily_volume_usd=5_000_000)   # 明显更低流动性
    assert abs(low_liq) > abs(high_liq)

def test_slippage_capped_at_max():
    sm = SlippageModel(max_slippage_bps=5.0)
    # 极大订单应触及上限
    cost = sm.estimate(notional=1_000_000_000, avg_daily_volume_usd=1_000_000)
    max_possible = -(1_000_000_000 * 5.0 / 10_000)
    assert cost >= max_possible   # 不超过上限

def test_slippage_by_bps():
    sm = SlippageModel()
    cost = sm.estimate_by_bps(100_000, 4.0)
    assert cost == pytest.approx(-(100_000 * 4.0 / 10_000))


# ─── FundingModel ─────────────────────────────────────────────────────────────

def test_funding_long_is_negative():
    """多头付资金费 → funding_drag < 0。"""
    fm = FundingModel()
    drag = fm.estimate(100_000, Direction.LONG, holding_hours=8.0, funding_rate=0.0001)
    assert drag < 0

def test_funding_short_is_positive():
    """空头收资金费（正资金费率环境）→ funding_drag > 0。"""
    fm = FundingModel()
    drag = fm.estimate(100_000, Direction.SHORT, holding_hours=8.0, funding_rate=0.0001)
    assert drag > 0

def test_funding_can_be_positive_or_negative():
    """资金费率可正可负（负资金费率时多头收益）。"""
    fm = FundingModel()
    # 负资金费率：空头付，多头收
    drag_long  = fm.estimate(100_000, Direction.LONG,  8.0, funding_rate=-0.0001)
    drag_short = fm.estimate(100_000, Direction.SHORT, 8.0, funding_rate=-0.0001)
    assert drag_long > 0    # 多头收益
    assert drag_short < 0   # 空头成本

def test_funding_scales_with_holding_time():
    fm = FundingModel()
    d8  = fm.estimate(100_000, Direction.LONG, holding_hours=8.0)
    d16 = fm.estimate(100_000, Direction.LONG, holding_hours=16.0)
    assert abs(d16) == pytest.approx(abs(d8) * 2)

def test_funding_zero_holding():
    fm = FundingModel()
    assert fm.estimate(100_000, Direction.LONG, 0.0) == 0.0


# ─── ImpactModel ──────────────────────────────────────────────────────────────

def test_impact_reduces_net_pnl():
    drag = ImpactModel().estimate(notional=1_000_000)
    assert drag < 0

def test_impact_increases_with_notional():
    im = ImpactModel()
    assert abs(im.estimate(2_000_000)) > abs(im.estimate(1_000_000))

def test_impact_increases_with_low_liquidity():
    """流动性越低，冲击越大。"""
    im = ImpactModel()
    high = im.estimate(1_000_000, avg_daily_volume_usd=1_000_000_000)
    low  = im.estimate(1_000_000, avg_daily_volume_usd=10_000_000)
    assert abs(low) > abs(high)

def test_impact_sqrt_scaling():
    """冲击与 sqrt(notional/adv) 成比例。"""
    im = ImpactModel(impact_factor=10.0, max_impact_bps=999.0)
    adv = 1_000_000_000
    # 单边: impact_bps = 10 * sqrt(n/adv)
    # n=4M vs n=1M: sqrt(4)/sqrt(1) = 2 → bps 比 = 2
    # 但名义也受 4x，所以成本比 = 4 * 2 = 8（已测得实际ratio=8）
    d1 = im.estimate_single_leg(1_000_000, adv)
    d4 = im.estimate_single_leg(4_000_000, adv)
    actual_ratio = abs(d4) / abs(d1)
    # sqrt(4) * 4 = 8
    assert actual_ratio == pytest.approx(8.0, rel=0.01)

def test_impact_zero_notional():
    assert ImpactModel().estimate(0) == 0.0

def test_impact_single_leg_half_of_round_trip():
    im = ImpactModel()
    assert im.estimate_single_leg(1_000_000) == pytest.approx(im.estimate(1_000_000) / 2)


# ─── RealityEngine ───────────────────────────────────────────────────────────

def test_engine_all_drags_negative_except_funding():
    engine = RealityEngine()
    cost = engine.estimate(100_000)
    assert cost.fee_drag < 0
    assert cost.slippage_drag < 0
    assert cost.impact_drag < 0
    # funding 可正可负（默认 LONG → 负）

def test_engine_total_drag_negative_for_long():
    cost = RealityEngine().estimate(100_000)
    assert cost.total_drag < 0

def test_engine_net_pnl_less_than_gross():
    engine = RealityEngine()
    gross = 500.0
    attr = engine.build_attribution(gross_pnl=gross, notional=100_000)
    assert attr.net_pnl < gross

def test_engine_attribution_is_balanced():
    """build_attribution 必须通过 PnLAttribution.validate()。"""
    engine = RealityEngine()
    attr = engine.build_attribution(gross_pnl=1000.0, notional=100_000)
    attr.validate()   # must not raise

def test_engine_short_funding_positive():
    """空头在正资金费率环境收益 → total_drag 可能比纯多头小。"""
    ctx_long  = MarketContext(direction=Direction.LONG,  funding_rate=0.001)
    ctx_short = MarketContext(direction=Direction.SHORT, funding_rate=0.001)
    engine = RealityEngine()
    long_drag  = engine.estimate(100_000, ctx_long).total_drag
    short_drag = engine.estimate(100_000, ctx_short).total_drag
    # 空头收资金费 → 总成本更低（绝对值更小）
    assert abs(short_drag) < abs(long_drag)

def test_engine_rejects_negative_notional():
    with pytest.raises(ValueError):
        RealityEngine().estimate(-1)
