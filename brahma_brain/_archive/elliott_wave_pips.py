#!/usr/bin/env python3
# ponytail: elliott_wave_pips 463行，有意为之，重构前先 grep 所有调用方
"""
阶段2-② Elliott Wave PIPs扩展模块
=====================================
基于Perceptually Important Points（PIPs）识别Elliott Wave结构：
  - 三浪（ABC回调）：Zigzag / Flat / Triangle
  - 五浪（12345推动）：Impulse Wave

纯标准库实现，零依赖。

接口：
    from brahma_brain.elliott_wave_pips import ElliottWaveDetector
    detector = ElliottWaveDetector(closes, highs, lows)
    result = detector.analyze()
    # result = {wave_type, wave_count, confidence, score_addon, summary}

作者：设计院 2026-08-20
"""
from __future__ import annotations
import math
from typing import List, Tuple, Optional

# ── PIPs提取算法 ──────────────────────────────────────────────────────────────

def extract_pips(prices: List[float], n_pips: int = 9) -> List[Tuple[int, float]]:
    """
    提取Perceptually Important Points（感知重要点）
    使用最大垂直距离法（Maximum Vertical Distance）
    
    参数：
        prices: 价格序列
        n_pips: 目标PIPs数量（默认9个）
    
    返回：
        [(index, price), ...] 按时间排序
    """
    if len(prices) < 3:
        return [(i, p) for i, p in enumerate(prices)]
    
    # 初始：首尾两点
    pips = [0, len(prices) - 1]
    
    while len(pips) < n_pips and len(pips) < len(prices):
        max_dist = -1.0
        max_idx = -1
        
        for i in range(len(pips) - 1):
            left_i, right_i = pips[i], pips[i + 1]
            if right_i - left_i < 2:
                continue
            
            # 从left到right的线段
            p_left  = prices[left_i]
            p_right = prices[right_i]
            
            # 逐点计算垂直距离
            for j in range(left_i + 1, right_i):
                # 插值
                t = (j - left_i) / (right_i - left_i)
                interp = p_left + t * (p_right - p_left)
                dist = abs(prices[j] - interp)
                # 归一化（相对于本段价格范围）
                seg_range = abs(p_right - p_left) or 1.0
                dist_norm = dist / seg_range
                if dist_norm > max_dist:
                    max_dist = dist_norm
                    max_idx = j
        
        if max_idx == -1:
            break
        
        # 插入新点并保持排序
        pips.append(max_idx)
        pips = sorted(set(pips))
    
    return [(i, prices[i]) for i in pips]


# ── Elliott Wave识别 ──────────────────────────────────────────────────────────

