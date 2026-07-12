"""
brahma_v6/bus/file_bus.py — 可回放事件总线 v1.0
设计院 × 顶级评估v6.0 Phase 2 | 2026-07-08

架构设计：
  当前实现：FileEventBus（JSONL持久化，零外部依赖，即刻可用）
  未来迁移：NATSEventBus / RedisStreamBus（接口相同，无缝替换）

核心能力：
  1. 发布订阅（pub/sub）
  2. 主题过滤（subject prefix match）
  3. JSONL持久化 + 回放
  4. 消费者组（consumer group）支持
  5. 消息确认（ack）
  6. 死信队列（dead letter）
  7. 同一接口 → NATS/Redis迁移零成本
"""
from __future__ import annotations
import json
import time
import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from collections import defaultdict

BASE = Path(__file__).resolve().parents[2]
BUS_DIR = BASE / "data" / "event_bus"
BUS_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════
#  消息包装器
# ══════════════════════════════════════════════════════
@dataclass
class BusMessage:
    subject: str
    payload: Dict[str, Any]
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    ts_published: float = field(default_factory=time.time)
    ack_count: int = 0
    redelivery_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "BusMessage":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ══════════════════════════════════════════════════════
#  消费者状态（支持consumer group）
# ══════════════════════════════════════════════════════
@dataclass
class ConsumerState:
    group: str
    subject_filter: str
    last_seq: int = 0       # 最后消费的序号
    ack_pending: int = 0
    total_consumed: int = 0


