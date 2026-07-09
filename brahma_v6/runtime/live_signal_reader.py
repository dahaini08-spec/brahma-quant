"""
brahma_v6/runtime/live_signal_reader.py
梵天 live_signal_log.jsonl → RawSignal 消费器

职责：
  - tail 方式跟踪 live_signal_log.jsonl 新增条目
  - 去重（signal_id）
  - 过滤过期 / 已结算 / action=SKIP 的信号
  - 将有效信号转换为 RawSignal 交给 OrderPipeline
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Set

from brahma_v6.runtime.signal_consumer import RawSignal

logger = logging.getLogger(__name__)

# 默认日志路径
_DEFAULT_LOG = Path(__file__).resolve().parents[3] / "data" / "live_signal_log.jsonl"

# 信号有效期上限（秒）：超过此时间不再执行
MAX_SIGNAL_AGE_S: float = 4 * 3600   # 4h

# 最低分数（归一化 score，梵天原始 score / 175）
MIN_SCORE_RAW: float = 155.0          # 原始梵天分

# action 白名单：只有这些 action 才下单
ALLOWED_ACTIONS: Set[str] = {"BUY", "SELL", "TRADE", "EXECUTE", "OPEN"}

# regime 黑名单（梵天死穴）
BLOCKED_REGIMES: Set[str] = {"BEAR_TREND", "CHOP_HIGH", "CHOP_LONG", "UNKNOWN"}


@dataclass
class LiveSignalReader:
    """
    Tails live_signal_log.jsonl, yields valid RawSignal objects.

    Parameters
    ----------
    log_path : Path
        Path to live_signal_log.jsonl
    poll_interval : float
        Seconds between polls when no new lines appear
    max_signal_age_s : float
        Signals older than this are skipped
    min_score_raw : float
        Minimum raw brahma score (0-175 scale)
    """

    log_path: Path = field(default_factory=lambda: _DEFAULT_LOG)
    poll_interval: float = 5.0
    max_signal_age_s: float = MAX_SIGNAL_AGE_S
    min_score_raw: float = MIN_SCORE_RAW

    # runtime state
    _seen_ids: Set[str] = field(default_factory=set, init=False, repr=False)
    _file_pos: int = field(default=0, init=False, repr=False)
    _stats: dict = field(default_factory=lambda: {"read": 0, "skipped": 0, "emitted": 0}, init=False, repr=False)

    def __post_init__(self) -> None:
        self.log_path = Path(self.log_path)
        # Start at end of existing file (don't replay history)
        if self.log_path.exists():
            self._file_pos = self.log_path.stat().st_size
            logger.info(f"[LiveSignalReader] 初始化 tail at byte {self._file_pos} — {self.log_path}")
        else:
            logger.warning(f"[LiveSignalReader] 日志文件不存在: {self.log_path}")

    # ─────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────

    def poll_once(self) -> list[RawSignal]:
        """Non-blocking: read any new lines, return list of valid RawSignals."""
        if not self.log_path.exists():
            return []

        current_size = self.log_path.stat().st_size
        if current_size <= self._file_pos:
            return []

        results: list[RawSignal] = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                f.seek(self._file_pos)
                new_data = f.read(current_size - self._file_pos)
                self._file_pos = current_size

            for line in new_data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    sig = self._parse(entry)
                    if sig:
                        results.append(sig)
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    logger.debug(f"[LiveSignalReader] 解析异常: {e}")
        except OSError as e:
            logger.warning(f"[LiveSignalReader] 读取失败: {e}")

        return results

    def tail(self, stop_fn=None) -> Iterator[RawSignal]:
        """
        Blocking generator. Yields RawSignals as they appear.
        stop_fn: optional callable → return True to stop.
        """
        logger.info("[LiveSignalReader] 开始 tail 模式")
        while True:
            if stop_fn and stop_fn():
                logger.info("[LiveSignalReader] stop_fn=True, 退出")
                break
            signals = self.poll_once()
            for sig in signals:
                yield sig
            time.sleep(self.poll_interval)

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ─────────────────────────────────────────────
    #  Internal
    # ─────────────────────────────────────────────

    def _parse(self, entry: dict) -> Optional[RawSignal]:
        """Validate and convert a log entry to RawSignal. Returns None to skip."""
        self._stats["read"] += 1

        signal_id = entry.get("signal_id") or entry.get("event_id") or ""

        # 去重
        if signal_id and signal_id in self._seen_ids:
            self._stats["skipped"] += 1
            return None
        if signal_id:
            self._seen_ids.add(signal_id)

        # 仅处理 valid=True 的信号
        if not entry.get("valid", False):
            self._stats["skipped"] += 1
            return None

        # 已结算 / 过期
        if entry.get("settled") or entry.get("status") in ("CLOSED", "TIMEOUT", "EXPIRED"):
            self._stats["skipped"] += 1
            return None

        # 分数门控
        score_raw = float(entry.get("score", 0) or 0)
        if score_raw < self.min_score_raw:
            self._stats["skipped"] += 1
            return None

        # 时效门控
        ts = float(entry.get("ts", 0) or 0)
        if ts > 0 and (time.time() - ts) > self.max_signal_age_s:
            logger.debug(f"[LiveSignalReader] 信号过期 {signal_id} age={time.time()-ts:.0f}s")
            self._stats["skipped"] += 1
            return None

        # 方向
        direction = (entry.get("direction") or entry.get("signal_dir") or "").upper()
        if direction not in ("LONG", "SHORT"):
            self._stats["skipped"] += 1
            return None
        side = "BUY" if direction == "LONG" else "SELL"

        # 体制黑名单
        regime = (entry.get("regime") or "UNKNOWN").upper()
        if regime in BLOCKED_REGIMES:
            logger.info(f"[LiveSignalReader] 体制封锁 {entry.get('symbol')} regime={regime}")
            self._stats["skipped"] += 1
            return None

        symbol = entry.get("symbol", "")
        if not symbol:
            self._stats["skipped"] += 1
            return None

        # 价格
        price = float(entry.get("price") or entry.get("generated_price") or 0)
        if price <= 0:
            self._stats["skipped"] += 1
            return None

        # 入场区中点（用于限价单）
        entry_lo = float(entry.get("entry_lo") or price)
        entry_hi = float(entry.get("entry_hi") or price)
        limit_price = round((entry_lo + entry_hi) / 2, 8)

        # 止损/止盈
        sl = float(entry.get("stop_loss") or 0)
        tp1 = float(entry.get("tp1") or 0)

        raw_sig = RawSignal(
            symbol=symbol,
            side=side,
            score=score_raw / 175.0,   # 归一化到 0-1
            price=limit_price,
            quantity=0.0,              # 由 RiskKernel 计算 size
            regime=regime,
            ev_bucket_action="ALLOW",
            order_type="LIMIT",
            reduce_only=False,
            signal_id=signal_id or __import__("uuid").uuid4().hex,
            metadata={
                "score_raw": score_raw,
                "direction": direction,
                "sl": sl,
                "tp1": tp1,
                "ts_signal": ts,
                "regime": regime,
            },
        )

        self._stats["emitted"] += 1
        logger.info(
            f"[LiveSignalReader] ✅ 发出信号: {symbol} {direction} score={score_raw:.1f} "
            f"regime={regime} price={limit_price} id={signal_id[:8]}"
        )
        return raw_sig
