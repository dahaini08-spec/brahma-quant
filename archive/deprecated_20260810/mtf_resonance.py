"""
mtf_resonance.py — HCME M6: 多周期共振验证引擎
设计院封印 2026-08-08 · 苏摩111批准

检查15m信号是否与1H/4H/日线结构方向一致，
计算共振强度评分，为梵天35维评分提供加成。
"""

import json
import sys
import os
from typing import Optional

_BB_DIR = os.path.dirname(os.path.abspath(__file__))
if _BB_DIR not in sys.path:
    sys.path.insert(0, _BB_DIR)

# 周期权重（大周期赋予更高权重）
_TF_WEIGHTS = {
    '1d':  4,
    '4h':  3,
    '1h':  2,
    '15m': 1,
}

# 共振评分阈值 → bonus
_SCORE_TO_BONUS = [
    (90, +20),
    (75, +15),
    (60, +10),
    (40, +5),
    (0,   0),
]


def _get_direction(symbol: str, interval: str, limit: int = 100) -> str:
    """
    获取指定周期的方向: 'LONG' / 'SHORT' / 'NEUTRAL'
    使用 EMA50 + EMA200 + RSI 综合判断
    """
    try:
        from data_cache import get_klines, klines_to_ohlcv
        raw = get_klines(symbol, interval, limit)
        if not raw:
            return 'NEUTRAL'
        ohlcv = klines_to_ohlcv(raw)
        closes = ohlcv.get('c', [])
        if len(closes) < 50:
            return 'NEUTRAL'

        # EMA计算
        def _ema(series, period):
            if len(series) < period:
                return series[-1] if series else 0
            k = 2 / (period + 1)
            val = sum(series[:period]) / period
            for x in series[period:]:
                val = x * k + val * (1 - k)
            return val

        # RSI计算
        def _rsi(series, n=14):
            if len(series) < n + 1:
                return 50.0
            gains, losses = [], []
            for i in range(1, len(series)):
                d = series[i] - series[i - 1]
                gains.append(max(d, 0))
                losses.append(max(-d, 0))
            ag = sum(gains[-n:]) / n
            al = sum(losses[-n:]) / n
            return round(100 - 100 / (1 + ag / al), 1) if al else 100

        ema50  = _ema(closes, 50)
        ema200 = _ema(closes, min(200, len(closes)))
        rsi    = _rsi(closes)
        price  = closes[-1]

        score = 0
        # 价格 vs EMA50
        if price > ema50:
            score += 2
        elif price < ema50:
            score -= 2

        # EMA50 vs EMA200
        if ema50 > ema200:
            score += 2
        elif ema50 < ema200:
            score -= 2

        # RSI
        if rsi > 55:
            score += 1
        elif rsi < 45:
            score -= 1

        # 近期价格动量（最近10根K线）
        if len(closes) >= 10:
            if closes[-1] > closes[-10]:
                score += 1
            elif closes[-1] < closes[-10]:
                score -= 1

        if score >= 3:
            return 'LONG'
        elif score <= -3:
            return 'SHORT'
        else:
            return 'NEUTRAL'

    except Exception:
        return 'NEUTRAL'


def _alignment_score(tf_alignment: dict, signal_direction: str) -> float:
    """
    计算方向一致性分数（0-100）
    权重加权：大周期更重要
    """
    total_weight = 0
    aligned_weight = 0

    for tf, direction in tf_alignment.items():
        w = _TF_WEIGHTS.get(tf, 1)
        total_weight += w
        if direction == signal_direction:
            aligned_weight += w
        elif direction == 'NEUTRAL':
            aligned_weight += w * 0.5  # 中性方向算半分

    if total_weight == 0:
        return 50.0

    return round(aligned_weight / total_weight * 100, 1)


def _score_to_bonus(score: float) -> int:
    """共振分数 → 梵天score加成"""
    for threshold, bonus in _SCORE_TO_BONUS:
        if score >= threshold:
            return bonus
    return 0


