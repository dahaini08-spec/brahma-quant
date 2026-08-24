# ponytail: volatility_context 316行，有意为之，重构前先 grep 所有调用方
"""
volatility_context.py — HCME M5: 波动率历史分位引擎
设计院封印 2026-08-08 · 苏摩111批准

用6.8年历史数据为当前波动率定位历史分位，
输出 atr_percentile / bbw_percentile / vol_regime / expansion_prob_24h / historical_context
"""

import json
import os
import math
import statistics
from typing import Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_BASE, 'data', 'backtest')
_CACHE_DIR = os.path.join(_BASE, 'data')

# 波动率体制阈值（ATR百分位）
_REGIME_MAP = [
    (85, 'EXTREME'),
    (65, 'HIGH'),
    (35, 'NORMAL'),
    (0,  'LOW'),
]

def _vol_regime(pct: float) -> str:
    for threshold, label in _REGIME_MAP:
        if pct >= threshold:
            return label
    return 'LOW'


def _calc_atr14(highs, lows, closes):
    """Wilder ATR14 序列"""
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return []
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    period = 14
    if len(trs) < period:
        return []
    atr_vals = [sum(trs[:period]) / period]
    for i in range(period, len(trs)):
        atr_vals.append((atr_vals[-1] * (period - 1) + trs[i]) / period)
    return atr_vals


def _calc_bbw(closes, period=20):
    """Bollinger Band Width 序列（占价格百分比）"""
    n = len(closes)
    if n < period:
        return []
    bbw_vals = []
    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        mid = sum(window) / period
        var = sum((x - mid) ** 2 for x in window) / period
        std = math.sqrt(var)
        bbw = (2 * 2 * std / mid) * 100 if mid > 0 else 0
        bbw_vals.append(bbw)
    return bbw_vals


def _load_4h_klines(symbol: str):
    """加载4H K线数据，返回 (highs, lows, closes) 列表"""
    fname = f'{symbol.upper()}_4h.json'
    fpath = os.path.join(_DATA_DIR, fname)
    if not os.path.exists(fpath):
        return None, None, None
    with open(fpath) as f:
        data = json.load(f)
    # 格式: [open_time, open, high, low, close, volume, ...]
    highs  = [float(k[2]) for k in data]
    lows   = [float(k[3]) for k in data]
    closes = [float(k[4]) for k in data]
    return highs, lows, closes


def _build_percentile_table(symbol: str) -> dict:
    """
    预计算 ATR14 + BBW 分位数查找表
    缓存到 data/vol_percentile_{sym}.json
    """
    sym = symbol.upper().replace('USDT', '')
    cache_file = os.path.join(_CACHE_DIR, f'vol_percentile_{sym.lower()}.json')

    # 若缓存已存在且数据充足则直接返回
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cached = json.load(f)
            if len(cached.get('atr_series', [])) > 1000:
                return cached
        except Exception:
            pass

    highs, lows, closes = _load_4h_klines(symbol)
    if highs is None:
        return {}

    atr_series = _calc_atr14(highs, lows, closes)
    bbw_series = _calc_bbw(closes)

    # 对齐长度（ATR比BBW少 period-1 个）
    min_len = min(len(atr_series), len(bbw_series))
    atr_series = atr_series[-min_len:]
    bbw_series = bbw_series[-min_len:]
    prices_aligned = closes[-(min_len):]

    result = {
        'symbol': symbol,
        'atr_series': atr_series,
        'bbw_series': bbw_series,
        'prices':     prices_aligned,
        'count':      min_len,
    }

    # 写缓存
    try:
        with open(cache_file, 'w') as f:
            json.dump(result, f)
    except Exception:
        pass

    return result


def _percentile_rank(series: list, value: float) -> float:
    """计算 value 在 series 中的百分位（0-100）"""
    if not series:
        return 50.0
    below = sum(1 for x in series if x < value)
    return round(below / len(series) * 100, 1)


def _expansion_prob_24h(atr_pct: float, bbw_pct: float, vol_regime: str) -> float:
    """
    估计24小时内波动率扩张的概率
    基于历史统计规律：
    - BBW极度压缩（<35分位）→ 扩张概率高
    - 高ATR分位 → 可能继续扩张或均值回归
    """
    # 基础概率模型（基于布林带压缩/ATR分位历史统计）
    if vol_regime == 'LOW':
        # 低波动 → 极高扩张概率（均值回归+压缩后释放）
        base = 0.78
        bbw_factor = max(0, (35 - bbw_pct) / 35) * 0.15
    elif vol_regime == 'NORMAL':
        base = 0.52
        bbw_factor = max(0, (50 - bbw_pct) / 50) * 0.1
    elif vol_regime == 'HIGH':
        # 高波动 → 可能继续扩张但概率递减
        base = 0.45
        bbw_factor = (bbw_pct - 65) / 35 * 0.1
    else:  # EXTREME
        base = 0.35
        bbw_factor = -0.05
    return round(min(0.95, max(0.15, base + bbw_factor)), 2)


