"""
brahma_v6/ops/metrics.py — Prometheus 指标埋点
设计院 P2 | 2026-07-08

无 Prometheus 时降级为内存计数器（零依赖运行）。
有 prometheus_client 时自动启用真实 Gauge/Counter/Histogram。
"""
from __future__ import annotations
import time
from collections import defaultdict
from typing import Dict, Optional

# ── 尝试导入 prometheus_client ──────────────────────────────
try:
    from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, push_to_gateway
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False


class _FallbackMetric:
    """无 Prometheus 时的内存计数器"""
    def __init__(self, name: str, desc: str = ""):
        self.name = name
        self._val: float = 0.0
        self._count: int = 0
        self._sum: float = 0.0

    def inc(self, amount: float = 1.0) -> None:
        self._val += amount
        self._count += 1

    def set(self, val: float) -> None:
        self._val = val

    def observe(self, val: float) -> None:
        self._count += 1
        self._sum += val
        self._val = self._sum / self._count

    def labels(self, **kwargs) -> "_FallbackMetric":
        return self

    @property
    def value(self) -> float:
        return self._val


class BrahmaMetrics:
    """
    梵天系统核心指标。
    自动检测 prometheus_client，无则降级为内存计数器。
    """

    def __init__(self, pushgateway_url: Optional[str] = None):
        self._prom = _PROM_AVAILABLE
        self._push_url = pushgateway_url
        self._counters: Dict[str, _FallbackMetric] = {}
        self._memory: Dict[str, float] = defaultdict(float)

        if self._prom:
            self._setup_prometheus()
        else:
            self._setup_fallback()

    def _setup_prometheus(self) -> None:
        self.signal_total     = Counter("brahma_signals_total",     "信号总数", ["symbol","direction","regime"])
        self.signal_blocked   = Counter("brahma_signals_blocked",   "被封锁信号", ["layer"])
        self.signal_approved  = Counter("brahma_signals_approved",  "通过信号", ["symbol"])
        self.order_intent     = Counter("brahma_order_intents",     "订单意图", ["symbol","direction"])
        self.paper_fill       = Counter("brahma_paper_fills",       "Paper成交", ["symbol"])
        self.pnl_net          = Gauge  ("brahma_pnl_net",           "累计净PnL")
        self.win_rate         = Gauge  ("brahma_win_rate",          "胜率", ["symbol"])
        self.risk_latency_ms  = Histogram("brahma_risk_latency_ms", "风控延迟ms",
                                          buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50])
        self.fill_latency_ms  = Histogram("brahma_fill_latency_ms", "成交延迟ms",
                                          buckets=[1, 5, 10, 50, 100, 500, 1000])
        self.system_health    = Gauge  ("brahma_system_health",     "系统健康分")

    def _setup_fallback(self) -> None:
        for name in ["signal_total","signal_blocked","signal_approved",
                     "order_intent","paper_fill","pnl_net","win_rate",
                     "risk_latency_ms","fill_latency_ms","system_health"]:
            self._counters[name] = _FallbackMetric(name)
        # 便捷属性
        for k, v in self._counters.items():
            setattr(self, k, v)

    # ── 业务方法 ────────────────────────────────────────────

    def record_signal(self, symbol: str, direction: str, regime: str,
                      blocked: bool, blocked_layer: str = "") -> None:
        if self._prom:
            self.signal_total.labels(symbol=symbol, direction=direction, regime=regime).inc()
            if blocked:
                self.signal_blocked.labels(layer=blocked_layer or "UNKNOWN").inc()
            else:
                self.signal_approved.labels(symbol=symbol).inc()
        else:
            self._memory[f"signal_total.{symbol}.{direction}"] += 1
            if blocked:
                self._memory[f"blocked.{blocked_layer}"] += 1
            else:
                self._memory[f"approved.{symbol}"] += 1

    def record_order_intent(self, symbol: str, direction: str) -> None:
        if self._prom:
            self.order_intent.labels(symbol=symbol, direction=direction).inc()
        else:
            self._memory[f"intent.{symbol}.{direction}"] += 1

    def record_paper_fill(self, symbol: str, net_pnl: float) -> None:
        if self._prom:
            self.paper_fill.labels(symbol=symbol).inc()
            self.pnl_net.inc(net_pnl)
        else:
            self._memory[f"fill.{symbol}"] += 1
            self._memory["pnl_net"] += net_pnl

    def record_risk_latency(self, ms: float) -> None:
        if self._prom:
            self.risk_latency_ms.observe(ms)
        else:
            self._memory["risk_latency_samples"] += 1
            self._memory["risk_latency_sum"] += ms

    def set_system_health(self, score: float) -> None:
        if self._prom:
            self.system_health.set(score)
        else:
            self._memory["system_health"] = score

    def snapshot(self) -> Dict:
        """返回当前指标快照（供日志/报告）"""
        if self._prom:
            return {"prometheus": True, "push_url": self._push_url}
        n = self._memory.get("risk_latency_samples", 1) or 1
        return {
            "prometheus":       False,
            "signals_total":    sum(v for k, v in self._memory.items() if k.startswith("signal_total.")),
            "signals_blocked":  sum(v for k, v in self._memory.items() if k.startswith("blocked.")),
            "signals_approved": sum(v for k, v in self._memory.items() if k.startswith("approved.")),
            "order_intents":    sum(v for k, v in self._memory.items() if k.startswith("intent.")),
            "paper_fills":      sum(v for k, v in self._memory.items() if k.startswith("fill.")),
            "pnl_net":          round(self._memory.get("pnl_net", 0), 4),
            "system_health":    self._memory.get("system_health", 100),
            "risk_latency_avg_ms": round(self._memory.get("risk_latency_sum", 0) / n, 4),
        }


# ── 全局单例 ───────────────────────────────────────────────
_metrics: Optional[BrahmaMetrics] = None


def get_metrics() -> BrahmaMetrics:
    global _metrics
    if _metrics is None:
        _metrics = BrahmaMetrics()
    return _metrics


if __name__ == "__main__":
    m = BrahmaMetrics()
    m.record_signal("BTCUSDT", "LONG", "BEAR_RECOVERY", False)
    m.record_signal("ETHUSDT", "SHORT", "BEAR_TREND", True, "L3_REGIME_DEATH_ZONE")
    m.record_order_intent("BTCUSDT", "LONG")
    m.record_paper_fill("BTCUSDT", 0.025)
    m.record_risk_latency(0.032)
    m.set_system_health(100.0)
    snap = m.snapshot()
    print(f"Prometheus可用: {_PROM_AVAILABLE}")
    print(f"指标快照: {snap}")
    assert snap["signals_total"] == 2
    assert snap["signals_blocked"] == 1
    assert snap["paper_fills"] == 1
    print("✅ Prometheus metrics 自检完成")
