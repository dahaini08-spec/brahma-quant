"""
brahma_v6/lake/data_lake.py — Polars + DuckDB 数据湖
设计院 × 顶级评估v6.0 Phase 3 | 2026-07-08

架构：
  Parquet Data Lake（按日/标的分区）
    ↓
  DuckDB 即席查询（零复制读取 Parquet）
    ↓
  Polars Lazy API 特征计算（端到端优化，流式处理超内存数据）
    ↓
  brahma_v6 Feature 层 → 信号引擎

数据分区策略：
  data/lake/
    klines/YYYY-MM-DD/<symbol>.parquet
    signals/YYYY-MM-DD/signals.parquet
    fills/YYYY-MM-DD/fills.parquet
    pnl/YYYY-MM-DD/pnl.parquet
    regime/YYYY-MM-DD/regime.parquet
"""
from __future__ import annotations
import json
import time
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

LAKE_DIR = BASE / "data" / "lake"
LAKE_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════
#  Polars 特征引擎
# ══════════════════════════════════════════════════════
class PolarsFeatureEngine:
    """
    基于 Polars Lazy API 计算交易特征。
    端到端优化 + 流式处理超内存数据。
    """

    def __init__(self):
        try:
            import polars as pl
            self.pl = pl
            self._available = True
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def compute_kline_features(self, klines: List[Dict]) -> Optional[Any]:
        """
        从K线数据计算技术特征。
        输入: [{"open","high","low","close","volume","timestamp"}, ...]
        输出: polars.LazyFrame
        """
        if not self._available or not klines:
            return None

        pl = self.pl
        df = pl.DataFrame(klines).lazy()

        # 确保列类型
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.collect_schema().names():
                df = df.with_columns(pl.col(col).cast(pl.Float64))

        df = df.with_columns([
            # ── RSI (近似) ───────────────────────────────
            pl.col("close").diff().alias("price_diff"),

            # ── 收益率 ───────────────────────────────────
            (pl.col("close").pct_change() * 100).alias("return_pct"),

            # ── 波动率（20期标准差） ──────────────────────
            pl.col("close").pct_change().rolling_std(window_size=20).alias("volatility_20"),

            # ── BB压缩（20期BB宽度/均值） ─────────────────
            (
                (pl.col("close").rolling_std(window_size=20) * 2 /
                 pl.col("close").rolling_mean(window_size=20)) * 100
            ).alias("bb_width_pct"),

            # ── 量比（当前量/20期均量） ───────────────────
            (pl.col("volume") / pl.col("volume").rolling_mean(window_size=20)).alias("vol_ratio"),

            # ── 高低点范围 ───────────────────────────────
            ((pl.col("high") - pl.col("low")) / pl.col("low") * 100).alias("range_pct"),

            # ── EMA 价格偏离 ─────────────────────────────
            (
                (pl.col("close") - pl.col("close").ewm_mean(span=20)) /
                pl.col("close").ewm_mean(span=20) * 100
            ).alias("ema20_dist_pct"),

            (
                (pl.col("close") - pl.col("close").ewm_mean(span=55)) /
                pl.col("close").ewm_mean(span=55) * 100
            ).alias("ema55_dist_pct"),
        ])

        return df

    def compute_signal_features(self, signals: List[Dict]) -> Optional[Any]:
        """
        从信号日志计算统计特征（用于Dharma2验证）
        """
        if not self._available or not signals:
            return None

        pl = self.pl
        df = pl.DataFrame(signals).lazy()

        # 信号有效性统计
        if "score" in df.collect_schema().names():
            df = df.with_columns([
                pl.col("score").cast(pl.Float64),
                pl.col("score").gt(155).alias("is_high_quality"),
            ])

        return df

    def to_parquet(self, lazy_frame: Any, path: Path) -> bool:
        """将LazyFrame写入Parquet"""
        if not self._available:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lazy_frame.collect().write_parquet(str(path))
            return True
        except Exception as e:
            print(f"[DataLake] Parquet写入失败: {e}")
            return False

    def read_parquet(self, path: Path) -> Optional[Any]:
        """读取Parquet为LazyFrame"""
        if not self._available or not path.exists():
            return None
        try:
            return self.pl.scan_parquet(str(path))
        except Exception:
            return None

    def scan_lake(self, table: str, date_from: str = None, date_to: str = None) -> Optional[Any]:
        """扫描整个数据湖分区（支持时间范围）"""
        if not self._available:
            return None
        pattern = LAKE_DIR / table / "**" / "*.parquet"
        try:
            lf = self.pl.scan_parquet(str(pattern))
            return lf
        except Exception:
            return None


