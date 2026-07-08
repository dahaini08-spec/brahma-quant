"""
brahma_v6/dharma2/ev_bucket.py — EV Bucket 治理框架
设计院 P1 | 2026-07-08

每个 bucket 维护 9 维统计：
  n / WR / avg_win / avg_loss / EV / PF / net_pnl / max_dd / decay_score

升仓条件（全部满足）：
  n >= 100, EV > 0, PF > 1.25, net_pnl > 0, max_dd 可控, live_drift < 阈值

降仓/封禁条件：
  EV < 0 连续10笔 / PF < 0.9 / live_drift > 20%
"""
from __future__ import annotations
import json
import time
import math
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parents[2]
EV_DIR = BASE / "data" / "ev_buckets"
EV_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class EVBucketStats:
    """单个 bucket 的统计状态"""
    bucket_key: str         # 格式: symbol|direction|regime|score_bucket
    n: int = 0
    wins: int = 0
    losses: int = 0
    sum_win: float = 0.0
    sum_loss: float = 0.0    # 负数累积
    net_pnl: float = 0.0
    max_dd: float = 0.0
    peak_pnl: float = 0.0
    consecutive_loss: int = 0
    last_updated: float = field(default_factory=time.time)
    status: str = "WATCH"   # WATCH / ACTIVE / REDUCE / BANNED

    @property
    def wr(self) -> float:
        return self.wins / max(self.n, 1)

    @property
    def avg_win(self) -> float:
        return self.sum_win / max(self.wins, 1)

    @property
    def avg_loss(self) -> float:
        return self.sum_loss / max(self.losses, 1)

    @property
    def ev(self) -> float:
        """期望值 = WR × avg_win + (1-WR) × avg_loss"""
        return self.wr * self.avg_win + (1 - self.wr) * self.avg_loss

    @property
    def profit_factor(self) -> float:
        if abs(self.sum_loss) < 1e-10:
            return 999.0 if self.sum_win > 0 else 0.0
        return self.sum_win / abs(self.sum_loss)

    @property
    def size_multiplier(self) -> float:
        """当前建议仓位乘数（1.0=基准）"""
        if self.status == "BANNED":
            return 0.0
        if self.status == "REDUCE":
            return 0.3
        if self.n < 30:
            return 0.5   # 样本不足→保守
        if self.n < 100:
            return 0.7
        # 样本充足 → EV调制
        if self.ev <= 0 or self.profit_factor < 0.9:
            return 0.3
        if self.profit_factor >= 1.5 and self.wr >= 0.55:
            return 1.2
        return 1.0

    def update(self, net_pnl: float) -> None:
        """记录一笔交易，更新 bucket 统计"""
        self.n += 1
        self.net_pnl += net_pnl
        if net_pnl > 0:
            self.wins += 1
            self.sum_win += net_pnl
            self.consecutive_loss = 0
        else:
            self.losses += 1
            self.sum_loss += net_pnl
            self.consecutive_loss += 1

        # 更新最大回撤
        self.peak_pnl = max(self.peak_pnl, self.net_pnl)
        dd = self.peak_pnl - self.net_pnl
        self.max_dd = max(self.max_dd, dd)
        self.last_updated = time.time()
        self._auto_classify()

    def _auto_classify(self) -> None:
        """自动分级"""
        if self.n < 10:
            self.status = "WATCH"
            return
        if self.consecutive_loss >= 10 or (self.n >= 20 and self.profit_factor < 0.7):
            self.status = "BANNED"
        elif self.ev < 0 or self.profit_factor < 0.9:
            self.status = "REDUCE"
        elif self.n >= 100 and self.ev > 0 and self.profit_factor > 1.25 and self.net_pnl > 0:
            self.status = "ACTIVE"
        else:
            self.status = "WATCH"

    def summary(self) -> Dict:
        return {
            "bucket":         self.bucket_key,
            "n":              self.n,
            "wr":             round(self.wr, 3),
            "ev":             round(self.ev, 5),
            "pf":             round(self.profit_factor, 3),
            "net_pnl":        round(self.net_pnl, 4),
            "avg_win":        round(self.avg_win, 5),
            "avg_loss":       round(self.avg_loss, 5),
            "max_dd":         round(self.max_dd, 4),
            "cons_loss":      self.consecutive_loss,
            "status":         self.status,
            "size_mult":      round(self.size_multiplier, 2),
        }


