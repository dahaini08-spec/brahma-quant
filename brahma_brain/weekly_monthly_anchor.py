#!/usr/bin/env python3
"""
阶段2-① 周线/月线大周期锚定模块
=====================================
从1D数据重采样生成周线/月线，提取大周期特征维度，
注入 fangcang_engine 的特征向量，提升方仓相似度匹配精度。

新增维度：
  W1  weekly_trend      [-1,+1]  周线趋势方向（EMA斜率）
  W2  weekly_rsi        [0,1]    周线RSI归一化
  W3  weekly_pos        [0,1]    当前价在52周高低范围内的位置
  W4  weekly_compress   [0,1]    周线BBW（布林带宽度压缩度）
  W5  monthly_trend     [-1,+1]  月线趋势方向
  W6  monthly_pos       [0,1]    当前价在12个月高低范围内的位置
  W7  weekly_vol_trend  [0,1]    近4周 vs 前4周成交量趋势
  W8  htf_confluence    [0,1]    周月线多空一致性（0=分歧，1=共振）

作者：设计院 2026-08-20
"""
from __future__ import annotations
import gzip
import json
import math
import os
import datetime
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

_DIR  = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, "..", "data")
_HIST = os.path.join(_DATA, "historical")

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _load_1d_bars(symbol: str) -> List[dict]:
    """加载合并1D数据（早期+主体）"""
    bars = []
    for fname in [f"{symbol}_1d_early.jsonl.gz", f"{symbol}_1d.jsonl.gz"]:
        path = os.path.join(_HIST, fname)
        if os.path.exists(path):
            with gzip.open(path, 'rt') as f:
                bars.extend(json.loads(l) for l in f if l.strip())
    # 去重排序
    seen: set = set()
    deduped = []
    for b in sorted(bars, key=lambda x: x.get('ts', 0)):
        k = b.get('ts', 0)
        if k not in seen:
            seen.add(k)
            deduped.append(b)
    return deduped


def _resample_to_weekly(bars_1d: List[dict]) -> List[dict]:
    """1D → 周线（ISO周，周一开盘~周日收盘）"""
    weeks: Dict[Tuple, List[dict]] = defaultdict(list)
    for b in bars_1d:
        dt = datetime.datetime.fromtimestamp(b['ts'] / 1000)
        key = dt.isocalendar()[:2]  # (year, week_number)
        weeks[key].append(b)
    
    result = []
    for key in sorted(weeks.keys()):
        wbars = sorted(weeks[key], key=lambda x: x['ts'])
        result.append({
            'ts':  wbars[0]['ts'],
            'o':   wbars[0].get('o', wbars[0].get('c', 0)),
            'h':   max(b.get('h', 0) for b in wbars),
            'l':   min(b.get('l', float('inf')) for b in wbars),
            'c':   wbars[-1].get('c', 0),
            'v':   sum(b.get('v', 0) for b in wbars),
            '_week': key,
        })
    return result


def _resample_to_monthly(bars_1d: List[dict]) -> List[dict]:
    """1D → 月线"""
    months: Dict[Tuple, List[dict]] = defaultdict(list)
    for b in bars_1d:
        dt = datetime.datetime.fromtimestamp(b['ts'] / 1000)
        key = (dt.year, dt.month)
        months[key].append(b)
    
    result = []
    for key in sorted(months.keys()):
        mbars = sorted(months[key], key=lambda x: x['ts'])
        result.append({
            'ts':  mbars[0]['ts'],
            'o':   mbars[0].get('o', mbars[0].get('c', 0)),
            'h':   max(b.get('h', 0) for b in mbars),
            'l':   min(b.get('l', float('inf')) for b in mbars),
            'c':   mbars[-1].get('c', 0),
            'v':   sum(b.get('v', 0) for b in mbars),
            '_month': key,
        })
    return result


