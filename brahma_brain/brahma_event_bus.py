"""
brahma_event_bus.py — 转发shim（2026-09-01 设计院精简封印）
实际代码已合并进 brahma_bus.py
"""
from brahma_brain.brahma_bus import BrahmaEvent, Event, BrahmaEventBus, _handle_regime_change_purge  # noqa: F401

__all__ = ['BrahmaEvent', 'Event', 'BrahmaEventBus', '_handle_regime_change_purge']
