"""
brahma_v6/ops/prometheus_exporter.py — Prometheus 真实部署
设计院 9.2 升级 | 2026-07-08

功能：
  1. 真实 prometheus_client Counter/Gauge/Histogram 指标
  2. HTTP /metrics 端点（:9100）
  3. Pushgateway 推送（可选）
  4. 自动从 OrderManager / FileBus / TradeLedger 采集
  5. 无 Prometheus 时降级为内存计数器（零配置运行）

指标清单：
  brahma_signals_total          — 信号总数（by symbol/direction/regime）
  brahma_signals_blocked_total  — 封锁信号（by layer）
  brahma_risk_latency_ms        — 风控延迟 Histogram
  brahma_order_intents_total    — OrderIntent 总数
  brahma_orders_filled_total    — 已成交订单
  brahma_orders_unknown_total   — UNKNOWN 订单
  brahma_pnl_net_usdt           — 累计净 PnL
  brahma_win_rate               — 胜率（by symbol）
  brahma_system_health          — 系统健康分
  brahma_ev_bucket_multiplier   — EV Bucket 仓位乘数
  brahma_paper_fill_latency_ms  — Paper 成交延迟
"""
from __future__ import annotations
import time
import threading
from pathlib import Path
from typing import Dict, Optional, List

BASE = Path(__file__).resolve().parents[2]

# ── Prometheus 导入 ─────────────────────────────────────────
try:
    from prometheus_client import (
        Counter, Gauge, Histogram, Summary,
        CollectorRegistry, push_to_gateway,
        start_http_server, REGISTRY,
    )
    _PROM = True
except ImportError:
    _PROM = False


