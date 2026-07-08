"""
tests/test_brahma_v6_final.py — Phase 5 + 全链路接入 测试
设计院 × 顶级评估v6.0 | 2026-07-08
"""
import pytest
import sys
import time
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from brahma_v6.paper.paper_forward import (
    PaperTrade, PaperPortfolio, PaperExecutor,
    ForwardValidator, DriftMonitor, _compute_net_pnl
)
from brahma_v6.apps.runner_bridge import enrich_with_v6, _V6_OK


# ══════════════════════════════════════════════════════
#  Phase 5: Paper Forward
# ══════════════════════════════════════════════════════
class TestPaperTrade:

    def test_open_trade(self):
        t = PaperTrade(symbol="BTCUSDT", direction="LONG",
                       entry_price=62000.0, stop_loss=60500.0,
                       quantity=0.001, leverage=3)
        assert t.status == "OPEN"
        assert t.trade_id

    def test_close_trade_profit(self):
        t = PaperTrade(symbol="BTCUSDT", direction="LONG",
                       entry_price=62000.0, stop_loss=60500.0,
                       take_profit=64000.0, quantity=0.001, leverage=3)
        t.nav_at_entry = 100.0
        t.close(64000.0, "TP1")
        assert t.status == "CLOSED"
        assert t.net_pnl > 0           # 净盈利
        assert t.gross_pnl > t.net_pnl # 毛>净（有成本）
        assert t.fee > 0
        assert t.slippage > 0

    def test_close_trade_loss(self):
        t = PaperTrade(symbol="ETHUSDT", direction="LONG",
                       entry_price=1720.0, stop_loss=1680.0,
                       quantity=0.021, leverage=3)
        t.nav_at_entry = 100.0
        t.close(1680.0, "SL")
        assert t.status == "CLOSED"
        assert t.net_pnl < 0

    def test_short_trade_profit(self):
        t = PaperTrade(symbol="BTCUSDT", direction="SHORT",
                       entry_price=63000.0, stop_loss=64500.0,
                       take_profit=61000.0, quantity=0.001, leverage=3)
        t.nav_at_entry = 100.0
        t.close(61000.0, "TP1")
        assert t.net_pnl > 0

    def test_holding_hours_computed(self):
        t = PaperTrade(symbol="BTCUSDT", direction="LONG",
                       entry_price=62000.0, stop_loss=60500.0, quantity=0.001)
        t.ts_open = time.time() - 7200  # 2小时前开仓
        t.nav_at_entry = 100.0
        t.close(63000.0, "TP1")
        assert 1.8 <= t.holding_hours <= 2.2


class TestPaperPortfolio:

    def test_nav_updates_on_close(self, tmp_path):
        port = PaperPortfolio(initial_nav=100.0)
        port._log_file = tmp_path / "trades.jsonl"
        exec_ = PaperExecutor(port)
        trade = exec_.execute_signal("BTCUSDT","LONG",162.0,"BULL_TREND",
                                      62000.0, 60500.0, 64000.0, 0.8, 3)
        initial_nav = port.nav
        port.close_trade(trade.trade_id, 64000.0, "TP1")
        # NAV应该变化
        assert port.nav != initial_nav

    def test_drawdown_calculation(self, tmp_path):
        port = PaperPortfolio(initial_nav=100.0)
        port._log_file = tmp_path / "trades.jsonl"
        port.peak_nav = 105.0
        port.nav = 100.0
        dd = port.drawdown()
        assert abs(dd - 4.76) < 0.1

    def test_summary_with_trades(self, tmp_path):
        port = PaperPortfolio(initial_nav=100.0)
        port._log_file = tmp_path / "trades.jsonl"
        exec_ = PaperExecutor(port)
        for i in range(5):
            t = exec_.execute_signal("BTCUSDT","LONG",160.0,"BULL_TREND",
                                      62000.0, 60500.0, 64000.0, 0.5, 3)
            port.close_trade(t.trade_id, 64000.0 if i % 2 == 0 else 60500.0, "TP1" if i%2==0 else "SL")
        s = port.summary()
        assert s["total_trades"] == 5
        assert 0 <= s["win_rate"] <= 1
        assert "profit_factor" in s

    def test_simulate_exit_sl(self, tmp_path):
        port = PaperPortfolio(initial_nav=100.0)
        port._log_file = tmp_path / "trades.jsonl"
        exec_ = PaperExecutor(port)
        trade = exec_.execute_signal("ETHUSDT","LONG",155.0,"BULL_TREND",
                                      1720.0, 1680.0, 1800.0, 0.5, 3)
        closed = exec_.simulate_exit(trade, current_price=1675.0)
        assert closed is not None
        assert closed.exit_reason == "SL"

    def test_simulate_exit_tp(self, tmp_path):
        port = PaperPortfolio(initial_nav=100.0)
        port._log_file = tmp_path / "trades.jsonl"
        exec_ = PaperExecutor(port)
        trade = exec_.execute_signal("ETHUSDT","LONG",162.0,"BULL_TREND",
                                      1720.0, 1680.0, 1800.0, 0.6, 3)
        closed = exec_.simulate_exit(trade, current_price=1810.0)
        assert closed is not None
        assert closed.exit_reason == "TP1"