def _build_warning(tf_alignment: dict, signal_direction: str,
                   current_price: float) -> str:
    """生成风险警告文本"""
    warnings = []
    opposite = 'SHORT' if signal_direction == 'LONG' else 'LONG'

    # 检查大周期是否反向
    for tf in ['1d', '4h']:
        dir_ = tf_alignment.get(tf, 'NEUTRAL')
        if dir_ == opposite:
            tf_name = '日线' if tf == '1d' else '4H'
            warnings.append(f'{tf_name}级别方向为{opposite}，注意逆势风险')

    # 检查仅1H+15m共振（大周期不支持）
    big_tf_align = [
        tf_alignment.get('1d', 'NEUTRAL') == signal_direction,
        tf_alignment.get('4h', 'NEUTRAL') == signal_direction,
    ]
    small_tf_align = [
        tf_alignment.get('1h', 'NEUTRAL') == signal_direction,
        tf_alignment.get('15m', 'NEUTRAL') == signal_direction,
    ]
    if not any(big_tf_align) and all(small_tf_align):
        warnings.append('仅小周期共振，缺乏大周期结构支撑')

    return '；'.join(warnings) if warnings else ''


class MTFResonance:
    """HCME M6: 多周期共振验证引擎"""

    def check(self, symbol: str, direction: str,
              current_price: float) -> dict:
        """
        检查15m信号是否与1H/4H/日线结构方向一致

        Args:
            symbol:        交易对，如 'BTCUSDT'
            direction:     信号方向 'LONG' / 'SHORT'
            current_price: 当前价格

        Returns:
            {
              "resonance_score": 85,
              "tf_alignment": {
                "15m": "LONG", "1h": "LONG", "4h": "LONG", "1d": "NEUTRAL"
              },
              "confirmation_count": 3,
              "score_bonus": +15,
              "warning": "..."
            }
        """
        try:
            direction = direction.upper()
            if direction not in ('LONG', 'SHORT'):
                direction = 'LONG'

            # 并发获取各周期方向（顺序执行，各有超时保护）
            tf_alignment = {}
            timeframes = [
                ('15m', 50),
                ('1h',  100),
                ('4h',  100),
                ('1d',  100),
            ]
            for tf, limit in timeframes:
                try:
                    tf_alignment[tf] = _get_direction(symbol, tf, limit)
                except Exception:
                    tf_alignment[tf] = 'NEUTRAL'

            # 计算共振分数
            res_score = _alignment_score(tf_alignment, direction)

            # 方向一致的周期数量
            confirmation_count = sum(
                1 for d in tf_alignment.values()
                if d == direction
            )

            # 梵天score加成
            bonus = _score_to_bonus(res_score)

            # 风险警告
            warning = _build_warning(tf_alignment, direction, current_price)

            return {
                'resonance_score':    round(res_score, 1),
                'tf_alignment':       tf_alignment,
                'confirmation_count': confirmation_count,
                'score_bonus':        bonus,
                'warning':            warning,
                'signal_direction':   direction,
            }

        except Exception as e:
            return {
                'resonance_score':    50.0,
                'tf_alignment':       {},
                'confirmation_count': 0,
                'score_bonus':        0,
                'warning':            '',
                '_fallback':          True,
                '_error':             str(e),
            }


# ── 模块级缓存实例 ───────────────────────────────────────────────────
_mtf_instance: Optional[MTFResonance] = None


def check_mtf_resonance(symbol: str, direction: str,
                         current_price: float) -> dict:
    """便捷函数入口"""
    global _mtf_instance
    if _mtf_instance is None:
        _mtf_instance = MTFResonance()
    return _mtf_instance.check(symbol, direction, current_price)


if __name__ == '__main__':
    import requests
    btc_price = float(
        requests.get(
            'https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT',
            timeout=5
        ).json()['price']
    )
    print(f'BTC当前价: ${btc_price:,.0f}')
    mtf = MTFResonance()
    result = mtf.check('BTCUSDT', 'LONG', btc_price)
    print(json.dumps(result, ensure_ascii=False, indent=2))