# ══════════════════════════════════════════════════════
#  DuckDB 即席查询引擎
# ══════════════════════════════════════════════════════
class DuckDBQueryEngine:
    """
    DuckDB 直接查询 Parquet 文件，零复制，极低延迟。
    适合：回测查询、特征分析、性能报告、归因分析。
    """

    def __init__(self):
        try:
            import duckdb
            self._db = duckdb.connect(str(LAKE_DIR / "brahma.duckdb"))
            self._available = True
            self._init_views()
        except ImportError:
            self._available = False
            self._db = None

    @property
    def available(self) -> bool:
        return self._available

    def _init_views(self) -> None:
        """初始化常用视图"""
        if not self._available:
            return
        try:
            # 信号视图
            signals_path = str(LAKE_DIR / "signals/**/*.parquet")
            self._db.execute(f"""
                CREATE OR REPLACE VIEW v_signals AS
                SELECT * FROM read_parquet('{signals_path}', hive_partitioning=true)
            """)
            # PnL视图
            pnl_path = str(LAKE_DIR / "pnl/**/*.parquet")
            self._db.execute(f"""
                CREATE OR REPLACE VIEW v_pnl AS
                SELECT * FROM read_parquet('{pnl_path}', hive_partitioning=true)
            """)
        except Exception:
            pass  # 文件不存在时视图创建失败是正常的

    def query(self, sql: str) -> List[Dict]:
        """执行SQL查询，返回记录列表"""
        if not self._available:
            return []
        try:
            result = self._db.execute(sql).fetchall()
            cols = [d[0] for d in self._db.description]
            return [dict(zip(cols, row)) for row in result]
        except Exception as e:
            print(f"[DuckDB] 查询失败: {e}")
            return []

    def query_signal_performance(self, days: int = 30) -> Dict:
        """信号性能汇总查询"""
        if not self._available:
            return {}
        sql = f"""
        SELECT
            regime,
            direction,
            COUNT(*) as total_signals,
            AVG(score) as avg_score,
            SUM(CASE WHEN valid_signal THEN 1 ELSE 0 END) as valid_count,
            AVG(CASE WHEN valid_signal THEN score ELSE NULL END) as avg_valid_score
        FROM v_signals
        WHERE ts_iso >= NOW() - INTERVAL '{days} days'
        GROUP BY regime, direction
        ORDER BY total_signals DESC
        """
        return self.query(sql)

    def query_pnl_by_regime(self) -> List[Dict]:
        """按体制分层的PnL分析"""
        if not self._available:
            return []
        sql = """
        SELECT
            regime_at_entry,
            direction,
            COUNT(*) as trades,
            AVG(net_pnl) as avg_net_pnl,
            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate,
            AVG(gross_pnl) as avg_gross_pnl,
            AVG(fee_drag + slippage_drag + funding_drag) as avg_total_cost,
            SUM(net_pnl) as total_net_pnl
        FROM v_pnl
        GROUP BY regime_at_entry, direction
        ORDER BY total_net_pnl DESC
        """
        return self.query(sql)

    def query_feature_attribution(self) -> List[Dict]:
        """特征归因分析（外部评估v6.0要求）"""
        if not self._available:
            return []
        sql = """
        SELECT
            feature_name,
            AVG(contribution) as avg_contribution,
            COUNT(*) as n_signals,
            AVG(CASE WHEN net_pnl > 0 THEN contribution ELSE NULL END) as contrib_on_win
        FROM (
            SELECT
                p.net_pnl,
                json_extract_string(fa.key, '$') as feature_name,
                CAST(fa.value AS DOUBLE) as contribution
            FROM v_pnl p,
            LATERAL FLATTEN(input => p.feature_attribution) fa
        )
        GROUP BY feature_name
        ORDER BY avg_contribution DESC
        """
        return self.query(sql)

    def get_stats(self) -> Dict:
        """数据湖统计"""
        if not self._available:
            return {"available": False, "reason": "duckdb not installed"}
        try:
            # 统计各表行数
            tables = {}
            for view in ["v_signals", "v_pnl"]:
                try:
                    result = self._db.execute(f"SELECT COUNT(*) FROM {view}").fetchone()
                    tables[view] = result[0] if result else 0
                except Exception:
                    tables[view] = 0
            return {
                "available": True,
                "views": tables,
                "lake_dir": str(LAKE_DIR),
                "db_file": str(LAKE_DIR / "brahma.duckdb"),
            }
        except Exception as e:
            return {"available": False, "error": str(e)}