class TestForwardValidator:

    def _build_portfolio(self, tmp_path, n_trades=10, wr=0.65):
        import random
        port = PaperPortfolio(initial_nav=100.0)
        port._log_file = tmp_path / "trades.jsonl"
        exec_ = PaperExecutor(port)
        for i in range(n_trades):
            t = exec_.execute_signal("BTCUSDT","LONG",162.0,"BULL_TREND",
                                      62000.0, 60500.0, 64500.0, 0.8, 3)
            win = random.random() < wr
            port.close_trade(t.trade_id, 64500.0 if win else 60500.0, "TP1" if win else "SL")
        return port

    def test_validator_insufficient_trades(self, tmp_path):
        port = self._build_portfolio(tmp_path, n_trades=5)
        v = ForwardValidator(port)
        report = v.validate()
        assert report["checks"]["min_trades"]["pass"] is False
        assert "IN_PROGRESS" in report["verdict"] or "NEEDS" in report["verdict"]

    def test_validator_fields(self, tmp_path):
        port = self._build_portfolio(tmp_path, n_trades=10)
        v = ForwardValidator(port)
        report = v.validate()
        assert "summary" in report
        assert "checks" in report
        assert "by_symbol" in report
        assert "by_regime" in report
        assert "by_direction" in report

    def test_cost_model(self):
        cost = _compute_net_pnl("BTCUSDT","LONG",62000.0,64000.0,0.001,3,8.0)
        assert cost["gross_pnl"] > 0
        assert cost["net_pnl"] < cost["gross_pnl"]
        assert cost["fee"] > 0

    def test_drift_monitor(self, tmp_path):
        port = self._build_portfolio(tmp_path, n_trades=20, wr=0.62)
        monitor = DriftMonitor(backtest_wr=0.62, backtest_ev=0.5)
        drift = monitor.check_drift(port)
        assert "wr_drift_pct" in drift
        assert "overall_ok" in drift
        assert drift["wr_drift_pct"] >= 0


# ══════════════════════════════════════════════════════
#  全链路接入测试
# ══════════════════════════════════════════════════════
class TestRunnerBridge:

    def _mock_result(self, score=162.0, blocked=False, valid=True):
        return {
            "symbol": "BTCUSDT",
            "signal_dir": "LONG",
            "direction": "LONG",
            "score": score,
            "regime": "BULL_TREND",
            "grade": "🔴神级",
            "blocked": blocked,
            "valid_signal": valid,
            "action": "ENTER_FULL",
            "price": 62000.0,
            "stop_loss": 60500.0,
            "tp1": 64500.0,
            "rsi_1h": 65.0,
            "confidence": 0.80,
            "bull_prob": 0.65, "bear_prob": 0.20, "chop_prob": 0.15,
            "trace_id": str(uuid.uuid4()),
        }

    def test_enrich_adds_v6(self):
        assert _V6_OK, "v6组件应已加载"
        result = self._mock_result()
        enriched = enrich_with_v6(result, account_nav=100.0, skip_live_checks=True)
        assert "_v6" in enriched
        assert enriched["_v6"]["available"] is True

    def test_enrich_has_trace_id(self):
        result = self._mock_result()
        enriched = enrich_with_v6(result, skip_live_checks=True)
        assert "trace_id" in enriched["_v6"]

    def test_enrich_regime_v2(self):
        result = self._mock_result()
        enriched = enrich_with_v6(result, skip_live_checks=True)
        rv2 = enriched["_v6"].get("regime_v2", {})
        assert "entropy" in rv2
        assert "transition_risk" in rv2
        assert 0 <= rv2["entropy"] <= 1

    def test_enrich_risk_kernel_approve(self):
        result = self._mock_result(score=162.0, blocked=False)
        enriched = enrich_with_v6(result, skip_live_checks=True)
        rk = enriched["_v6"].get("risk_kernel", {})
        assert rk.get("decision") in ("APPROVE", "REDUCE")
        assert rk.get("final_size_nav", 0) > 0

    def test_enrich_risk_kernel_blocked_signal(self):
        result = self._mock_result(score=85.0, blocked=True)
        enriched = enrich_with_v6(result, skip_live_checks=True)
        # blocked信号不走kernel
        rk = enriched["_v6"].get("risk_kernel", {})
        assert rk == {} or rk.get("decision") == "BLOCKED"

    def test_enrich_dharma2_ev(self):
        result = self._mock_result()
        enriched = enrich_with_v6(result, skip_live_checks=True)
        ev = enriched["_v6"].get("dharma2_ev", {})
        assert "net_ev_pct" in ev
        assert "profit_factor" in ev
        assert "is_ev_positive" in ev

    def test_original_fields_preserved(self):
        result = self._mock_result()
        enriched = enrich_with_v6(result, skip_live_checks=True)
        # 原始字段必须保持不变
        assert enriched["score"] == 162.0
        assert enriched["regime"] == "BULL_TREND"
        assert enriched["symbol"] == "BTCUSDT"

    def test_enrich_does_not_crash_on_bad_input(self):
        bad = {"symbol": "", "score": None, "blocked": True}
        result = enrich_with_v6(bad, skip_live_checks=True)
        assert "_v6" in result  # 不崩溃


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
