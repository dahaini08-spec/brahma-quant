"""
analysis_snapshot.py — 分析快照锁定机制
梵天固化SOP v1.0 · 6方联合封印 2026-07-01

核心设计：
  每次分析开始时锁定一个数据快照ID，同一 snapshot_id 内
  所有子计算使用同一时刻的价格、体制、ATR数据，消除时序漂移。

使用方式（brahma_analysis_runner.py 已集成）：
    snap = AnalysisSnapshot('BTCUSDT')
    snap.lock(price=59000, regime='BEAR_TREND', atr_4h=1200)
    # 后续所有计算使用 snap.price / snap.regime / snap.atr_4h
    # 不再重新拉取，确保同一次分析内数据一致
"""

import time
from datetime import datetime, timezone


class AnalysisSnapshot:
    """
    数据快照锁定容器。

    核心原则：
      - 每次 brahma_analysis_runner.run_batch() 调用创建一个 snapshot
      - BTC 和 ETH 共享同一个 batch_id，保证两者时间戳一致
      - 同一 regime_version 下重跑 score 差异 < ±2分（95%置信）
    """

    def __init__(self, symbol: str, batch_id: str = None):
        self.symbol      = symbol.upper()
        self.locked_at   = time.time()
        self.utc_str     = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        # batch_id: 多标的分析时共享，确保BTC/ETH用同一时间基准
        self.batch_id    = batch_id or self.utc_str
        self.snapshot_id = f"{self.symbol}_{self.batch_id}"

        # 锁定字段（由 lock() 写入，之后只读）
        self.price        = None   # 快照时价格
        self.regime       = None   # 快照时体制
        self.regime_ver   = None   # 体制版本号（来自 RegimeStateMachine）
        self.atr_4h       = None   # 快照时 ATR_4H
        self._locked      = False

    def lock(self, price: float, regime: str,
             atr_4h: float = None, regime_ver: int = None):
        """
        锁定快照数据。只能调用一次，之后字段不可修改。
        """
        if self._locked:
            return  # 幂等：已锁定则忽略重复调用
        self.price       = float(price) if price else None
        self.regime      = regime
        self.regime_ver  = regime_ver
        self.atr_4h      = float(atr_4h) if atr_4h else None
        self._locked     = True

    @property
    def regime_tag(self) -> str:
        """返回带版本号的体制标签，如 BEAR_TREND@v12"""
        if self.regime and self.regime_ver is not None:
            return f"{self.regime}@v{self.regime_ver}"
        return self.regime or '?'

    @property
    def age_seconds(self) -> float:
        """快照已存在多少秒"""
        return time.time() - self.locked_at

    @property
    def is_stale(self) -> bool:
        """快照是否超过5分钟（超时需重新分析）"""
        return self.age_seconds > 300

    def to_dict(self) -> dict:
        return {
            'snapshot_id': self.snapshot_id,
            'symbol':      self.symbol,
            'batch_id':    self.batch_id,
            'locked_at':   self.locked_at,
            'utc_str':     self.utc_str,
            'price':       self.price,
            'regime':      self.regime,
            'regime_tag':  self.regime_tag,
            'regime_ver':  self.regime_ver,
            'atr_4h':      self.atr_4h,
            'age_seconds': round(self.age_seconds, 1),
            'is_stale':    self.is_stale,
        }

    def __repr__(self):
        return (f"<AnalysisSnapshot {self.snapshot_id} "
                f"price={self.price} regime={self.regime_tag} "
                f"age={self.age_seconds:.0f}s>")


class BatchSnapshot:
    """
    多标的批量分析的共享快照管理器。
    BTC 和 ETH 共享同一个 batch_id，确保时序一致性。

    使用方式：
        batch = BatchSnapshot(['BTCUSDT', 'ETHUSDT'])
        btc_snap = batch.get('BTCUSDT')
        eth_snap = batch.get('ETHUSDT')
        # 两者 batch_id 相同 → 视为同一时刻分析
    """

    def __init__(self, symbols: list):
        self.batch_id   = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        self.created_at = time.time()
        self.snapshots  = {
            s.upper(): AnalysisSnapshot(s, batch_id=self.batch_id)
            for s in symbols
        }

    def get(self, symbol: str) -> AnalysisSnapshot:
        return self.snapshots.get(symbol.upper())

    def lock_all(self, results: dict):
        """
        批量锁定：从 analyze() 结果中提取价格/体制/ATR 并锁定快照。
        results: {symbol: analyze_result}
        """
        for sym, r in results.items():
            snap = self.snapshots.get(sym.upper())
            if not snap:
                continue
            ms = r.get('ms', {})
            price    = ms.get('price') or r.get('price')
            regime   = ms.get('regime') or r.get('regime', 'CHOP_MID')
            atr_4h   = ms.get('atr', {}).get('4h') if isinstance(ms.get('atr'), dict) else None
            reg_ver  = r.get('regime_version') or r.get('_regime_version')
            snap.lock(price=price, regime=regime, atr_4h=atr_4h, regime_ver=reg_ver)

    def summary(self) -> str:
        lines = [f'BatchSnapshot batch_id={self.batch_id}']
        for sym, snap in self.snapshots.items():
            lines.append(f'  {sym}: {snap.regime_tag} price={snap.price} atr4h={snap.atr_4h}')
        return '\n'.join(lines)