class ElliottWaveDetector:
    """
    从PIPs提取Elliott Wave结构。
    
    使用最近N根K线（默认50根4H=约8天），
    提取9个PIPs后判断波浪结构。
    """

    # 五浪规则（宽松版，适合自动识别）
    IMPULSE_RULES = {
        # Wave 3不能是最短的推动浪
        'w3_not_shortest': True,
        # Wave 4不能进入Wave 1的价格区域（允许10%容忍）
        'w4_w1_overlap_tol': 0.10,
        # Wave 5回撤比Wave 3不超过
        'w5_extension_max': 1.618,
    }

    def __init__(self, closes: List[float], highs: List[float], lows: List[float], n_bars: int = 60):
        # 取最近n_bars根K线
        self.closes = closes[-n_bars:]
        self.highs  = highs[-n_bars:]
        self.lows   = lows[-n_bars:]
        self.n_bars = len(self.closes)

    def analyze(self) -> dict:
        """
        主分析入口，返回波浪分析结果

        Returns
        -------
        {
          wave_type   : 'IMPULSE'|'ZIGZAG'|'FLAT'|'TRIANGLE'|'UNKNOWN'
          wave_count  : int (已识别的浪数)
          direction   : 'UP'|'DOWN'|'NEUTRAL'
          confidence  : float [0,1]
          current_wave: str  (当前处于第几浪，如'W3','W4','W5')
          fib_levels  : dict (关键斐波那契水平)
          score_addon : int  (-10 ~ +15)
          summary     : str
        }
        """
        if self.n_bars < 20:
            return self._empty_result('数据不足')

        # 提取PIPs（使用中间价格减少噪音）
        mid_prices = [(h + l) / 2 for h, l in zip(self.highs, self.lows)]
        pips = extract_pips(mid_prices, n_pips=9)

        if len(pips) < 5:
            return self._empty_result('PIPs不足')

        # 尝试识别推动浪（5浪）
        impulse = self._try_impulse(pips)
        if impulse['confidence'] >= 0.6:
            return impulse

        # 尝试识别回调浪（3浪ABC）
        corrective = self._try_corrective(pips)
        if corrective['confidence'] >= 0.5:
            return corrective

        # 未识别，返回基本信息
        return self._basic_trend_analysis(pips)

    def _try_impulse(self, pips: List[Tuple[int, float]]) -> dict:
        """
        尝试识别推动五浪结构
        需要5~9个PIPs，找 1-2-3-4-5 结构
        """
        if len(pips) < 5:
            return {'confidence': 0.0, 'wave_type': 'UNKNOWN'}

        # 取最近5~6个PIPs组成候选浪
        candidates = pips[-6:] if len(pips) >= 6 else pips

        best_conf = 0.0
        best_result = {'confidence': 0.0, 'wave_type': 'UNKNOWN'}

        # 尝试不同起始点
        for start_idx in range(max(0, len(candidates) - 5), len(candidates) - 4):
            seg = candidates[start_idx:start_idx + 5]
            if len(seg) < 5:
                continue

            pts = [p for _, p in seg]
            result = self._check_impulse_rules(pts, seg)
            if result['confidence'] > best_conf:
                best_conf = result['confidence']
                best_result = result

        return best_result

    def _check_impulse_rules(self, pts: List[float], seg: List[Tuple]) -> dict:
        """
        检验5个点是否满足Elliott推动浪规则
        pts: [W0, W1, W2, W3, W4, W5] or [W0..W4]（5个点=4段）
        """
        if len(pts) < 5:
            return {'confidence': 0.0, 'wave_type': 'UNKNOWN'}

        w0, w1, w2, w3, w4 = pts[:5]

        # 判断方向（上涨五浪 or 下跌五浪）
        direction = 'UP' if w1 > w0 else 'DOWN'

        conf = 0.0
        rules_passed = 0
        total_rules = 5

        if direction == 'UP':
            wave1 = w1 - w0
            wave2 = w1 - w2  # 回调
            wave3 = w3 - w2
            wave4 = w3 - w4  # 回调
            # wave5会是w5-w4（如果有第6个点）

            # Rule 1: Wave 2不能超过Wave 0（不能跌破起点）
            if w2 > w0:
                conf += 0.25
                rules_passed += 1

            # Rule 2: Wave 3是推动浪中最强的（不能是最短）
            if wave1 > 0 and wave3 > 0:
                # Wave 3 至少比 Wave 1 的61.8%强
                if wave3 >= wave1 * 0.618:
                    conf += 0.25
                    rules_passed += 1

            # Rule 3: Wave 4不进入Wave 1的价格区域
            if w4 > w1 * 0.9:  # 允许10%重叠
                conf += 0.2
                rules_passed += 1

            # Rule 4: Wave 3高于Wave 1（新高）
            if w3 > w1:
                conf += 0.15
                rules_passed += 1

            # Rule 5: Wave 2回调不超过Wave 1的100%
            if wave1 > 0 and wave2 / wave1 < 1.0:
                conf += 0.15
                rules_passed += 1

        else:  # DOWN
            wave1 = w0 - w1
            wave2 = w2 - w1  # 反弹
            wave3 = w2 - w3
            wave4 = w4 - w3  # 反弹

            if w2 < w0:
                conf += 0.25
                rules_passed += 1
            if wave1 > 0 and wave3 > 0 and wave3 >= wave1 * 0.618:
                conf += 0.25
                rules_passed += 1
            if w4 < w1 * 1.1:
                conf += 0.2
                rules_passed += 1
            if w3 < w1:
                conf += 0.15
                rules_passed += 1
            if wave1 > 0 and wave2 / wave1 < 1.0:
                conf += 0.15
                rules_passed += 1

        # 判断当前处于第几浪（用于交易决策）
        if conf >= 0.6:
            # 已完成4段（W0~W4），可能在W5（最后推动浪）
            current_wave = 'W5_POTENTIAL'
            score_addon = 12  # 五浪末端 = 趋势结束信号，反向高价值
        elif conf >= 0.4:
            current_wave = 'W4_CORRECTION'
            score_addon = 8   # W4回调 = 入场好时机
        else:
            current_wave = 'UNCLEAR'
            score_addon = 0

        # 计算斐波那契目标位
        fib_levels = {}
        if direction == 'UP' and conf >= 0.5:
            base = w0
            ext = w3
            rng = ext - base
            fib_levels = {
                'fib_1.000': round(ext, 2),
                'fib_1.272': round(base + rng * 1.272, 2),
                'fib_1.618': round(base + rng * 1.618, 2),
                'retrace_0.382': round(ext - rng * 0.382, 2),
                'retrace_0.618': round(ext - rng * 0.618, 2),
            }
        elif direction == 'DOWN' and conf >= 0.5:
            base = w0
            ext = w3
            rng = base - ext
            fib_levels = {
                'fib_1.000': round(ext, 2),
                'fib_1.272': round(base - rng * 1.272, 2),
                'fib_1.618': round(base - rng * 1.618, 2),
                'retrace_0.382': round(ext + rng * 0.382, 2),
                'retrace_0.618': round(ext + rng * 0.618, 2),
            }

        summary_dir = '📈上涨' if direction == 'UP' else '📉下跌'
        summary = (
            f"Elliott五浪{summary_dir} | "
            f"当前:{current_wave} | 置信:{conf:.0%} | "
            f"({rules_passed}/{total_rules}规则通过)"
        )

        return {
            'wave_type':    'IMPULSE',
            'wave_count':   5,
            'direction':    direction,
            'confidence':   round(conf, 3),
            'current_wave': current_wave,
            'fib_levels':   fib_levels,
            'score_addon':  score_addon,
            'summary':      summary,
        }

    def _try_corrective(self, pips: List[Tuple[int, float]]) -> dict:
        """
        识别回调三浪（ABC结构）
        """
        if len(pips) < 4:
            return {'confidence': 0.0, 'wave_type': 'UNKNOWN'}

        seg = pips[-4:]  # 取最近4个PIPs（3段=ABC）
        pts = [p for _, p in seg]
        a0, a, b, c = pts

        direction = 'UP' if a > a0 else 'DOWN'

        conf = 0.0
        wave_type = 'ZIGZAG'

        if direction == 'UP':
            wave_a = a - a0
            wave_b = a - b   # 回调
            wave_c = c - b   # 推动

            # Zigzag: C超过A高点
            if c > a and wave_b / wave_a < 0.618:
                conf += 0.4
                wave_type = 'ZIGZAG'

            # Flat: B接近A的起点，C接近A
            elif b > a0 * 0.9 and abs(c - a) / max(wave_a, 1e-9) < 0.2:
                conf += 0.35
                wave_type = 'FLAT'

            # 通用ABC验证
            if wave_b / max(wave_a, 1e-9) < 1.0:  # B不超过A
                conf += 0.2
            if wave_c / max(wave_a, 1e-9) > 0.618:  # C至少是A的61.8%
                conf += 0.2

        else:  # DOWN
            wave_a = a0 - a
            wave_b = b - a   # 反弹
            wave_c = b - c   # 下跌

            if c < a and wave_b / wave_a < 0.618:
                conf += 0.4
                wave_type = 'ZIGZAG'
            elif b < a0 * 1.1 and abs(c - a) / max(wave_a, 1e-9) < 0.2:
                conf += 0.35
                wave_type = 'FLAT'

            if wave_b / max(wave_a, 1e-9) < 1.0:
                conf += 0.2
            if wave_c / max(wave_a, 1e-9) > 0.618:
                conf += 0.2

        current_wave = 'WAVE_C' if conf >= 0.5 else 'WAVE_B'
        # C浪尾端 = 回调结束，入场好时机
        score_addon = 10 if current_wave == 'WAVE_C' else 5

        dir_label = '🟢' if direction == 'UP' else '🔴'
        summary = (
            f"Elliott {wave_type}三浪{dir_label} | "
            f"当前:{current_wave} | 置信:{conf:.0%}"
        )

        return {
            'wave_type':    wave_type,
            'wave_count':   3,
            'direction':    direction,
            'confidence':   round(min(conf, 1.0), 3),
            'current_wave': current_wave,
            'fib_levels':   {},
            'score_addon':  score_addon,
            'summary':      summary,
        }

    def _basic_trend_analysis(self, pips: List[Tuple]) -> dict:
        """无法识别结构时，返回基本趋势分析"""
        if len(pips) < 2:
            return self._empty_result('PIPs不足')

        prices = [p for _, p in pips]
        first, last = prices[0], prices[-1]
        move = (last - first) / max(abs(first), 1e-9)

        direction = 'UP' if move > 0.01 else 'DOWN' if move < -0.01 else 'NEUTRAL'
        turns = sum(
            1 for i in range(1, len(prices) - 1)
            if (prices[i] > prices[i-1] and prices[i] > prices[i+1]) or
               (prices[i] < prices[i-1] and prices[i] < prices[i+1])
        )

        summary = f"结构未识别 | 方向:{direction} | 转折点:{turns}个 | 近期变化:{move*100:+.1f}%"

        return {
            'wave_type':    'UNKNOWN',
            'wave_count':   turns + 1,
            'direction':    direction,
            'confidence':   0.2,
            'current_wave': 'UNKNOWN',
            'fib_levels':   {},
            'score_addon':  0,
            'summary':      summary,
        }

    def _empty_result(self, reason: str = '') -> dict:
        return {
            'wave_type':    'UNKNOWN',
            'wave_count':   0,
            'direction':    'NEUTRAL',
            'confidence':   0.0,
            'current_wave': 'UNKNOWN',
            'fib_levels':   {},
            'score_addon':  0,
            'summary':      f'Elliott分析: {reason}',
        }


