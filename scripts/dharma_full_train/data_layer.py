#!/usr/bin/env python3
"""
达摩院数据层 v1.0 — 统一访问接口
设计院 2026-06-02

架构：
  data/dharma_8y/    训练集 2019-09~2024-12  ← 唯一训练数据源
  data/dharma_oos/   OOS验证 2024-11~2026-05 ← 只读，禁止混入训练
  data/klines/       原始文件（保留，勿删）

使用：
  from scripts.dharma_full_train.data_layer import DataLayer
  dl = DataLayer()
  btc_1h = dl.load_train('BTCUSDT', '1h')   # 训练集
  btc_oos = dl.load_oos('BTCUSDT', '1h')    # OOS验证
"""
import os, json, datetime

BASE     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRAIN_DIR = os.path.join(BASE, 'data', 'dharma_8y')
OOS_DIR   = os.path.join(BASE, 'data', 'dharma_oos')

# 严格截止线（防穿越）
TRAIN_CUTOFF    = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
TRAIN_CUTOFF_MS = int(TRAIN_CUTOFF.timestamp() * 1000)


class DataLayer:
    def __init__(self):
        self._train_cache = {}
        self._oos_cache   = {}

    def load_train(self, symbol: str, interval: str) -> list:
        """加载训练集 K线，自动穿越检查"""
        sym = symbol.upper()
        if not sym.endswith('USDT'): sym += 'USDT'
        key = f"{sym}_{interval}"
        if key in self._train_cache:
            return self._train_cache[key]

        fp = os.path.join(TRAIN_DIR, f"{sym}_{interval}_train.json")
        if not os.path.exists(fp):
            raise FileNotFoundError(f"训练集不存在: {fp}\n请运行 download_8y_data.py")

        bars = json.load(open(fp))
        # 严格穿越检查
        leaks = [b for b in bars if b[0] >= TRAIN_CUTOFF_MS]
        if leaks:
            raise ValueError(
                f"数据穿越！{len(leaks)}条训练数据超过截止线 {TRAIN_CUTOFF.date()}\n"
                f"首条泄漏: {datetime.datetime.fromtimestamp(leaks[0][0]//1000)}\n"
                "请重新下载训练集"
            )
        self._train_cache[key] = bars
        return bars

    def load_oos(self, symbol: str, interval: str) -> list:
        """加载OOS验证集，禁止混入训练"""
        sym = symbol.upper()
        if not sym.endswith('USDT'): sym += 'USDT'
        key = f"{sym}_{interval}"
        if key in self._oos_cache:
            return self._oos_cache[key]

        # OOS文件格式
        for pattern in [f"{sym}_{interval}_20241102_20260401.json",
                         f"{sym}_{interval}_20250131_20260501.json"]:
            fp = os.path.join(OOS_DIR, pattern)
            if os.path.exists(fp):
                bars = json.load(open(fp))
                self._oos_cache[key] = bars
                return bars
        raise FileNotFoundError(f"OOS数据不存在: {sym} {interval}")

    def split_train_oos(self, symbol: str, interval: str,
                        oos_start: str = '2024-01-01') -> tuple:
        """
        从训练集切分 train/validation
        oos_start: 验证集起始日（默认最后1年作为验证）
        返回: (train_bars, val_bars)
        """
        bars = self.load_train(symbol, interval)
        cut_ms = int(datetime.datetime.strptime(oos_start, '%Y-%m-%d')
                     .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
        train = [b for b in bars if b[0] < cut_ms]
        val   = [b for b in bars if b[0] >= cut_ms]
        return train, val

    def stats(self):
        """打印数据层统计"""
        print("=== 达摩院数据层 ===")
        print(f"训练集 ({TRAIN_DIR.split('/')[-1]}):")
        for f in sorted(os.listdir(TRAIN_DIR)):
            if not f.endswith('.json') or f in ('manifest.json',): continue
            bars = json.load(open(f"{TRAIN_DIR}/{f}"))
            if not bars: continue
            t0 = datetime.datetime.fromtimestamp(bars[0][0]//1000).strftime('%Y-%m-%d')
            t1 = datetime.datetime.fromtimestamp(bars[-1][0]//1000).strftime('%Y-%m-%d')
            print(f"  {f:<35} {len(bars):>7}条  {t0}~{t1}")


if __name__ == '__main__':
    dl = DataLayer()
    dl.stats()
    # 验证穿越保护
    btc = dl.load_train('BTC', '1h')
    train, val = dl.split_train_oos('BTC', '1h', '2024-01-01')
    print(f"\n切分验证: 训练={len(train)}条  验证={len(val)}条")
    print("✅ 数据层正常")