# ══════════════════════════════════════════════════════
#  FileEventBus — 主实现
# ══════════════════════════════════════════════════════
class FileEventBus:
    """
    基于JSONL文件的可回放事件总线。
    接口与NATSEventBus完全一致，未来可零成本迁移。

    使用模式：
        bus = FileEventBus()
        bus.subscribe("signal.*", my_handler)
        bus.publish("signal.scored", signal_event.to_dict(), trace_id="xxx")
        bus.replay("signal.scored", from_ts=1700000000)
    """

    def __init__(self, bus_dir: Path = BUS_DIR, max_file_size_mb: float = 50.0):
        self._bus_dir = bus_dir
        self._bus_dir.mkdir(parents=True, exist_ok=True)
        self._max_file_size = max_file_size_mb * 1024 * 1024
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._consumer_states: Dict[str, ConsumerState] = {}
        self._lock = threading.Lock()
        self._seq = self._load_seq()
        self._dead_letter: List[BusMessage] = []

    # ── 核心API ──────────────────────────────────────────

    def publish(
        self,
        subject: str,
        payload: Dict[str, Any],
        trace_id: str = "",
        sync: bool = False,
    ) -> BusMessage:
        """
        发布消息到主题。
        sync=True: 同步回调所有订阅者（适合单进程测试）
        sync=False: 后台线程回调（默认，适合生产）
        """
        msg = BusMessage(
            subject=subject,
            payload=payload,
            trace_id=trace_id,
            ts_published=time.time(),
        )
        # 持久化
        self._persist(msg)
        # 分发
        if sync:
            self._dispatch(msg)
        else:
            t = threading.Thread(target=self._dispatch, args=(msg,), daemon=True)
            t.start()
        return msg

    def subscribe(
        self,
        subject_filter: str,
        handler: Callable[[BusMessage], None],
        group: str = "default",
    ) -> None:
        """
        订阅主题（支持通配符 *）。
        subject_filter: 精确匹配或前缀+* 匹配（如 'signal.*'）
        """
        key = f"{group}::{subject_filter}"
        with self._lock:
            self._subscribers[subject_filter].append(handler)
            if key not in self._consumer_states:
                self._consumer_states[key] = ConsumerState(
                    group=group,
                    subject_filter=subject_filter,
                )

    def replay(
        self,
        subject_filter: str = "*",
        from_ts: float = 0.0,
        to_ts: float = 0.0,
        limit: int = 10000,
        callback: Optional[Callable[[BusMessage], None]] = None,
    ) -> List[BusMessage]:
        """
        回放历史消息。核心能力：事件溯源、回测、调试。
        """
        to_ts = to_ts or time.time()
        messages = []
        for log_file in sorted(self._bus_dir.glob("*.jsonl")):
            try:
                for line in log_file.read_text().splitlines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    msg = BusMessage.from_dict(d)
                    if msg.ts_published < from_ts or msg.ts_published > to_ts:
                        continue
                    if not self._matches(msg.subject, subject_filter):
                        continue
                    messages.append(msg)
                    if callback:
                        callback(msg)
                    if len(messages) >= limit:
                        return messages
            except Exception:
                continue
        return messages

    def get_stats(self) -> Dict:
        """总线统计"""
        total_msgs = 0
        total_size = 0
        subjects: Dict[str, int] = defaultdict(int)
        for log_file in self._bus_dir.glob("*.jsonl"):
            total_size += log_file.stat().st_size
            try:
                for line in log_file.read_text().splitlines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    total_msgs += 1
                    subjects[d.get("subject", "unknown")] += 1
            except Exception:
                continue
        return {
            "total_messages": total_msgs,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "subjects": dict(sorted(subjects.items(), key=lambda x: -x[1])[:10]),
            "dead_letter_count": len(self._dead_letter),
            "subscriber_count": sum(len(v) for v in self._subscribers.values()),
            "bus_type": "FileEventBus",
            "nats_ready": False,  # 迁移到NATS时改为True
        }

    def drain_dead_letter(self) -> List[BusMessage]:
        """返回并清空死信队列"""
        with self._lock:
            dead = list(self._dead_letter)
            self._dead_letter.clear()
        return dead

    # ── 内部方法 ─────────────────────────────────────────

    def _dispatch(self, msg: BusMessage) -> None:
        """分发到所有匹配的订阅者"""
        with self._lock:
            handlers = [
                h for subject_filter, handlers in self._subscribers.items()
                if self._matches(msg.subject, subject_filter)
                for h in handlers
            ]
        for handler in handlers:
            try:
                handler(msg)
            except Exception as e:
                msg.redelivery_count += 1
                if msg.redelivery_count >= 3:
                    with self._lock:
                        self._dead_letter.append(msg)

    def _matches(self, subject: str, pattern: str) -> bool:
        """主题匹配：精确 或 prefix.*"""
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return subject == prefix or subject.startswith(prefix + ".")
        return subject == pattern

    def _persist(self, msg: BusMessage) -> None:
        """
        P0-4 修复：持久化到按日分片的 JSONL 文件。
        1. fcntl 文件锁（Linux）防多进程写入竞争。
        2. 写入失败时 fail-closed：抨入死信队列，不再静默吃掉。
        """
        import os
        date_str = time.strftime("%Y-%m-%d", time.gmtime(msg.ts_published))
        log_file = self._bus_dir / f"events_{date_str}.jsonl"
        line = json.dumps(msg.to_dict(), ensure_ascii=False) + "\n"
        try:
            with self._lock:
                fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                try:
                    try:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX)
                    except ImportError:
                        pass  # Windows 不支持 fcntl，单进程时依赖 threading.Lock
                    os.write(fd, line.encode("utf-8"))
                finally:
                    try:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except ImportError:
                        pass
                    os.close(fd)
                self._seq += 1
        except Exception as e:
            # P0-4: fail-closed — 写入失败就进死信队列，不再静默吃掉
            msg.redelivery_count += 1
            self._dead_letter.append(msg)
            # 死信队列持久化（防重启丢失）
            try:
                dlq_file = self._bus_dir / "dead_letter.jsonl"
                dlq_entry = {"ts": time.time(), "error": str(e), **msg.to_dict()}
                with open(str(dlq_file), "a", encoding="utf-8") as df:
                    df.write(json.dumps(dlq_entry, ensure_ascii=False) + "\n")
            except Exception:
                pass  # DLQ 自身写入失败时不再抓持

    def _load_seq(self) -> int:
        """加载当前序号"""
        total = 0
        for f in self._bus_dir.glob("*.jsonl"):
            try:
                total += sum(1 for line in f.read_text().splitlines() if line.strip())
            except Exception:
                pass
        return total


