"""
tests/test_brahma_v6.py — brahma_v6全链路集成测试
设计院 × 顶级评估v6.0建议 2026-07-08

覆盖：
- 事件Schema + trace_id继承
- 12层Risk Kernel决策
- OrderIntent前置执行层
- Dharma2成本模型EV计算
"""
import pytest
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from brahma_v6.schemas.events import (
    make_signal_event, SignalScoredEvent, EventSubject,
    RiskDecisionEvent, OrderIntentEvent, BrahmaEvent
)
from brahma_v6.risk.kernel import RiskKernel
from brahma_v6.execution.order_intent import (
    OrderIntentProcessor, OrderState, AlgoSelector, PreTradeRisk
)
from brahma_v6.dharma2.cost_model import compute_cost, compute_ev


# ══════════════════════════════════════════════════════
#  事件Schema测试
# ══════════════════════════════════════════════════════
class TestEventSchema:

    def test_signal_event_has_trace_id(self):
        sig = make_signal_event("BTCUSDT", "LONG", 140.0, 158.0, "BULL_TREND", "🔴神级", False)
        assert sig.trace_id
        assert len(sig.trace_id) == 36  # UUID格式
        assert sig.event_id != sig.trace_id

    def test_child_event_inherits_trace_id(self):
        sig = make_signal_event("BTCUSDT", "LONG", 140.0, 158.0, "BULL_TREND", "🔴神级", False)
        child = sig.child(EventSubject.RISK_DECISION, source="risk_kernel")
        assert child.trace_id == sig.trace_id       # trace_id继承
        assert child.parent_event_id == sig.event_id  # parent正确
        assert child.event_id != sig.event_id        # 新event_id

    def test_signal_event_fields(self):
        sig = make_signal_event(
            "ETHUSDT", "SHORT", 120.0, 145.0, "BEAR_TREND", "🟠极强", False,
            regime_bonus=12.0, noise_penalty=-5.0, confidence=0.75,
            top_pos=[["funding", 8.4]], top_neg=[["liq_wall", -5.0]],
            adjustment_trace=["BEAR_SHORT_BONUS"],
        )
        assert sig.regime_bonus == 12.0
        assert sig.confidence == 0.75
        assert len(sig.top_positive_features) == 1
        assert sig.adjustment_trace == ["BEAR_SHORT_BONUS"]
        assert sig.valid_after_adj is False  # 145 < 155

    def test_valid_after_adj_true(self):
        sig = make_signal_event("BTCUSDT", "LONG", 150.0, 162.0, "BULL_TREND", "🔴神级", False)
        assert sig.valid_after_adj is True  # 162 >= 155

    def test_to_dict_serializable(self):
        import json
        sig = make_signal_event("BTCUSDT", "LONG", 140.0, 160.0, "BULL_TREND", "🔴神级", False)
        d = sig.to_dict()
        json.dumps(d)  # 不抛异常即通过


# ══════════════════════════════════════════════════════
#  12层Risk Kernel测试
# ══════════════════════════════════════════════════════
class TestRiskKernel:

    @pytest.fixture
    def kernel(self):
        return RiskKernel()

    def _sig(self, symbol="BTCUSDT", direction="LONG", score=160.0, regime="BULL_TREND", blocked=False):
        return make_signal_event(symbol, direction, score-10, score, regime, "🔴神级", blocked)

    def test_approve_high_score(self, kernel):
        sig = self._sig(score=162.0, regime="BULL_TREND")
        d = kernel.evaluate(sig, skip_live_checks=True)
        assert d.decision == "APPROVE"
        assert d.final_size_nav > 0
        assert d.max_leverage >= 3

    def test_block_regime_death_zone(self, kernel):
        sig = self._sig(direction="LONG", score=165.0, regime="BEAR_TREND")
        d = kernel.evaluate(sig, skip_live_checks=True)
        assert d.decision == "BLOCKED"
        assert "L3_REGIME_DEATH_ZONE" in d.blocked_layers

    def test_block_low_score(self, kernel):
        sig = self._sig(score=95.0)
        d = kernel.evaluate(sig, skip_live_checks=True)
        assert d.decision == "BLOCKED"
        assert "L4_SCORE_TOO_LOW" in d.blocked_layers

    def test_block_stale_signal(self, kernel):
        sig = self._sig(score=165.0)
        sig.ts_event = time.time() - 300  # 5分钟前的信号
        d = kernel.evaluate(sig, skip_live_checks=True)
        assert "L1_DATA_STALE" in d.blocked_layers

    def test_trace_id_preserved(self, kernel):
        sig = self._sig(score=165.0)
        d = kernel.evaluate(sig, skip_live_checks=True)
        assert d.trace_id == sig.trace_id   # trace_id贯穿全链

    def test_size_scales_with_score(self, kernel):
        sig_high = self._sig(score=165.0)
        sig_low = self._sig(score=140.0)
        d_high = kernel.evaluate(sig_high, skip_live_checks=True)
        d_low = kernel.evaluate(sig_low, skip_live_checks=True)
        assert d_high.final_size_nav >= d_low.final_size_nav

    def test_blocked_returns_zero_size(self, kernel):
        sig = self._sig(direction="LONG", regime="BEAR_TREND")
        d = kernel.evaluate(sig, skip_live_checks=True)
        assert d.final_size_nav == 0.0
        assert d.max_leverage == 1

    def test_chop_high_blocked_both(self, kernel):
        for direction in ["LONG", "SHORT"]:
            sig = self._sig(direction=direction, score=165.0, regime="CHOP_HIGH")
            d = kernel.evaluate(sig, skip_live_checks=True)
            assert d.decision == "BLOCKED"