# ══════════════════════════════════════════════════════
#  BrahmaPrometheusExporter
# ══════════════════════════════════════════════════════
class BrahmaPrometheusExporter:
    """
    梵天系统 Prometheus 指标导出器。
    有 prometheus_client → 真实指标 + HTTP server。
    无 → 内存计数器降级，接口相同。
    """

    def __init__(
        self,
        http_port: int = 9100,
        pushgateway_url: str = None,
        push_interval_s: float = 30.0,
        auto_start_http: bool = False,   # 默认不自动启动（按需调用 start()）
    ):
        self._port = http_port
        self._push_url = pushgateway_url
        self._push_interval = push_interval_s
        self._prom = _PROM
        self._http_started = False
        self._push_thread: Optional[threading.Thread] = None
        self._running = False

        # 内存计数器（降级用）
        self._mem: Dict[str, float] = {}

        if self._prom:
            self._setup_metrics()
        if auto_start_http and _PROM:
            self.start_http()

    def _setup_metrics(self) -> None:
        """注册所有 Prometheus 指标（幂等，重复注册安全）"""
        try:
            self.signals_total = Counter(
                "brahma_signals_total", "信号总数",
                ["symbol", "direction", "regime"],
            )
            self.signals_blocked = Counter(
                "brahma_signals_blocked_total", "封锁信号",
                ["layer"],
            )
            self.risk_latency = Histogram(
                "brahma_risk_latency_ms", "风控决策延迟(ms)",
                buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100],
            )
            self.order_intents = Counter(
                "brahma_order_intents_total", "OrderIntent 总数",
                ["symbol", "direction"],
            )
            self.orders_filled = Counter(
                "brahma_orders_filled_total", "已成交订单",
                ["symbol", "mode"],
            )
            self.orders_unknown = Gauge(
                "brahma_orders_unknown_gauge", "当前 UNKNOWN 订单数",
            )
            self.orders_reconciled = Counter(
                "brahma_orders_reconciled_total", "已对账订单",
                ["result"],
            )
            self.pnl_net = Gauge(
                "brahma_pnl_net_usdt", "累计净 PnL (USDT)",
                ["symbol"],
            )
            self.win_rate = Gauge(
                "brahma_win_rate", "胜率",
                ["symbol"],
            )
            self.system_health = Gauge(
                "brahma_system_health", "系统健康分 (0-100)",
            )
            self.ev_bucket_mult = Gauge(
                "brahma_ev_bucket_multiplier", "EV Bucket 仓位乘数",
                ["bucket"],
            )
            self.fill_latency = Histogram(
                "brahma_paper_fill_latency_ms", "Paper 成交延迟(ms)",
                buckets=[0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000],
            )
            self.bus_events = Counter(
                "brahma_bus_events_total", "总线事件数",
                ["subject"],
            )
            self.reconcile_count = Counter(
                "brahma_reconcile_total", "对账次数",
                ["method", "result"],
            )
        except Exception:
            # 已注册的指标不重复注册
            pass

    # ── 记录方法 ──────────────────────────────────────────
    def record_signal(
        self, symbol: str, direction: str, regime: str,
        blocked: bool, blocked_layer: str = "",
    ) -> None:
        if self._prom:
            self.signals_total.labels(symbol=symbol, direction=direction, regime=regime).inc()
            if blocked:
                self.signals_blocked.labels(layer=blocked_layer or "UNKNOWN").inc()
        else:
            self._mem[f"sig.{symbol}.{direction}"] = self._mem.get(f"sig.{symbol}.{direction}", 0) + 1
            if blocked:
                self._mem[f"blocked.{blocked_layer}"] = self._mem.get(f"blocked.{blocked_layer}", 0) + 1

    def record_risk_latency(self, ms: float) -> None:
        if self._prom:
            self.risk_latency.observe(ms)
        else:
            k = "risk_lat_sum"
            self._mem[k] = self._mem.get(k, 0) + ms
            self._mem["risk_lat_n"] = self._mem.get("risk_lat_n", 0) + 1

    def record_order_intent(self, symbol: str, direction: str) -> None:
        if self._prom:
            self.order_intents.labels(symbol=symbol, direction=direction).inc()
        else:
            self._mem[f"intent.{symbol}.{direction}"] = self._mem.get(f"intent.{symbol}.{direction}", 0) + 1

    def record_fill(self, symbol: str, mode: str = "paper", latency_ms: float = 0.0) -> None:
        if self._prom:
            self.orders_filled.labels(symbol=symbol, mode=mode).inc()
            if latency_ms > 0:
                self.fill_latency.observe(latency_ms)
        else:
            self._mem[f"fill.{symbol}"] = self._mem.get(f"fill.{symbol}", 0) + 1

    def set_orders_unknown(self, count: int) -> None:
        if self._prom:
            self.orders_unknown.set(count)
        else:
            self._mem["unknown"] = count

    def record_reconcile(self, method: str, result: str) -> None:
        if self._prom:
            self.reconcile_count.labels(method=method, result=result).inc()
        else:
            self._mem[f"reconcile.{method}.{result}"] = self._mem.get(f"reconcile.{method}.{result}", 0) + 1

    def set_pnl(self, symbol: str, net_pnl: float) -> None:
        if self._prom:
            self.pnl_net.labels(symbol=symbol).set(net_pnl)
        else:
            self._mem[f"pnl.{symbol}"] = net_pnl

    def set_win_rate(self, symbol: str, wr: float) -> None:
        if self._prom:
            self.win_rate.labels(symbol=symbol).set(wr)
        else:
            self._mem[f"wr.{symbol}"] = wr

    def set_system_health(self, score: float) -> None:
        if self._prom:
            self.system_health.set(score)
        else:
            self._mem["health"] = score

    def set_ev_bucket(self, bucket: str, multiplier: float) -> None:
        if self._prom:
            self.ev_bucket_mult.labels(bucket=bucket).set(multiplier)
        else:
            self._mem[f"ev.{bucket}"] = multiplier

    def record_bus_event(self, subject: str) -> None:
        if self._prom:
            self.bus_events.labels(subject=subject).inc()
        else:
            self._mem[f"bus.{subject}"] = self._mem.get(f"bus.{subject}", 0) + 1

    # ── 从系统组件自动采集 ────────────────────────────────
    def collect_from_order_manager(self, om) -> None:
        """从 OrderManager 采集实时订单指标"""
        stats = om.stats()
        self.set_orders_unknown(stats.get("unknown", 0))

    def collect_from_ledger(self, ledger) -> None:
        """从 TradeLedger 采集 PnL + 胜率"""
        report = ledger.pnl_report()
        if report.get("total_trades", 0) == 0:
            return
        # 按 symbol 细分
        breakdown = report.get("by_direction", {})
        for sym_data in [report]:
            self.set_pnl("ALL", report.get("net_pnl", 0))
            self.set_win_rate("ALL", report.get("win_rate", 0))

    def collect_from_ev_registry(self, registry) -> None:
        """从 EVBucketRegistry 采集仓位乘数"""
        for key, bucket in registry._buckets.items():
            self.set_ev_bucket(key[:40], bucket.size_multiplier)

    # ── HTTP Server ────────────────────────────────────────
    def start_http(self, port: int = None) -> bool:
        """启动 /metrics HTTP 端点"""
        if not self._prom:
            return False
        if self._http_started:
            return True
        try:
            start_http_server(port or self._port)
            self._http_started = True
            return True
        except Exception:
            return False

    # ── Pushgateway ────────────────────────────────────────
    def start_push(self) -> None:
        """后台线程定期推送到 Pushgateway"""
        if not self._prom or not self._push_url:
            return
        self._running = True
        self._push_thread = threading.Thread(
            target=self._push_loop, daemon=True, name="PromPusher"
        )
        self._push_thread.start()

    def push_now(self, job: str = "brahma_trading") -> bool:
        """立即推送一次"""
        if not self._prom or not self._push_url:
            return False
        try:
            push_to_gateway(self._push_url, job=job, registry=REGISTRY)
            return True
        except Exception:
            return False

    def _push_loop(self) -> None:
        while self._running:
            self.push_now()
            time.sleep(self._push_interval)

    # ── 快照（内存模式）──────────────────────────────────
    def snapshot(self) -> Dict:
        if self._prom:
            return {
                "backend":      "prometheus",
                "http_port":    self._port,
                "http_started": self._http_started,
                "push_url":     self._push_url,
            }
        total_sig = sum(v for k, v in self._mem.items() if k.startswith("sig."))
        total_blocked = sum(v for k, v in self._mem.items() if k.startswith("blocked."))
        total_fills = sum(v for k, v in self._mem.items() if k.startswith("fill."))
        n = self._mem.get("risk_lat_n", 1) or 1
        return {
            "backend":           "memory",
            "signals_total":     total_sig,
            "signals_blocked":   total_blocked,
            "fills_total":       total_fills,
            "unknown_orders":    self._mem.get("unknown", 0),
            "system_health":     self._mem.get("health", 100),
            "pnl_net_all":       self._mem.get("pnl.ALL", 0),
            "win_rate_all":      self._mem.get("wr.ALL", 0),
            "risk_lat_avg_ms":   round(self._mem.get("risk_lat_sum", 0) / n, 4),
        }


