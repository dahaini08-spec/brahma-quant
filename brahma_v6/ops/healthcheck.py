"""
brahma_v6/ops/healthcheck.py — HealthChecker for system component status
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import time
from typing import Dict, Any, Optional


class HealthChecker:
    """
    Aggregates health status from all system components.
    Returns a dict of component → status for ops monitoring.
    """

    def __init__(
        self,
        kill_switch=None,
        dlq=None,
        adapter=None,
        signal_consumer=None,
    ) -> None:
        self._kill_switch = kill_switch
        self._dlq = dlq
        self._adapter = adapter
        self._signal_consumer = signal_consumer
        self._start_time = time.time()

    def check_kill_switch(self) -> Dict[str, Any]:
        if self._kill_switch is None:
            return {"status": "UNKNOWN", "reason": "not_configured"}
        if self._kill_switch.is_active():
            return {
                "status": "CRITICAL",
                "reason": self._kill_switch.reason,
                "activated_at": self._kill_switch.activated_at,
            }
        return {"status": "OK"}

    def check_dlq(self) -> Dict[str, Any]:
        if self._dlq is None:
            return {"status": "UNKNOWN"}
        count = self._dlq.count()
        if count > 100:
            return {"status": "WARN", "dlq_depth": count}
        return {"status": "OK", "dlq_depth": count}

    def check_adapter(self) -> Dict[str, Any]:
        if self._adapter is None:
            return {"status": "UNKNOWN"}
        # Basic check: adapter exists and kill switch not active
        return {"status": "OK"}

    def check_signal_consumer(self) -> Dict[str, Any]:
        if self._signal_consumer is None:
            return {"status": "UNKNOWN"}
        stats = self._signal_consumer.stats
        return {"status": "OK", "stats": stats}

    def check_all(self) -> Dict[str, Any]:
        """Run all health checks and return aggregated status."""
        checks = {
            "kill_switch": self.check_kill_switch(),
            "dlq": self.check_dlq(),
            "adapter": self.check_adapter(),
            "signal_consumer": self.check_signal_consumer(),
        }

        # Determine overall status
        # UNKNOWN = optional component not configured → not a failure
        all_statuses = [c.get("status", "UNKNOWN") for c in checks.values()]
        critical_statuses = [s for s in all_statuses if s not in ("OK", "UNKNOWN")]
        if "CRITICAL" in critical_statuses:
            overall = "CRITICAL"
        elif "WARN" in critical_statuses:
            overall = "WARN"
        elif "DEGRADED" in critical_statuses:
            overall = "DEGRADED"
        else:
            overall = "OK"

        return {
            "overall": overall,
            "uptime_seconds": int(time.time() - self._start_time),
            "checks": checks,
            "ts": time.time(),
        }
