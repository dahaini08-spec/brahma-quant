#!/usr/bin/env python3
# ponytail: vpa_analyzer 382行，分析流程，上下文依赖深，拆分条件: 端到端测试覆盖>80%
"""
阶段2-③ VPA成交量行为识别模块（Volume Price Analysis）
=====================================
识别关键成交量行为模式：
  - 止跌量（Climax Selling / Stopping Volume）：大量+小振幅=主力接盘
  - 止涨量（Climax Buying / Buying Climax）：大量+上影线=主力派发
  - 派发量（Distribution Volume）：成交量放大但价格不涨
  - 吸筹量（Accumulation Volume）：成交量缩减后放量上涨
  - 无量上涨（No Supply）：量小但价格上涨=主力控盘
  - 无量下跌（No Demand）：量小但价格下跌=主力缺席

纯标准库实现，零依赖。

作者：设计院 2026-08-20
"""
from __future__ import annotations
import math
from typing import List, Dict, Tuple, Optional

# ── 常量 ─────────────────────────────────────────────────────────────────────
VOL_HIGH_THRESHOLD = 2.0   # 成交量 >= 均量 * 2 = 高量
VOL_LOW_THRESHOLD  = 0.5   # 成交量 <= 均量 * 0.5 = 低量
SPREAD_NARROW      = 0.4   # 振幅 <= 均振幅 * 0.4 = 窄幅
SPREAD_WIDE        = 1.5   # 振幅 >= 均振幅 * 1.5 = 宽幅
CLOSE_UPPER_PCTILE = 0.65  # 收盘在K线上65%以上 = 收盘强
CLOSE_LOWER_PCTILE = 0.35  # 收盘在K线下35%以下 = 收盘弱
UPPER_SHADOW_RATIO = 0.35  # 上影线占振幅35%以上 = 明显上影


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _close_position(o: float, h: float, l: float, c: float) -> float:
    """收盘价在K线振幅内的位置 [0=最低, 1=最高]"""
    rng = h - l
    if rng == 0:
        return 0.5
    return (c - l) / rng


def _upper_shadow_ratio(o: float, h: float, l: float, c: float) -> float:
    """上影线占总振幅的比例"""
    rng = h - l
    if rng == 0:
        return 0.0
    body_top = max(o, c)
    return (h - body_top) / rng


def _lower_shadow_ratio(o: float, h: float, l: float, c: float) -> float:
    """下影线占总振幅的比例"""
    rng = h - l
    if rng == 0:
        return 0.0
    body_bot = min(o, c)
    return (body_bot - l) / rng


def _body_ratio(o: float, h: float, l: float, c: float) -> float:
    """实体占总振幅的比例"""
    rng = h - l
    if rng == 0:
        return 0.0
    return abs(c - o) / rng


def _moving_avg(values: List[float], period: int) -> float:
    """简单移动平均"""
    if not values or period <= 0:
        return 0.0
    arr = values[-period:]
    return sum(arr) / len(arr)


# ── VPA分析器 ─────────────────────────────────────────────────────────────────