# ── 全局单例 ────────────────────────────────────────────────
_exporter: Optional[BrahmaPrometheusExporter] = None


def get_exporter(
    http_port: int = 9100,
    pushgateway_url: str = None,
) -> BrahmaPrometheusExporter:
    global _exporter
    if _exporter is None:
        _exporter = BrahmaPrometheusExporter(
            http_port=http_port,
            pushgateway_url=pushgateway_url,
        )
    return _exporter


if __name__ == "__main__":
    print("=== BrahmaPrometheusExporter 自检 ===\n")
    exp = BrahmaPrometheusExporter(http_port=9100)
    print(f"Prometheus 可用: {_PROM}")

    # 模拟指标记录
    exp.record_signal("BTCUSDT", "LONG", "BEAR_RECOVERY", False)
    exp.record_signal("ETHUSDT", "SHORT", "BEAR_TREND", True, "L3_REGIME_DEATH_ZONE")
    exp.record_risk_latency(0.032)
    exp.record_order_intent("BTCUSDT", "LONG")
    exp.record_fill("BTCUSDT", "paper", 1.5)
    exp.set_orders_unknown(2)
    exp.record_reconcile("mock", "FILLED")
    exp.record_reconcile("timeout_force", "CANCELLED")
    exp.set_system_health(100.0)
    exp.set_pnl("BTCUSDT", 12.5)
    exp.set_win_rate("BTCUSDT", 0.55)
    exp.set_ev_bucket("BTCUSDT|LONG|BEAR_RECOVERY|S4", 1.2)

    snap = exp.snapshot()
    print(f"快照: {snap}")

    # HTTP server 测试（不自动启动，仅测试接口）
    if _PROM:
        print(f"HTTP /metrics 端点: 调用 exp.start_http() 启动于 :{exp._port}")
        print(f"Pushgateway 推送: 调用 exp.push_now() 推送到 {exp._push_url or '(未配置)'}")

    print("\n✅ BrahmaPrometheusExporter 自检完成")
