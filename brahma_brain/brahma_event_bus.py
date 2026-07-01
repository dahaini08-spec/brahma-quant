"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 事件总线，模块间通信
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
梵天 EventBus v1.0
借鉴 vnpy EventEngine 设计，轻量级事件总线
解决模块间状态不同步问题（watching=0 类 bug 根治）

苏摩111批准落地 · 2026-06-28
"""

import threading
import json
import time
import logging
from collections import defaultdict
from typing import Callable, Any
from pathlib import Path

logger = logging.getLogger("BrahmaEventBus")


# ═══════════════════════════════════════════════════════
#  事件类型常量（宪法级，新增需苏摩批准）
# ═══════════════════════════════════════════════════════
class BrahmaEvent:
    # 价格相关
    PRICE_UPDATE      = "price_update"       # 实时价格更新
    PRICE_ALERT       = "price_alert"        # 价格告警（触碰关键位）

    # 信号相关
    SIGNAL_FIRED      = "signal_fired"       # 梵天发出新信号
    SIGNAL_EXPIRED    = "signal_expired"     # 信号超时失效
    SIGNAL_CANCELLED  = "signal_cancelled"   # 信号被门控拒绝

    # 持仓相关
    POSITION_OPEN     = "position_open"      # 开仓成功
    POSITION_CLOSE    = "position_close"     # 平仓（止盈/止损/手动）
    POSITION_UPDATE   = "position_update"    # 持仓状态变化（浮盈更新）
    SL_TRIGGERED      = "sl_triggered"       # 软止损触发

    # 体制相关
    REGIME_CHANGE     = "regime_change"      # 体制切换（BEAR→BULL等）

    # 系统相关
    SYSTEM_START      = "system_start"       # 系统启动
    SYSTEM_STOP       = "system_stop"        # 系统停止
    HEARTBEAT         = "heartbeat"          # 心跳


# ═══════════════════════════════════════════════════════
#  事件数据包
# ═══════════════════════════════════════════════════════
class Event:
    def __init__(self, event_type: str, data: Any = None):
        self.type = event_type
        self.data = data or {}
        self.ts   = time.time()

    def to_dict(self):
        return {"type": self.type, "data": self.data, "ts": self.ts}


# ═══════════════════════════════════════════════════════
#  EventBus 核心
# ═══════════════════════════════════════════════════════
class BrahmaEventBus:
    """
    轻量级事件总线
    - 同步模式（默认）：直接调用所有处理器
    - 异步模式：后台线程队列处理（可选）
    - 状态持久化：事件日志写入文件
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        # 单例模式，全系统共享一个EventBus
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._event_log_path = Path("data/brahma_event_log.jsonl")
        self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = True
        logger.info("BrahmaEventBus v1.0 初始化完成")

    def register(self, event_type: str, handler: Callable):
        """注册事件处理器"""
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
            logger.debug(f"注册处理器: {event_type} → {handler.__name__}")

    def unregister(self, event_type: str, handler: Callable):
        """注销事件处理器"""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def emit(self, event: Event, persist: bool = False):
        """
        发射事件 → 调用所有注册的处理器
        persist=True 时写入事件日志文件
        """
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"EventBus处理器错误 [{event.type}] {handler.__name__}: {e}")

        if persist:
            self._log_event(event)

    def emit_position_open(self, symbol: str, side: str, entry: float,
                            sl: float, tp1: float, signal_id: str, **kwargs):
        """便捷方法：发射持仓开仓事件"""
        data = {
            "symbol": symbol, "side": side, "entry": entry,
            "sl": sl, "tp1": tp1, "signal_id": signal_id,
            **kwargs
        }
        self.emit(Event(BrahmaEvent.POSITION_OPEN, data), persist=True)

    def emit_position_close(self, symbol: str, outcome: str, pnl_pct: float,
                             signal_id: str, **kwargs):
        """便捷方法：发射持仓平仓事件"""
        data = {
            "symbol": symbol, "outcome": outcome,
            "pnl_pct": pnl_pct, "signal_id": signal_id,
            **kwargs
        }
        self.emit(Event(BrahmaEvent.POSITION_CLOSE, data), persist=True)

    def emit_regime_change(self, symbol: str, old_regime: str, new_regime: str):
        """便捷方法：发射体制切换事件"""
        data = {"symbol": symbol, "old": old_regime, "new": new_regime}
        self.emit(Event(BrahmaEvent.REGIME_CHANGE, data), persist=True)

    def emit_sl_triggered(self, symbol: str, trigger_price: float,
                           sl_price: float, signal_id: str):
        """便捷方法：发射软止损触发事件"""
        data = {
            "symbol": symbol, "trigger_price": trigger_price,
            "sl_price": sl_price, "signal_id": signal_id
        }
        self.emit(Event(BrahmaEvent.SL_TRIGGERED, data), persist=True)

    def _log_event(self, event: Event):
        """写入事件日志（追加模式）"""
        try:
            with open(self._event_log_path, "a") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"事件日志写入失败: {e}")

    def get_recent_events(self, event_type: str = None, limit: int = 50) -> list:
        """读取最近的事件日志"""
        events = []
        if not self._event_log_path.exists():
            return []
        with open(self._event_log_path) as f:
            for line in f:
                try:
                    e = json.loads(line.strip())
                    if event_type is None or e.get("type") == event_type:
                        events.append(e)
                except:
                    pass
        return events[-limit:]

    def handler_count(self, event_type: str) -> int:
        return len(self._handlers.get(event_type, []))

    def status(self) -> dict:
        return {
            "registered_types": list(self._handlers.keys()),
            "handler_counts": {k: len(v) for k, v in self._handlers.items()},
            "log_path": str(self._event_log_path),
        }


# 全局单例
bus = BrahmaEventBus()