class VPAAnalyzer:
    """
    成交量行为分析器

    使用方式：
        analyzer = VPAAnalyzer(opens, highs, lows, closes, volumes)
        result = analyzer.analyze(n_bars=20)
    """

    def __init__(
        self,
        opens:   List[float],
        highs:   List[float],
        lows:    List[float],
        closes:  List[float],
        volumes: List[float],
    ):
        self.opens   = opens
        self.highs   = highs
        self.lows    = lows
        self.closes  = closes
        self.volumes = volumes
        self.n       = len(closes)

    def analyze(self, n_bars: int = 20) -> dict:
        """
        分析最近n_bars根K线的成交量行为

        Returns
        -------
        {
          vpa_signal     : str   主要信号名
          strength       : float [0,1] 信号强度
          direction_bias : str   'BULLISH'|'BEARISH'|'NEUTRAL'
          score_addon    : int   [-15, +15]
          patterns       : list  检测到的所有模式
          vol_condition  : str   'HIGH'|'LOW'|'NORMAL'
          summary        : str
        }
        """
        if self.n < 10:
            return self._empty_result('数据不足')

        # 取最近n_bars根
        n = min(n_bars, self.n)
        opens   = self.opens[-n:]
        highs   = self.highs[-n:]
        lows    = self.lows[-n:]
        closes  = self.closes[-n:]
        volumes = self.volumes[-n:]

        # 计算基准参数
        avg_vol    = _moving_avg(volumes, n)
        avg_spread = _moving_avg([h - l for h, l in zip(highs, lows)], n)

        if avg_vol == 0 or avg_spread == 0:
            return self._empty_result('量价数据异常')

        # 分析最近3根K线（最新信号）
        patterns = []
        recent_signals = []

        for i in range(-min(3, n), 0):
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
            spread = h - l

            vol_ratio    = v / avg_vol
            spread_ratio = spread / avg_spread
            close_pos    = _close_position(o, h, l, c)
            upper_shadow = _upper_shadow_ratio(o, h, l, c)
            lower_shadow = _lower_shadow_ratio(o, h, l, c)
            body_pct     = _body_ratio(o, h, l, c)
            is_bull_bar  = c > o

            # ━━━ 止跌量识别 ━━━
            # 条件：大量 + 窄幅 + 下影线长 + 收盘偏强
            if (vol_ratio >= VOL_HIGH_THRESHOLD and
                    spread_ratio <= SPREAD_NARROW * 1.5 and
                    lower_shadow >= 0.3 and
                    close_pos >= CLOSE_UPPER_PCTILE - 0.1):
                patterns.append({
                    'type': 'STOPPING_VOLUME',
                    'label': '止跌量🟢',
                    'signal': 'BULLISH',
                    'strength': min(vol_ratio / 3.0, 1.0) * 0.9 + lower_shadow * 0.1,
                    'bar_idx': i,
                    'detail': f'量比={vol_ratio:.1f}x 下影={lower_shadow:.0%} 位置={close_pos:.0%}',
                })
                recent_signals.append(('BULLISH', min(vol_ratio / 3.0, 1.0)))

            # ━━━ 止涨量/买入高潮识别 ━━━
            # 条件：大量 + 宽幅/窄幅 + 上影线长 + 收盘偏弱
            elif (vol_ratio >= VOL_HIGH_THRESHOLD and
                      upper_shadow >= UPPER_SHADOW_RATIO and
                      close_pos <= CLOSE_LOWER_PCTILE + 0.1):
                patterns.append({
                    'type': 'BUYING_CLIMAX',
                    'label': '止涨量🔴',
                    'signal': 'BEARISH',
                    'strength': min(vol_ratio / 3.0, 1.0) * 0.8 + upper_shadow * 0.2,
                    'bar_idx': i,
                    'detail': f'量比={vol_ratio:.1f}x 上影={upper_shadow:.0%} 位置={close_pos:.0%}',
                })
                recent_signals.append(('BEARISH', min(vol_ratio / 3.0, 1.0)))

            # ━━━ 派发量识别 ━━━
            # 条件：大量 + 价格不涨（收盘偏弱或下跌K线）
            elif (vol_ratio >= VOL_HIGH_THRESHOLD * 0.8 and
                      not is_bull_bar and
                      close_pos <= CLOSE_LOWER_PCTILE):
                patterns.append({
                    'type': 'DISTRIBUTION',
                    'label': '派发量🔴',
                    'signal': 'BEARISH',
                    'strength': vol_ratio / 4.0,
                    'bar_idx': i,
                    'detail': f'量比={vol_ratio:.1f}x 空头收盘 位置={close_pos:.0%}',
                })
                recent_signals.append(('BEARISH', vol_ratio / 4.0))

            # ━━━ 无量上涨（No Supply）识别 ━━━
            # 条件：低量 + 价格上涨 + 收盘偏强
            elif (vol_ratio <= VOL_LOW_THRESHOLD and
                      is_bull_bar and
                      close_pos >= CLOSE_UPPER_PCTILE):
                patterns.append({
                    'type': 'NO_SUPPLY',
                    'label': '无量上涨🟡',
                    'signal': 'BULLISH_WEAK',
                    'strength': 0.6,
                    'bar_idx': i,
                    'detail': f'量比={vol_ratio:.1f}x 多头收盘（主力控盘）',
                })
                recent_signals.append(('BULLISH', 0.5))

            # ━━━ 无量下跌（No Demand）识别 ━━━
            elif (vol_ratio <= VOL_LOW_THRESHOLD and
                      not is_bull_bar and
                      close_pos <= CLOSE_LOWER_PCTILE):
                patterns.append({
                    'type': 'NO_DEMAND',
                    'label': '无量下跌🟡',
                    'signal': 'BEARISH_WEAK',
                    'strength': 0.5,
                    'bar_idx': i,
                    'detail': f'量比={vol_ratio:.1f}x 空头收盘（主力缺席）',
                })
                recent_signals.append(('BEARISH', 0.4))

        # ━━━ 全局成交量趋势 ━━━
        vol_trend = self._analyze_vol_trend(volumes, closes)

        # ━━━ 综合判断 ━━━
        bullish_strength = sum(s for sig, s in recent_signals if sig == 'BULLISH')
        bearish_strength = sum(s for sig, s in recent_signals if sig == 'BEARISH')

        if bullish_strength > bearish_strength * 1.2:
            direction_bias = 'BULLISH'
            net_strength   = min(bullish_strength / 2.0, 1.0)
            score_addon    = int(net_strength * 15)
        elif bearish_strength > bullish_strength * 1.2:
            direction_bias = 'BEARISH'
            net_strength   = min(bearish_strength / 2.0, 1.0)
            score_addon    = -int(net_strength * 15)
        else:
            direction_bias = 'NEUTRAL'
            net_strength   = 0.3
            score_addon    = 0

        # 主信号
        if patterns:
            main_pattern = max(patterns, key=lambda x: x['strength'])
            vpa_signal = main_pattern['label']
        else:
            vpa_signal = '无明确信号'

        # 当前成交量条件
        latest_vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        if latest_vol_ratio >= VOL_HIGH_THRESHOLD:
            vol_condition = 'HIGH'
        elif latest_vol_ratio <= VOL_LOW_THRESHOLD:
            vol_condition = 'LOW'
        else:
            vol_condition = 'NORMAL'

        summary = (
            f"VPA: {vpa_signal} | {direction_bias} | "
            f"量条件={vol_condition}({latest_vol_ratio:.1f}x均量) | "
            f"评分:{score_addon:+d} | {vol_trend['label']}"
        )

        return {
            'vpa_signal':     vpa_signal,
            'strength':       round(net_strength, 3),
            'direction_bias': direction_bias,
            'score_addon':    score_addon,
            'patterns':       patterns,
            'vol_condition':  vol_condition,
            'vol_ratio_now':  round(latest_vol_ratio, 2),
            'vol_trend':      vol_trend,
            'summary':        summary,
        }

    def _analyze_vol_trend(self, volumes: List[float], closes: List[float]) -> dict:
        """分析成交量趋势（量价背离检测）"""
        n = len(volumes)
        if n < 8:
            return {'label': '数据不足', 'divergence': False}

        half = n // 2
        vol_early = sum(volumes[:half]) / half
        vol_late  = sum(volumes[half:]) / half
        price_early = closes[0]
        price_late  = closes[-1]

        vol_change   = (vol_late - vol_early) / max(vol_early, 1e-9)
        price_change = (price_late - price_early) / max(price_early, 1e-9)

        # 量价背离检测
        divergence = False
        label = ''

        if price_change > 0.02 and vol_change < -0.2:
            # 价涨量缩 = 上涨乏力
            divergence = True
            label = '⚠️价涨量缩（上涨乏力）'
        elif price_change < -0.02 and vol_change < -0.3:
            # 价跌量缩 = 下跌衰竭（底部特征）
            label = '🟡价跌量缩（下跌衰竭）'
        elif price_change > 0.02 and vol_change > 0.3:
            label = '🟢价涨量增（强势上涨）'
        elif price_change < -0.02 and vol_change > 0.3:
            label = '🔴价跌量增（下跌加速）'
        else:
            label = '量价同步（正常）'

        return {
            'label':       label,
            'divergence':  divergence,
            'vol_change':  round(vol_change, 3),
            'price_change': round(price_change, 3),
        }

    def _empty_result(self, reason: str) -> dict:
        return {
            'vpa_signal':     '无信号',
            'strength':       0.0,
            'direction_bias': 'NEUTRAL',
            'score_addon':    0,
            'patterns':       [],
            'vol_condition':  'NORMAL',
            'vol_ratio_now':  1.0,
            'vol_trend':      {'label': reason, 'divergence': False},
            'summary':        f'VPA: {reason}',
        }


