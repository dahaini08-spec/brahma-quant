"""
brahma_v6/ops/alerting.py — Alerter: sends alerts via push_hub
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Alerter:
    """
    Sends alerts to the configured push channel.
    Alert levels: INFO, WARN, CRITICAL
    """

    def __init__(self, push_hub=None, min_level: str = "WARN") -> None:
        self._push_hub = push_hub
        self._min_level = min_level
        self._levels = {"INFO": 0, "WARN": 1, "CRITICAL": 2}

    def send_alert(self, level: str, message: str) -> bool:
        """
        Send an alert at the given level.
        Returns True if alert was sent, False if filtered by min_level.
        """
        level_upper = level.upper()
        min_val = self._levels.get(self._min_level, 1)
        curr_val = self._levels.get(level_upper, 0)

        if curr_val < min_val:
            return False

        entry = {
            "level": level_upper,
            "message": message,
            "ts": time.time(),
        }

        # Log regardless
        if level_upper == "CRITICAL":
            logger.critical(f"ALERT [{level_upper}]: {message}")
        elif level_upper == "WARN":
            logger.warning(f"ALERT [{level_upper}]: {message}")
        else:
            logger.info(f"ALERT [{level_upper}]: {message}")

        # Push via push_hub if available
        if self._push_hub is not None:
            try:
                if hasattr(self._push_hub, "send"):
                    self._push_hub.send(level=level_upper, message=message)
                elif callable(self._push_hub):
                    self._push_hub(entry)
            except Exception as e:
                logger.error(f"Failed to send alert via push_hub: {e}")
                return False

        return True

    def critical(self, message: str) -> bool:
        return self.send_alert("CRITICAL", message)

    def warn(self, message: str) -> bool:
        return self.send_alert("WARN", message)

    def info(self, message: str) -> bool:
        return self.send_alert("INFO", message)
