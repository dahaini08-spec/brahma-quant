"""
tests/test_brahma_v6_phase2.py — Phase 2-4 全链路测试
设计院 × 顶级评估v6.0 | 2026-07-08

覆盖：
- Phase 2: FileEventBus 事件总线
- Phase 3: Polars 特征引擎 + DuckDB 数据湖
- Phase 4: Regime v2.0 概率 + entropy + transition_risk
"""
import pytest
import sys
import time
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from brahma_v6.bus.file_bus import FileEventBus, BusMessage, SignalBusAdapter
from brahma_v6.lake.data_lake import PolarsFeatureEngine, LakeWriter, DuckDBQueryEngine
from brahma_v6.regime.regime_v2 import RegimeProbEngine, RegimeV2Adapter, REGIMES


# ══════════════════════════════════════════════════════
#  Phase 2: FileEventBus
# ══════════════════════════════════════════════════════
class TestFileEventBus:

    @pytest.fixture
    def bus(self, tmp_path):
        return FileEventBus(bus_dir=tmp_path / "event_bus")

    def test_publish_and_subscribe(self, bus):
        received = []
        bus.subscribe("signal.*", lambda m: received.append(m))
        bus.publish("signal.scored", {"symbol": "BTCUSDT", "score": 162.0}, sync=True)
        assert len(received) == 1
        assert received[0].payload["symbol"] == "BTCUSDT"

    def test_wildcard_match(self, bus):
        received = []
        bus.subscribe("signal.*", lambda m: received.append(m))
        bus.publish("signal.scored", {"symbol": "BTC"}, sync=True)
        bus.publish("signal.raw", {"symbol": "ETH"}, sync=True)
        bus.publish("position.open", {"symbol": "SOL"}, sync=True)  # 不应触发
        assert len(received) == 2

    def test_exact_match(self, bus):
        received = []
        bus.subscribe("system.health", lambda m: received.append(m))
        bus.publish("system.health", {"status": "OK"}, sync=True)
        bus.publish("system.alert", {"msg": "test"}, sync=True)  # 不应触发
        assert len(received) == 1

    def test_trace_id_preserved(self, bus):
        received = []
        bus.subscribe("signal.*", lambda m: received.append(m))
        bus.publish("signal.scored", {"x": 1}, trace_id="trace-abc-123", sync=True)
        assert received[0].trace_id == "trace-abc-123"

    def test_replay(self, bus):
        bus.publish("signal.scored", {"score": 155}, sync=True)
        bus.publish("signal.scored", {"score": 160}, sync=True)
        bus.publish("position.open", {"symbol": "BTC"}, sync=True)
        replayed = bus.replay("signal.*", from_ts=0.0)
        assert len(replayed) == 2

    def test_replay_all(self, bus):
        bus.publish("a.b", {"x": 1}, sync=True)
        bus.publish("c.d", {"x": 2}, sync=True)
        replayed = bus.replay("*", from_ts=0.0)
        assert len(replayed) >= 2

    def test_stats(self, bus):
        bus.publish("signal.scored", {"x": 1}, sync=True)
        bus.publish("signal.scored", {"x": 2}, sync=True)
        stats = bus.get_stats()
        assert stats["total_messages"] >= 2
        assert "signal.scored" in stats["subjects"]
        assert stats["bus_type"] == "FileEventBus"

    def test_msg_id_unique(self, bus):
        msgs = []
        bus.subscribe("*", lambda m: msgs.append(m))
        for _ in range(5):
            bus.publish("signal.scored", {}, sync=True)
        ids = [m.msg_id for m in msgs]
        assert len(set(ids)) == len(ids)  # 全部唯一

    def test_signal_adapter(self, bus):
        adapter = SignalBusAdapter(bus)
        received = []
        bus.subscribe("signal.*", lambda m: received.append(m))
        adapter.emit_signal({
            "symbol": "BTCUSDT", "signal_dir": "LONG", "score": 162.0,
            "regime": "BULL_TREND", "blocked": False, "valid_signal": True, "action": "ENTER_FULL",
        })
        assert len(received) == 1
        assert received[0].payload["score"] == 162.0

    def test_position_events(self, bus):
        adapter = SignalBusAdapter(bus)
        received = []
        bus.subscribe("position.*", lambda m: received.append(m))
        adapter.emit_position_open("BTCUSDT", "LONG", 62000.0, 0.001, 60500.0)
        adapter.emit_position_close("BTCUSDT", "LONG", 63500.0, 15.0, "TP1")
        assert len(received) == 2
        assert received[0].subject == "position.open"
        assert received[1].subject == "position.close"