# ══════════════════════════════════════════════════════
#  OrderIntent前置执行层测试
# ══════════════════════════════════════════════════════
class TestOrderIntent:

    @pytest.fixture
    def approved_risk(self):
        sig = make_signal_event("BTCUSDT", "LONG", 150.0, 162.0, "BULL_TREND", "🔴神级", False)
        sig.payload["direction"] = "LONG"
        kernel = RiskKernel()
        risk = kernel.evaluate(sig, skip_live_checks=True)
        # 手动注入direction到payload
        risk.payload["direction"] = "LONG"
        return risk

    def test_creates_record_from_approved(self, approved_risk):
        proc = OrderIntentProcessor()
        record = proc.from_risk_decision(approved_risk, 62000.0, 60500.0, 0.001)
        assert record is not None
        assert record.state == OrderState.PENDING
        assert record.symbol == "BTCUSDT"
        assert record.trace_id == approved_risk.trace_id

    def test_blocked_returns_none(self):
        sig = make_signal_event("BTCUSDT", "LONG", 90.0, 92.0, "BEAR_TREND", "🔴神级", False)
        kernel = RiskKernel()
        risk = kernel.evaluate(sig, skip_live_checks=True)
        assert risk.decision == "BLOCKED"
        proc = OrderIntentProcessor()
        record = proc.from_risk_decision(risk, 62000.0, 60500.0, 0.001)
        assert record is None

    def test_pre_trade_pass(self, approved_risk):
        proc = OrderIntentProcessor()
        record = proc.from_risk_decision(approved_risk, 62000.0, 60500.0, 0.001)
        record.direction = "LONG"
        ok = proc.pre_trade_check(record, current_price=62000.0)
        assert ok
        assert record.state == OrderState.PRE_TRADE_CHECK

    def test_pre_trade_fail_bad_sl(self, approved_risk):
        proc = OrderIntentProcessor()
        record = proc.from_risk_decision(approved_risk, 62000.0, 63000.0, 0.001)  # SL高于入场
        record.direction = "LONG"
        ok = proc.pre_trade_check(record, current_price=62000.0)
        assert not ok
        assert record.state == OrderState.LOCAL_DENIED

    def test_fill_updates_state(self, approved_risk):
        proc = OrderIntentProcessor()
        record = proc.from_risk_decision(approved_risk, 62000.0, 60500.0, 0.001)
        record.direction = "LONG"
        proc.mark_submitted(record, "EX999")
        fill_evt = proc.mark_filled(record, 62050.0, 0.001, 0.062)
        assert record.state == OrderState.FILLED
        assert record.fill_price == 62050.0
        quality = record.execution_quality()
        assert quality["fill_rate"] == 1.0
        assert "slippage_pct" in quality

    def test_algo_selector_btc_trend(self):
        sig = make_signal_event("BTCUSDT", "LONG", 150.0, 162.0, "BULL_TREND", "🔴神级", False)
        kernel = RiskKernel()
        risk = kernel.evaluate(sig, skip_live_checks=True)
        risk.payload["direction"] = "LONG"
        proc = OrderIntentProcessor()
        from brahma_v6.schemas.events import make_order_intent
        intent = make_order_intent(risk, 0.001, 62000.0, 60500.0)
        algo = AlgoSelector.select(intent, regime="BULL_TREND")
        assert algo == "POST_ONLY_LIMIT"

    def test_algo_pump_returns_limit_only(self):
        sig = make_signal_event("SOLUSDT", "LONG", 140.0, 155.0, "BEAR_RECOVERY", "🔴神级", False)
        kernel = RiskKernel()
        risk = kernel.evaluate(sig, skip_live_checks=True)
        risk.payload["direction"] = "LONG"
        from brahma_v6.schemas.events import make_order_intent
        intent = make_order_intent(risk, 0.01, 150.0, 145.0)
        algo = AlgoSelector.select(intent, regime="BEAR_RECOVERY", is_pump=True)
        assert algo == "LIMIT_ONLY"