# ══════════════════════════════════════════════════════
#  数据湖写入器
# ══════════════════════════════════════════════════════
class LakeWriter:
    """
    实时写入数据到Parquet数据湖。
    支持追加写入（先读再合并）。
    """

    def __init__(self):
        self._polars = PolarsFeatureEngine()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def write_signal(self, signal: Dict) -> bool:
        """写入信号记录到湖"""
        if not self._polars.available:
            return False
        pl = self._polars.pl
        date = self._today()
        path = LAKE_DIR / "signals" / date / "signals.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)

        # 标准化字段
        row = {
            "ts": float(signal.get("ts", time.time())),
            "ts_iso": str(signal.get("ts_iso", "")),
            "symbol": str(signal.get("symbol", "")),
            "direction": str(signal.get("direction", signal.get("signal_dir", ""))),
            "regime": str(signal.get("regime", "")),
            "score": float(signal.get("score", 0)),
            "grade": str(signal.get("grade", "")),
            "action": str(signal.get("action", "")),
            "valid_signal": bool(signal.get("valid_signal", False)),
            "blocked": bool(signal.get("blocked", True)),
            "price": float(signal.get("price", 0)),
            "trace_id": str(signal.get("trace_id", "")),
        }
        try:
            new_df = pl.DataFrame([row])
            if path.exists():
                existing = pl.read_parquet(str(path))
                combined = pl.concat([existing, new_df])
            else:
                combined = new_df
            combined.write_parquet(str(path))
            return True
        except Exception as e:
            print(f"[LakeWriter] 信号写入失败: {e}")
            return False

    def write_pnl(self, pnl: Dict) -> bool:
        """写入PnL归因记录"""
        if not self._polars.available:
            return False
        pl = self._polars.pl
        date = self._today()
        path = LAKE_DIR / "pnl" / date / "pnl.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": float(pnl.get("ts", time.time())),
            "symbol": str(pnl.get("symbol", "")),
            "direction": str(pnl.get("direction", "")),
            "gross_pnl": float(pnl.get("gross_pnl", 0)),
            "net_pnl": float(pnl.get("net_pnl", 0)),
            "fee_drag": float(pnl.get("fee_drag", 0)),
            "slippage_drag": float(pnl.get("slippage_drag", 0)),
            "funding_drag": float(pnl.get("funding_drag", 0)),
            "holding_hours": float(pnl.get("holding_hours", 0)),
            "regime_at_entry": str(pnl.get("regime_at_entry", "")),
            "regime_at_exit": str(pnl.get("regime_at_exit", "")),
            "signal_score": float(pnl.get("signal_score", 0)),
            "trace_id": str(pnl.get("trace_id", "")),
        }
        try:
            new_df = pl.DataFrame([row])
            if path.exists():
                existing = pl.read_parquet(str(path))
                combined = pl.concat([existing, new_df])
            else:
                combined = new_df
            combined.write_parquet(str(path))
            return True
        except Exception as e:
            print(f"[LakeWriter] PnL写入失败: {e}")
            return False

    def ingest_signal_log(self, jsonl_path: Path = None) -> int:
        """
        将现有 live_signal_log.jsonl 批量导入数据湖。
        迁移入口：一次性把历史信号纳入Parquet体系。
        """
        if not self._polars.available:
            return 0
        jsonl_path = jsonl_path or (BASE / "data" / "live_signal_log.jsonl")
        if not jsonl_path.exists():
            return 0
        count = 0
        try:
            with jsonl_path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        signal = json.loads(line)
                        self.write_signal(signal)
                        count += 1
                    except Exception:
                        continue
        except Exception:
            pass
        return count


# ══════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════
_polars_engine: Optional[PolarsFeatureEngine] = None
_duck_engine: Optional[DuckDBQueryEngine] = None
_lake_writer: Optional[LakeWriter] = None


def get_polars() -> PolarsFeatureEngine:
    global _polars_engine
    if _polars_engine is None:
        _polars_engine = PolarsFeatureEngine()
    return _polars_engine


def get_duck() -> DuckDBQueryEngine:
    global _duck_engine
    if _duck_engine is None:
        _duck_engine = DuckDBQueryEngine()
    return _duck_engine


def get_writer() -> LakeWriter:
    global _lake_writer
    if _lake_writer is None:
        _lake_writer = LakeWriter()
    return _lake_writer


if __name__ == "__main__":
    print("=== Polars + DuckDB 数据湖自检 ===\n")

    polars_engine = PolarsFeatureEngine()
    duck_engine = DuckDBQueryEngine()
    writer = LakeWriter()

    print(f"Polars: {'✅ ' + __import__('polars').__version__ if polars_engine.available else '❌ 未安装'}")
    print(f"DuckDB: {'✅ ' + __import__('duckdb').__version__ if duck_engine.available else '❌ 未安装'}")

    # 测试K线特征计算
    if polars_engine.available:
        import random, math
        price = 62000.0
        klines = []
        for i in range(50):
            price *= (1 + random.gauss(0, 0.005))
            klines.append({"open": price*0.999, "high": price*1.002, "low": price*0.997,
                           "close": price, "volume": random.uniform(500, 2000), "timestamp": time.time()+i*3600})
        lf = polars_engine.compute_kline_features(klines)
        df = lf.collect()
        print(f"\nK线特征计算: {df.shape[0]}行 × {df.shape[1]}列")
        print(f"  特征列: {df.columns[:6]}...")

    # 测试信号导入
    imported = writer.ingest_signal_log()
    print(f"\n历史信号导入: {imported} 条 → Parquet数据湖")

    # DuckDB统计
    stats = duck_engine.get_stats()
    print(f"\nDuckDB统计: {stats}")

    print("\n✅ 数据湖自检完成")