def _ema(values: List[float], period: int) -> List[float]:
    """指数移动平均"""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: List[float], period: int = 14) -> float:
    """RSI计算"""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [-min(d, 0) for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def _bb_width(closes: List[float], period: int = 20) -> float:
    """布林带宽度（归一化）"""
    if len(closes) < period:
        return 0.05
    arr = closes[-period:]
    mid = sum(arr) / period
    if mid == 0:
        return 0.05
    variance = sum((x - mid) ** 2 for x in arr) / period
    std = math.sqrt(variance)
    return (2 * std) / mid


# ── 核心接口 ──────────────────────────────────────────────────────────────────

class WeeklyMonthlyAnchor:
    """
    大周期锚定引擎。
    使用方式：
        anchor = WeeklyMonthlyAnchor('BTCUSDT')
        features = anchor.get_features(current_price=71756)
    """

    def __init__(self, symbol: str = 'BTCUSDT'):
        self.symbol = symbol
        bars_1d = _load_1d_bars(symbol)
        self.weekly_bars  = _resample_to_weekly(bars_1d)
        self.monthly_bars = _resample_to_monthly(bars_1d)

    def get_features(self, current_price: Optional[float] = None) -> dict:
        """
        返回8维大周期特征（W1~W8），已归一化到[-1,1]或[0,1]

        Parameters
        ----------
        current_price : float, optional
            当前价格。若None，使用最新周线收盘价。

        Returns
        -------
        dict with keys: weekly_trend, weekly_rsi, weekly_pos,
                        weekly_compress, monthly_trend, monthly_pos,
                        weekly_vol_trend, htf_confluence,
                        _anchor_summary (human-readable)
        """
        wb = self.weekly_bars
        mb = self.monthly_bars

        if not wb or not mb:
            return self._empty_features()

        # 取近52周数据
        w52 = wb[-52:] if len(wb) >= 52 else wb
        w_closes = [b['c'] for b in w52]
        w_highs  = [b['h'] for b in w52]
        w_lows   = [b['l'] for b in w52]
        w_vols   = [b['v'] for b in w52]

        # 取近12个月数据
        m12 = mb[-12:] if len(mb) >= 12 else mb
        m_closes = [b['c'] for b in m12]
        m_highs  = [b['h'] for b in m12]
        m_lows   = [b['l'] for b in m12]

        cur_price = current_price or w_closes[-1]

        # W1: 周线趋势（EMA10 vs EMA30斜率方向，[-1,+1]）
        if len(w_closes) >= 30:
            ema10 = _ema(w_closes, 10)[-1]
            ema30 = _ema(w_closes, 30)[-1]
            ema10_prev = _ema(w_closes[:-4], 10)[-1] if len(w_closes) > 4 else ema10
            slope = (ema10 - ema10_prev) / max(ema10_prev, 1e-9)
            w_trend = max(-1.0, min(1.0, slope * 50))  # 归一化：2%斜率=±1
        elif len(w_closes) >= 10:
            slope = (w_closes[-1] - w_closes[-10]) / max(w_closes[-10], 1e-9)
            w_trend = max(-1.0, min(1.0, slope * 5))
        else:
            w_trend = 0.0

        # W2: 周线RSI [0,1]
        w_rsi = _rsi(w_closes, 14) / 100.0

        # W3: 当前价在52周高低范围的位置 [0,1]
        w52_high = max(w_highs) if w_highs else cur_price
        w52_low  = min(w_lows)  if w_lows  else cur_price
        price_range = w52_high - w52_low
        w_pos = (cur_price - w52_low) / price_range if price_range > 0 else 0.5

        # W4: 周线BBW（布林带压缩度，越小越压缩），已归一化 [0,1]
        w_compress = min(_bb_width(w_closes, min(20, len(w_closes))), 0.5) / 0.5  # 0.5=宽松，0=极压缩

        # W5: 月线趋势方向 [-1,+1]
        if len(m_closes) >= 6:
            m_slope = (m_closes[-1] - m_closes[-6]) / max(m_closes[-6], 1e-9)
            m_trend = max(-1.0, min(1.0, m_slope * 3))
        else:
            m_trend = 0.0

        # W6: 当前价在12个月高低范围的位置 [0,1]
        m12_high = max(m_highs) if m_highs else cur_price
        m12_low  = min(m_lows)  if m_lows  else cur_price
        m_range  = m12_high - m12_low
        m_pos    = (cur_price - m12_low) / m_range if m_range > 0 else 0.5

        # W7: 近4周 vs 前4周成交量趋势 [0,1]
        if len(w_vols) >= 8:
            recent_avg = sum(w_vols[-4:]) / 4
            prev_avg   = sum(w_vols[-8:-4]) / 4
            vol_ratio  = recent_avg / max(prev_avg, 1e-9)
            w_vol_trend = min(vol_ratio / 2.0, 1.0)  # 2x成交量=满分
        else:
            w_vol_trend = 0.5

        # W8: 周月线共振系数 [0,1]
        # 同向看多/看空=1.0，分歧=0.0
        w_bull = w_trend > 0.1 and w_pos > 0.4
        w_bear = w_trend < -0.1 and w_pos < 0.6
        m_bull = m_trend > 0.05 and m_pos > 0.4
        m_bear = m_trend < -0.05 and m_pos < 0.6
        if (w_bull and m_bull) or (w_bear and m_bear):
            htf_confluence = 0.85
        elif (w_bull and m_bear) or (w_bear and m_bull):
            htf_confluence = 0.15
        else:
            htf_confluence = 0.5  # 中性

        # 生成人类可读摘要
        w_trend_label = "🟢上涨" if w_trend > 0.2 else "🔴下跌" if w_trend < -0.2 else "🟡横盘"
        m_trend_label = "🟢上涨" if m_trend > 0.1 else "🔴下跌" if m_trend < -0.1 else "🟡横盘"
        confluence_label = "✅共振" if htf_confluence > 0.7 else "⚠️分歧" if htf_confluence < 0.3 else "中性"

        summary = (
            f"周线:{w_trend_label}(RSI={w_rsi*100:.0f}% 位置={w_pos*100:.0f}%区间) | "
            f"月线:{m_trend_label}(位置={m_pos*100:.0f}%区间) | "
            f"HTF共振:{confluence_label}({htf_confluence:.2f})"
        )

        return {
            # 8维特征（方仓相似度匹配用）
            'weekly_trend':    round(w_trend, 4),
            'weekly_rsi':      round(w_rsi, 4),
            'weekly_pos':      round(w_pos, 4),
            'weekly_compress': round(w_compress, 4),
            'monthly_trend':   round(m_trend, 4),
            'monthly_pos':     round(m_pos, 4),
            'weekly_vol_trend':round(w_vol_trend, 4),
            'htf_confluence':  round(htf_confluence, 4),
            # 原始数据（调试用）
            '_w52_high': w52_high,
            '_w52_low':  w52_low,
            '_m12_high': m12_high,
            '_m12_low':  m12_low,
            '_anchor_summary': summary,
        }

    def _empty_features(self) -> dict:
        return {
            'weekly_trend': 0.0, 'weekly_rsi': 0.5,
            'weekly_pos': 0.5,   'weekly_compress': 0.5,
            'monthly_trend': 0.0, 'monthly_pos': 0.5,
            'weekly_vol_trend': 0.5, 'htf_confluence': 0.5,
            '_anchor_summary': '无周月线数据',
        }


# ── 全局缓存（per symbol）────────────────────────────────────────────────────
_ANCHOR_CACHE: Dict[str, 'WeeklyMonthlyAnchor'] = {}

def get_anchor(symbol: str) -> 'WeeklyMonthlyAnchor':
    """获取（或创建）锚定引擎实例（单例）"""
    if symbol not in _ANCHOR_CACHE:
        _ANCHOR_CACHE[symbol] = WeeklyMonthlyAnchor(symbol)
    return _ANCHOR_CACHE[symbol]


# ── 直接调用测试 ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    for sym, price in [('BTCUSDT', 71756), ('ETHUSDT', 2281)]:
        print(f"\n{'='*60}")
        print(f"📊 {sym} 大周期锚定分析 | 当前价: ${price:,.0f}")
        print('='*60)
        anchor = WeeklyMonthlyAnchor(sym)
        feat = anchor.get_features(price)
        print(f"  {feat['_anchor_summary']}")
        print(f"  W1 周线趋势:  {feat['weekly_trend']:+.3f}")
        print(f"  W2 周线RSI:   {feat['weekly_rsi']*100:.1f}%")
        print(f"  W3 52周位置:  {feat['weekly_pos']*100:.1f}% (0%=52周低 100%=52周高)")
        print(f"  W4 周线BBW:   {feat['weekly_compress']*100:.1f}% (0%=极压缩)")
        print(f"  W5 月线趋势:  {feat['monthly_trend']:+.3f}")
        print(f"  W6 12月位置:  {feat['monthly_pos']*100:.1f}%")
        print(f"  W7 量能趋势:  {feat['weekly_vol_trend']*100:.1f}%")
        print(f"  W8 HTF共振:   {feat['htf_confluence']:.2f}")
        print(f"  52W区间: ${feat['_w52_low']:,.0f} ~ ${feat['_w52_high']:,.0f}")
        print(f"  12M区间: ${feat['_m12_low']:,.0f} ~ ${feat['_m12_high']:,.0f}")
