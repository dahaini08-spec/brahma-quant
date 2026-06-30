"""
nerve_emitter.py — 末梢神经发射器
===================================
任何模块一行代码即可接入神经系统：
    from nerve_system.nerve_emitter import nerve

    nerve.emit("ORDER_FAILED", {"symbol": "BTCUSDT", "error": "-4014"})
    nerve.emit("SL_TRIGGERED", {"symbol": "ETHUSDT", "price": 2100.0})

设计原则：
- 永不阻塞调用方（异步写入，失败静默）
- 零依赖（只用标准库）
- 毫秒级延迟
"""
import json, time, pathlib, threading, os
from typing import Any, Dict, Optional

# ── 事件总线文件路径 ─────────────────────────────────────────────
_ROOT      = pathlib.Path(__file__).parent.parent
_BUS_FILE  = _ROOT / "data" / "nerve_bus.jsonl"
_LOCK      = threading.Lock()

# ── 事件分级 ─────────────────────────────────────────────────────
# CRITICAL  → 立即触发协调官推送（不等30min周期）
# ERROR     → 本轮 nerve scan 一定上报
# WARN      → 本轮 nerve scan 上报
# INFO      → 仅写入总线，不主动推送
LEVEL_CRITICAL = "CRITICAL"
LEVEL_ERROR    = "ERROR"
LEVEL_WARN     = "WARN"
LEVEL_INFO     = "INFO"

# ── 事件类型注册表（标准化事件名，防止拼写错误）─────────────────
EVENT_TYPES = {
    # 执行层
    "ORDER_OPEN_OK":       "INFO",
    "ORDER_OPEN_FAIL":     "ERROR",
    "ORDER_TP_FAIL":       "ERROR",
    "ORDER_SL_FAIL":       "CRITICAL",
    "ORDER_PARTIAL_FILL":  "WARN",
    # 止损/止盈
    "SL_TRIGGERED":        "CRITICAL",
    "TP_TRIGGERED":        "INFO",
    "TRAILING_SL_MOVED":   "INFO",
    # 对账
    "GHOST_POSITION":      "CRITICAL",
    "MISSING_POSITION":    "CRITICAL",
    "NAV_DRIFT":           "ERROR",
    # 系统
    "PROCESS_CRASH":       "CRITICAL",
    "SCAN_ZERO_RESULT":    "WARN",
    "CIRCUIT_BREAKER_ON":  "CRITICAL",
    "CIRCUIT_BREAKER_OFF": "INFO",
    "KELLY_ABNORMAL":      "WARN",
    "POSITION_OVERTIME":   "WARN",
    # 信号
    "SIGNAL_GENERATED":    "INFO",
    "SIGNAL_REJECTED":     "INFO",
    "SIGNAL_CLOSED_WIN":   "INFO",
    "SIGNAL_CLOSED_LOSS":  "INFO",
    # 通用
    "CUSTOM":              "INFO",
}


class NerveEmitter:
    """末梢神经发射器 — 全局单例"""

    def __init__(self, module: str = "unknown"):
        self.module = module

    def bind(self, module: str) -> "NerveEmitter":
        """绑定调用模块名，返回新实例"""
        e = NerveEmitter(module)
        return e

    def emit(self, event_type: str, data: Dict[str, Any] = None,
             level: Optional[str] = None, message: str = "") -> None:
        """
        发射神经信号（非阻塞，失败静默）

        Args:
            event_type: 事件类型（见 EVENT_TYPES）
            data:       事件附带数据
            level:      覆盖默认级别（可选）
            message:    可读描述（可选）
        """
        try:
            lvl = level or EVENT_TYPES.get(event_type, "INFO")
            event = {
                "ts":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "ts_ms":  int(time.time() * 1000),
                "module": self.module,
                "event":  event_type,
                "level":  lvl,
                "msg":    message or "",
                "data":   {k: str(v)[:200] for k, v in (data or {}).items()},
                "pid":    os.getpid(),
            }
            line = json.dumps(event, ensure_ascii=False)
            with _LOCK:
                _BUS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(_BUS_FILE, "a") as f:
                    f.write(line + "\n")

            # CRITICAL 级别：同步写入 nerve_alerts（立即可见）
            if lvl == LEVEL_CRITICAL:
                _ALERTS = _ROOT / "data" / "nerve_alerts.jsonl"
                alert = {
                    "ts":      event["ts"],
                    "layer":   "L_REALTIME",
                    "level":   "ERROR",
                    "event":   event_type,
                    "module":  self.module,
                    "issue":   message or event_type,
                    "data":    json.dumps(data or {}, ensure_ascii=False)[:300],
                }
                with _LOCK:
                    with open(_ALERTS, "a") as f:
                        f.write(json.dumps(alert, ensure_ascii=False) + "\n")

        except Exception:
            pass  # 末梢神经不能让主流程崩溃

    # ── 快捷方法 ─────────────────────────────────────────────────
    def order_fail(self, symbol: str, order_type: str, error: Any):
        t = "ORDER_SL_FAIL" if order_type == "SL" else "ORDER_TP_FAIL" if order_type in ("TP1","TP2") else "ORDER_OPEN_FAIL"
        self.emit(t, {"symbol": symbol, "order_type": order_type, "error": str(error)},
                  message=f"{symbol} {order_type}单失败: {error}")

    def position_ghost(self, symbol: str, signal_id: str):
        self.emit("GHOST_POSITION", {"symbol": symbol, "signal_id": signal_id},
                  message=f"{symbol} 幽灵持仓（本地OPEN但Binance无仓）")

    def position_missing(self, symbol: str, amount: float):
        self.emit("MISSING_POSITION", {"symbol": symbol, "amount": str(amount)},
                  message=f"{symbol} 漏单（Binance有仓{amount}但本地无记录）")

    def sl_triggered(self, symbol: str, price: float, pnl: float = 0):
        self.emit("SL_TRIGGERED", {"symbol": symbol, "price": str(price), "pnl": str(pnl)},
                  message=f"{symbol} 止损触发 @ {price}  pnl={pnl:+.4f}")

    def tp_triggered(self, symbol: str, tp_n: int, price: float, pnl: float = 0):
        self.emit("TP_TRIGGERED", {"symbol": symbol, "tp": str(tp_n), "price": str(price), "pnl": str(pnl)},
                  message=f"{symbol} TP{tp_n}触达 @ {price}  pnl={pnl:+.4f}")


# ── 全局单例 ─────────────────────────────────────────────────────
nerve = NerveEmitter("system")


# ── 模块专用实例工厂 ─────────────────────────────────────────────
def get_nerve(module: str) -> NerveEmitter:
    """获取绑定特定模块名的末梢实例"""
    return NerveEmitter(module)


if __name__ == "__main__":
    # 测试
    n = get_nerve("test")
    n.emit("ORDER_OPEN_FAIL", {"symbol": "BTCUSDT", "error": "-4014"}, message="tick size错误")
    n.emit("GHOST_POSITION", {"symbol": "ETHUSDT", "signal_id": "LH-ETH-123"})
    n.emit("SIGNAL_GENERATED", {"symbol": "SOLUSDT", "score": "85", "channel": "A"})
    print(f"测试完成，事件写入: {_BUS_FILE}")
    # 读回验证
    lines = _BUS_FILE.read_text().strip().split("\n")
    for l in lines[-3:]:
        e = json.loads(l)
        print(f"  [{e['level']}] {e['event']} from {e['module']}: {e['msg']}")