# ══════════════════════════════════════════════════════
#  Dharma2 成本模型测试
# ══════════════════════════════════════════════════════
class TestDharma2CostModel:

    def test_cost_reduces_gross_pnl(self):
        cost = compute_cost("BTCUSDT", "LONG", 2.0, 4.0, leverage=3)
        assert cost.net_pnl_pct < cost.gross_pnl_pct
        assert cost.fee_drag_pct > 0
        assert cost.slippage_drag_pct > 0
        assert cost.funding_drag_pct > 0

    def test_market_order_higher_cost(self):
        limit = compute_cost("BTCUSDT", "LONG", 2.0, 4.0, use_market_order=False)
        market = compute_cost("BTCUSDT", "LONG", 2.0, 4.0, use_market_order=True)
        total_limit = limit.fee_drag_pct + limit.slippage_drag_pct
        total_market = market.fee_drag_pct + market.slippage_drag_pct
        assert total_market > total_limit

    def test_longer_holding_higher_funding(self):
        short = compute_cost("BTCUSDT", "LONG", 2.0, 4.0)
        long_ = compute_cost("BTCUSDT", "LONG", 2.0, 48.0)
        assert long_.funding_drag_pct > short.funding_drag_pct

    def test_ev_positive_high_wr(self):
        ev = compute_ev("BTCUSDT", "LONG", 0.70, 2.5, 2.0, 4.0, 3)
        assert ev["is_ev_positive"]
        assert ev["net_ev_pct"] > 0
        assert ev["profit_factor"] > 1.0

    def test_ev_negative_low_wr(self):
        ev = compute_ev("BTCUSDT", "LONG", 0.35, 1.0, 3.0, 4.0, 3)
        assert not ev["is_ev_positive"]

    def test_breakeven_wr_calculation(self):
        ev = compute_ev("ETHUSDT", "SHORT", 0.60, 3.0, 2.5, 6.0, 3)
        assert 0.3 < ev["breakeven_wr"] < 0.7  # 理性范围内

    def test_sol_higher_cost_than_btc(self):
        btc = compute_cost("BTCUSDT", "LONG", 2.0, 4.0)
        sol = compute_cost("SOLUSDT", "LONG", 2.0, 4.0)
        btc_total = btc.fee_drag_pct + btc.slippage_drag_pct
        sol_total = sol.fee_drag_pct + sol.slippage_drag_pct
        assert sol_total > btc_total  # SOL流动性更差，成本更高


# ══════════════════════════════════════════════════════
#  全链路集成测试
# ══════════════════════════════════════════════════════
class TestFullPipeline:

    def test_signal_to_order_trace_preserved(self):
        """信号→风控→订单意图，trace_id全程一致"""
        sig = make_signal_event(
            "BTCUSDT", "LONG", 148.0, 163.0, "BULL_TREND", "🔴神级", False,
            confidence=0.80,
        )
        sig.payload["direction"] = "LONG"

        kernel = RiskKernel()
        risk = kernel.evaluate(sig, skip_live_checks=True)
        assert risk.trace_id == sig.trace_id

        if risk.decision != "BLOCKED":
            risk.payload["direction"] = "LONG"
            proc = OrderIntentProcessor()
            record = proc.from_risk_decision(risk, 62000.0, 60500.0, 0.001)
            if record:
                assert record.trace_id == sig.trace_id  # ✅ 全链路trace_id一致

    def test_blocked_signal_no_order(self):
        """死穴信号不产生订单意图"""
        sig = make_signal_event(
            "ETHUSDT", "LONG", 160.0, 168.0, "BEAR_TREND", "🔴神级", False,
        )
        kernel = RiskKernel()
        risk = kernel.evaluate(sig, skip_live_checks=True)
        assert risk.decision == "BLOCKED"

        proc = OrderIntentProcessor()
        risk.payload["direction"] = "LONG"
        record = proc.from_risk_decision(risk, 1700.0, 1650.0, 0.021)
        assert record is None  # ✅ 无订单产生

    def test_ev_gate_before_order(self):
        """EV负期望的信号应被识别，不应下单"""
        ev = compute_ev("SOLUSDT", "LONG", 0.40, 1.5, 3.0, 8.0, leverage=5)
        assert not ev["is_ev_positive"]
        # EV负期望 → 对应score通常低 → Risk Kernel应阻止


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