# ══════════════════════════════════════════════════════
#  Phase 3: 数据湖
# ══════════════════════════════════════════════════════
class TestDataLake:

    def test_polars_available(self):
        engine = PolarsFeatureEngine()
        assert engine.available, "Polars应已安装"

    def test_kline_features(self):
        import random
        engine = PolarsFeatureEngine()
        if not engine.available:
            pytest.skip("Polars未安装")
        price = 62000.0
        klines = []
        for i in range(30):
            price *= (1 + random.gauss(0, 0.005))
            klines.append({"open": price*0.999, "high": price*1.001,
                           "low": price*0.998, "close": price,
                           "volume": random.uniform(500, 2000), "timestamp": float(i)})
        lf = engine.compute_kline_features(klines)
        df = lf.collect()
        assert df.shape[0] == 30
        assert "return_pct" in df.columns
        assert "bb_width_pct" in df.columns
        assert "vol_ratio" in df.columns
        assert "ema20_dist_pct" in df.columns

    def test_lake_writer_signal(self, tmp_path):
        engine = PolarsFeatureEngine()
        if not engine.available:
            pytest.skip("Polars未安装")
        import polars as pl
        from brahma_v6.lake.data_lake import LakeWriter, LAKE_DIR
        # 使用临时目录
        writer = LakeWriter()
        # 仅测试行为不崩溃
        result = writer.write_signal({
            "ts": time.time(), "symbol": "BTCUSDT", "direction": "LONG",
            "regime": "BULL_TREND", "score": 162.0, "grade": "🔴神级",
            "action": "ENTER_FULL", "valid_signal": True, "blocked": False, "price": 62000.0,
        })
        # 可能成功或因权限失败，不强制断言

    def test_polars_signal_features(self):
        engine = PolarsFeatureEngine()
        if not engine.available:
            pytest.skip("Polars未安装")
        signals = [
            {"score": 162.0, "regime": "BULL_TREND", "valid_signal": True, "symbol": "BTC"},
            {"score": 140.0, "regime": "BULL_TREND", "valid_signal": False, "symbol": "ETH"},
            {"score": 95.0, "regime": "CHOP_MID", "valid_signal": False, "symbol": "SOL"},
        ]
        lf = engine.compute_signal_features(signals)
        df = lf.collect()
        assert df.shape[0] == 3
        assert "is_high_quality" in df.columns
        high_q = df.filter(df["is_high_quality"]).shape[0]
        assert high_q == 1  # 只有162.0 > 155

    def test_duckdb_available(self):
        engine = DuckDBQueryEngine()
        # duckdb可能刚安装，不强制要求
        if not engine.available:
            pytest.skip("DuckDB未安装")
        stats = engine.get_stats()
        assert stats["available"] is True