def _historical_context_text(symbol: str, atr_pct: float, atr_val: float,
                               series: list, prices: list) -> str:
    """
    生成历史情境描述文本
    找到历史上ATR分位相近的时期，分析后续48H表现
    """
    if not series or not prices or atr_pct < 0:
        return ''

    n = len(series)
    if n < 200:
        return ''

    # 找历史上ATR分位相近的K线（±10分位）
    lo_q = max(0, atr_pct - 10)
    hi_q = min(100, atr_pct + 10)
    lo_val = sorted(series)[int(len(series) * lo_q / 100)]
    hi_val = sorted(series)[int(len(series) * hi_q / 100)]

    # 统计后续48H（12根4H K线）涨跌
    returns_48h = []
    for i in range(n - 12):
        if lo_val <= series[i] <= hi_val:
            ret = (prices[i + 12] - prices[i]) / prices[i] * 100 if prices[i] > 0 else 0
            returns_48h.append(ret)

    if len(returns_48h) < 10:
        return ''

    avg_ret = statistics.mean(returns_48h)
    pos_count = sum(1 for r in returns_48h if r > 0)
    wr = pos_count / len(returns_48h)

    sym_short = symbol.upper().replace('USDT', '')
    direction = '上涨' if avg_ret > 0 else '下跌'
    return (f'当前{sym_short} ATR处于历史第{atr_pct:.0f}百分位，'
            f'历史相似波动率后续48H平均{direction}{abs(avg_ret):.1f}%，'
            f'胜率（看多）{wr:.0%}（n={len(returns_48h)}）')


class VolatilityContext:
    """HCME M5: 波动率历史分位引擎"""

    def __init__(self):
        self._tables = {}

    def _get_table(self, symbol: str) -> dict:
        key = symbol.upper()
        if key not in self._tables:
            self._tables[key] = _build_percentile_table(key)
        return self._tables[key]

    def get_percentile(self, symbol: str,
                       current_atr: float,
                       current_bbw: float) -> dict:
        """
        返回当前ATR/BBW的历史分位及波动率体制

        Args:
            symbol:       交易对，如 'BTCUSDT'
            current_atr:  当前ATR绝对值（价格单位）
            current_bbw:  当前BBW百分比（如 0.02 表示 2%，也接受 2.0）

        Returns:
            {
              "atr_percentile": 65,
              "bbw_percentile": 30,
              "vol_regime": "HIGH",
              "expansion_prob_24h": 0.73,
              "historical_context": "...",
              "score_adj": 0,   # 对梵天35维评分的建议调整
            }
        """
        try:
            table = self._get_table(symbol)
            if not table or not table.get('atr_series'):
                return self._fallback(current_atr, current_bbw)

            atr_series = table['atr_series']
            bbw_series = table['bbw_series']
            prices     = table.get('prices', [])

            # BBW归一化：若传入小数（0.02）转换为百分比（2.0）
            if current_bbw < 1.0:
                current_bbw = current_bbw * 100

            atr_pct = _percentile_rank(atr_series, current_atr)
            bbw_pct = _percentile_rank(bbw_series, current_bbw)

            regime = _vol_regime(atr_pct)
            exp_prob = _expansion_prob_24h(atr_pct, bbw_pct, regime)
            ctx_text = _historical_context_text(symbol, atr_pct, current_atr,
                                                 atr_series, prices)

            # score_adj：极低波动率（Low/压缩）→ 不建议激进入场，+0
            # 极高波动率（Extreme）→ 建议缩仓，-5
            if regime == 'EXTREME':
                score_adj = -5
            elif regime == 'LOW' and bbw_pct < 20:
                score_adj = +3  # 压缩后释放，奖励
            else:
                score_adj = 0

            return {
                'atr_percentile':     round(atr_pct, 1),
                'bbw_percentile':     round(bbw_pct, 1),
                'vol_regime':         regime,
                'expansion_prob_24h': exp_prob,
                'historical_context': ctx_text,
                'score_adj':          score_adj,
                'current_atr':        round(current_atr, 4),
                'current_bbw':        round(current_bbw, 4),
            }
        except Exception as e:
            return self._fallback(current_atr, current_bbw, error=str(e))

    def _fallback(self, atr: float, bbw: float, error: str = '') -> dict:
        return {
            'atr_percentile':     50.0,
            'bbw_percentile':     50.0,
            'vol_regime':         'NORMAL',
            'expansion_prob_24h': 0.50,
            'historical_context': '',
            'score_adj':          0,
            'current_atr':        round(atr, 4),
            'current_bbw':        round(bbw, 4),
            '_fallback':          True,
            '_error':             error,
        }


# ── 模块级缓存实例 ───────────────────────────────────────────────────
_vc_instance: Optional[VolatilityContext] = None


def get_volatility_context(symbol: str, current_atr: float,
                            current_bbw: float) -> dict:
    """便捷函数入口"""
    global _vc_instance
    if _vc_instance is None:
        _vc_instance = VolatilityContext()
    return _vc_instance.get_percentile(symbol, current_atr, current_bbw)


if __name__ == '__main__':
    # 快速测试
    vc = VolatilityContext()
    result = vc.get_percentile('BTCUSDT', 800, 0.02)
    print(json.dumps(result, ensure_ascii=False, indent=2))