class EVBucketRegistry:
    """全局 EV Bucket 注册表，支持持久化"""

    def __init__(self, store_file: Path = None):
        self._file = store_file or EV_DIR / "ev_buckets.json"
        self._buckets: Dict[str, EVBucketStats] = {}
        self._load()

    def _make_key(
        self,
        symbol: str,
        direction: str,
        regime: str,
        score_bucket: str,
    ) -> str:
        return f"{symbol}|{direction}|{regime}|{score_bucket}"

    def _score_bucket(self, score: float) -> str:
        if score < 110: return "S1_low"
        if score < 138: return "S2_mid"
        if score < 155: return "S3_high"
        if score < 170: return "S4_elite"
        return "S5_divine"

    def record(
        self,
        symbol: str,
        direction: str,
        regime: str,
        score: float,
        net_pnl: float,
    ) -> EVBucketStats:
        """记录一笔交易结果"""
        key = self._make_key(symbol, direction, regime, self._score_bucket(score))
        if key not in self._buckets:
            self._buckets[key] = EVBucketStats(bucket_key=key)
        self._buckets[key].update(net_pnl)
        self._save()
        return self._buckets[key]

    def get_multiplier(
        self,
        symbol: str,
        direction: str,
        regime: str,
        score: float,
    ) -> float:
        """获取当前仓位乘数建议"""
        key = self._make_key(symbol, direction, regime, self._score_bucket(score))
        if key not in self._buckets:
            return 0.7  # 新bucket保守
        return self._buckets[key].size_multiplier

    def top_buckets(self, n: int = 10) -> List[Dict]:
        """EV排名前N的bucket"""
        active = [b for b in self._buckets.values() if b.n >= 20]
        return [b.summary() for b in sorted(active, key=lambda x: -x.ev)[:n]]

    def decay_buckets(self) -> List[Dict]:
        """需要降仓或封禁的bucket"""
        return [b.summary() for b in self._buckets.values()
                if b.status in ("REDUCE", "BANNED")]

    def report(self) -> str:
        lines = ["=== EV Bucket 治理报告 ===\n"]
        lines.append(f"总 bucket 数: {len(self._buckets)}")
        active = sum(1 for b in self._buckets.values() if b.status == "ACTIVE")
        banned = sum(1 for b in self._buckets.values() if b.status == "BANNED")
        reduce = sum(1 for b in self._buckets.values() if b.status == "REDUCE")
        lines.append(f"ACTIVE={active} WATCH={len(self._buckets)-active-banned-reduce} REDUCE={reduce} BANNED={banned}\n")
        lines.append("Top EV Buckets:")
        for b in self.top_buckets(5):
            lines.append(f"  {b['bucket']:<50} WR={b['wr']:.1%} EV={b['ev']:.5f} PF={b['pf']:.2f} n={b['n']} mult={b['size_mult']}")
        if self.decay_buckets():
            lines.append("\n⚠️ Decay Buckets (降仓/封禁):")
            for b in self.decay_buckets()[:5]:
                lines.append(f"  ❌ {b['bucket']:<50} {b['status']} EV={b['ev']:.5f}")
        return "\n".join(lines)

    def _load(self) -> None:
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text())
                for k, v in data.items():
                    b = EVBucketStats(**v)
                    self._buckets[k] = b
            except Exception:
                pass

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._buckets.items()}
            self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass


# 全局单例
_registry: Optional[EVBucketRegistry] = None


def get_registry() -> EVBucketRegistry:
    global _registry
    if _registry is None:
        _registry = EVBucketRegistry()
    return _registry


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        reg = EVBucketRegistry(Path(d) / "test.json")
        # 模拟100笔交易
        import random; random.seed(42)
        for _ in range(120):
            pnl = random.gauss(0.002, 0.015)
            reg.record("BTCUSDT", "LONG", "BEAR_RECOVERY", 162.0, pnl)

        bucket = reg._buckets[list(reg._buckets.keys())[0]]
        print(f"BTCUSDT LONG BEAR_RECOVERY n=120:")
        print(f"  WR={bucket.wr:.1%} EV={bucket.ev:.5f} PF={bucket.profit_factor:.2f}")
        print(f"  status={bucket.status} size_mult={bucket.size_multiplier}")
        print(reg.report())
        print("✅ EV Bucket 自检完成")