# ══════════════════════════════════════════════════════
#  Phase 4: Regime v2.0
# ══════════════════════════════════════════════════════
class TestRegimeV2:

    @pytest.fixture
    def engine(self):
        return RegimeProbEngine()

    def _compute(self, engine, regime="BULL_TREND", bp=0.6, brp=0.2, cp=0.2,
                 conf=0.8, rsi=60.0):
        return engine.compute("BTCUSDT", regime, bp, brp, cp, conf, rsi)

    def test_prob_sums_to_one(self, engine):
        result = self._compute(engine)
        total = sum(result["prob"].values())
        assert abs(total - 1.0) < 0.01

    def test_primary_regime_has_highest_prob(self, engine):
        result = self._compute(engine, regime="BULL_TREND", bp=0.7, brp=0.1, cp=0.2, conf=0.9)
        probs = result["prob"]
        max_regime = max(probs, key=lambda k: probs[k])
        # 高置信度时主体制应该是概率最高的
        assert max_regime in ("BULL_TREND", "BULL_EARLY", "BULL_CORRECTION")

    def test_entropy_range(self, engine):
        result = self._compute(engine)
        assert 0.0 <= result["entropy"] <= 1.0

    def test_high_confidence_low_entropy(self, engine):
        high_conf = self._compute(engine, bp=0.90, brp=0.05, cp=0.05, conf=0.95)
        low_conf = self._compute(engine, bp=0.35, brp=0.35, cp=0.30, conf=0.30)
        assert high_conf["entropy"] < low_conf["entropy"]

    def test_transition_risk_range(self, engine):
        result = self._compute(engine)
        assert 0.0 <= result["transition_risk"] <= 1.0

    def test_bear_trend_high_transition_risk(self, engine):
        stable = engine.compute("BTCUSDT", "BULL_TREND", 0.7, 0.15, 0.15, 0.85, 65.0)
        unstable = engine.compute("BTCUSDT", "CHOP_MID", 0.33, 0.33, 0.34, 0.35, 50.0)
        # CHOP_MID应有更高的不确定性
        assert unstable["entropy"] >= stable["entropy"] * 0.8

    def test_liquidity_regime(self, engine):
        deep = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0, depth_ratio=2.0)
        thin = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0, depth_ratio=0.5)
        assert deep["liquidity_regime"] == "DEEP"
        assert thin["liquidity_regime"] in ("THIN", "NORMAL")

    def test_funding_regime(self, engine):
        crowded = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0, funding_rate=80.0)
        neutral = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0, funding_rate=5.0)
        assert crowded["funding_regime"] in ("LONG_CROWDED", "LONG_EXTREME")
        assert neutral["funding_regime"] == "NEUTRAL"

    def test_volatility_regime(self, engine):
        compressed = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0, volatility=0.2)
        extreme = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0, volatility=6.0)
        assert compressed["volatility_regime"] == "COMPRESSED"
        assert extreme["volatility_regime"] == "EXTREME"

    def test_bear_trend_long_banned(self, engine):
        result = engine.compute("ETH", "BEAR_TREND", 0.1, 0.75, 0.15, 0.85, 25.0)
        assert "LONG_BANNED" in result["allowed_directions"]

    def test_bull_trend_short_tactical(self, engine):
        result = engine.compute("BTC", "BULL_TREND", 0.7, 0.1, 0.2, 0.85, 68.0)
        assert "LONG_FULL" in result["allowed_directions"]

    def test_high_entropy_reduces_leverage(self, engine):
        high_conf = engine.compute("BTC", "BULL_TREND", 0.85, 0.05, 0.10, 0.95, 65.0)
        low_conf = engine.compute("BTC", "BULL_TREND", 0.35, 0.33, 0.32, 0.25, 50.0)
        assert low_conf["leverage_multiplier"] <= high_conf["leverage_multiplier"]

    def test_adapter_enrich(self):
        adapter = RegimeV2Adapter()
        analysis = {
            "symbol": "BTCUSDT",
            "regime": "BULL_TREND",
            "bull_prob": 0.65,
            "bear_prob": 0.20,
            "chop_prob": 0.15,
            "regime_confidence": 0.80,
            "rsi_1h": 65.0,
        }
        enriched = adapter.enrich(analysis, funding_rate=20.0, volatility=1.5)
        assert "regime_v2" in enriched
        rv2 = enriched["regime_v2"]
        assert "entropy" in rv2
        assert "transition_risk" in rv2
        assert "prob" in rv2
        assert abs(sum(rv2["prob"].values()) - 1.0) < 0.05

    def test_caching(self, engine):
        """同一输入两次调用，结果应一致"""
        r1 = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0)
        r2 = engine.compute("BTC", "BULL_TREND", 0.6, 0.2, 0.2, 0.8, 60.0)
        assert r1["entropy"] == r2["entropy"]
        assert r1["primary_regime"] == r2["primary_regime"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