# ── 测试 ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import gzip, json, os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'historical')

    for symbol in ['BTCUSDT', 'ETHUSDT']:
        fpath = os.path.join(DATA, f'{symbol}_4h.jsonl.gz')
        if not os.path.exists(fpath):
            print(f'  {symbol}: 数据文件不存在')
            continue

        with gzip.open(fpath, 'rt') as f:
            bars = [json.loads(l) for l in f]

        closes = [b['c'] for b in bars]
        highs  = [b['h'] for b in bars]
        lows   = [b['l'] for b in bars]

        detector = ElliottWaveDetector(closes, highs, lows, n_bars=60)
        result = detector.analyze()

        print(f'\n{"="*60}')
        print(f'📊 {symbol} Elliott Wave分析')
        print(f'{"="*60}')
        print(f'  {result["summary"]}')
        print(f'  波浪类型: {result["wave_type"]}')
        print(f'  当前浪位: {result["current_wave"]}')
        print(f'  置信度:  {result["confidence"]:.1%}')
        print(f'  评分加成: {result["score_addon"]:+d}')
        if result['fib_levels']:
            print(f'  斐波那契目标位:')
            for k, v in result['fib_levels'].items():
                print(f'    {k}: ${v:,.1f}')

        # 测试PIPs
        mid = [(h + l) / 2 for h, l in zip(highs[-60:], lows[-60:])]
        pips = extract_pips(mid, n_pips=9)
        print(f'  PIPs({len(pips)}点): ', end='')
        for i, (idx, price) in enumerate(pips):
            trend = '↑' if i > 0 and price > pips[i-1][1] else '↓' if i > 0 else ''
            print(f'${price:,.0f}{trend}', end='  ')
        print()