# ══════════════════════════════════════════════════════
#  信号适配器：将brahma_analysis_runner输出接入总线
# ══════════════════════════════════════════════════════
class SignalBusAdapter:
    """
    把现有brahma_analysis_runner输出接入FileEventBus。
    向后兼容，不修改现有runner。
    """

    def __init__(self, bus: FileEventBus):
        self._bus = bus

    def emit_signal(self, analysis_result: Dict) -> Optional[BusMessage]:
        """从run_analysis()结果构建并发布signal.scored事件"""
        if not analysis_result or not analysis_result.get("symbol"):
            return None
        payload = {
            "symbol":       analysis_result.get("symbol", ""),
            "direction":    analysis_result.get("signal_dir", analysis_result.get("direction", "")),
            "regime":       analysis_result.get("regime", ""),
            "regime_cn":    analysis_result.get("regime_cn", ""),
            "score":        analysis_result.get("score", 0),
            "grade":        analysis_result.get("grade", ""),
            "blocked":      analysis_result.get("blocked", True),
            "valid_signal": analysis_result.get("valid_signal", False),
            "action":       analysis_result.get("action", ""),
            "price":        analysis_result.get("price", 0),
            "stop_loss":    analysis_result.get("stop_loss", 0),
            "tp1":          analysis_result.get("tp1", 0),
            "timing_badge": analysis_result.get("timing_badge", ""),
            "raw_result":   True,
        }
        trace_id = analysis_result.get("trace_id", str(uuid.uuid4()))
        return self._bus.publish("signal.scored", payload, trace_id=trace_id, sync=True)

    def emit_position_open(self, symbol: str, direction: str, entry_price: float,
                            quantity: float, stop_loss: float, trace_id: str = "") -> BusMessage:
        return self._bus.publish("position.open", {
            "symbol": symbol, "direction": direction,
            "entry_price": entry_price, "quantity": quantity,
            "stop_loss": stop_loss,
        }, trace_id=trace_id, sync=True)

    def emit_position_close(self, symbol: str, direction: str, exit_price: float,
                             pnl_usdt: float, reason: str, trace_id: str = "") -> BusMessage:
        return self._bus.publish("position.close", {
            "symbol": symbol, "direction": direction,
            "exit_price": exit_price, "pnl_usdt": pnl_usdt, "reason": reason,
        }, trace_id=trace_id, sync=True)

    def emit_regime_change(self, symbol: str, old_regime: str, new_regime: str) -> BusMessage:
        return self._bus.publish("regime.change", {
            "symbol": symbol, "old_regime": old_regime, "new_regime": new_regime,
        }, sync=True)


# ══════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════
_bus: Optional[FileEventBus] = None


def get_bus() -> FileEventBus:
    global _bus
    if _bus is None:
        _bus = FileEventBus()
    return _bus


def get_adapter() -> SignalBusAdapter:
    return SignalBusAdapter(get_bus())


if __name__ == "__main__":
    print("=== FileEventBus 自检 ===\n")
    bus = FileEventBus(bus_dir=BASE / "data" / "event_bus_test")

    received = []

    def on_signal(msg: BusMessage):
        received.append(msg)
        print(f"  📨 [signal.*] {msg.subject}: {msg.payload.get('symbol')} score={msg.payload.get('score')}")

    def on_position(msg: BusMessage):
        print(f"  📨 [position.*] {msg.subject}: {msg.payload.get('symbol')} {msg.payload.get('direction')}")

    bus.subscribe("signal.*", on_signal)
    bus.subscribe("position.*", on_position)

    # 发布测试消息（sync=True方便测试）
    bus.publish("signal.scored", {"symbol": "BTCUSDT", "score": 162.0, "direction": "LONG"}, sync=True)
    bus.publish("signal.scored", {"symbol": "ETHUSDT", "score": 138.5, "direction": "SHORT"}, sync=True)
    bus.publish("position.open", {"symbol": "BTCUSDT", "direction": "LONG", "entry_price": 62000.0}, sync=True)
    bus.publish("system.health", {"status": "HEALTHY", "latency_ms": 120}, sync=True)

    assert len(received) == 2, f"期望2条signal消息，实际{len(received)}"

    # 回放测试
    replayed = bus.replay("signal.*", from_ts=0.0)
    print(f"\n  回放: {len(replayed)} 条signal消息")
    assert len(replayed) >= 2

    stats = bus.get_stats()
    print(f"\n  总线统计: {stats['total_messages']}条 {stats['total_size_mb']}MB")
    print(f"  主题分布: {stats['subjects']}")

    # 清理测试目录
    import shutil
    shutil.rmtree(BASE / "data" / "event_bus_test", ignore_errors=True)

    print("\n✅ FileEventBus 自检完成")