# ── 便利接口 ──────────────────────────────────────────────────────────────────

def analyze_vpa(bars: list, n_bars: int = 20) -> dict:
    """
    便利接口：直接传入bars列表
    bars格式：[{'o':...,'h':...,'l':...,'c':...,'v':...}, ...]
    """
    opens   = [float(b.get('o', b.get('c', 0))) for b in bars]
    highs   = [float(b.get('h', 0)) for b in bars]
    lows    = [float(b.get('l', 0)) for b in bars]
    closes  = [float(b.get('c', 0)) for b in bars]
    volumes = [float(b.get('v', 0)) for b in bars]

    analyzer = VPAAnalyzer(opens, highs, lows, closes, volumes)
    return analyzer.analyze(n_bars=n_bars)


# ── 测试 ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import gzip, json, os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'historical')

    for symbol in ['BTCUSDT', 'ETHUSDT']:
        fpath = os.path.join(DATA, f'{symbol}_4h.jsonl.gz')
        if not os.path.exists(fpath):
            continue

        with gzip.open(fpath, 'rt') as f:
            bars = [json.loads(l) for l in f]

        result = analyze_vpa(bars, n_bars=20)

        print(f'\n{"="*60}')
        print(f'📊 {symbol} VPA成交量行为分析')
        print(f'{"="*60}')
        print(f'  {result["summary"]}')
        print(f'  方向偏向: {result["direction_bias"]}')
        print(f'  信号强度: {result["strength"]:.1%}')
        print(f'  评分加成: {result["score_addon"]:+d}')
        if result['patterns']:
            print(f'  识别到的模式:')
            for p in result['patterns']:
                print(f'    [{p["label"]}] {p["detail"]}')
        else:
            print(f'  无明确VPA模式')
        vt = result['vol_trend']
        print(f'  量价趋势: {vt["label"]} (价格{vt["price_change"]*100:+.1f}% 量能{vt["vol_change"]*100:+.0f}%)')
