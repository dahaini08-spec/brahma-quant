"""
smart_money_engine.py — 聪明钱流向分析引擎
设计院 2026-07-01 · Glassnode盲区替代方案

替代能力：
  Glassnode链上巨鲸流向 ≈ 80%（用Binance大户持仓比+持仓趋势代理）
  CryptoQuant矿工流向 ≈ 60%（用OI结构+大户背离间接推断）

核心逻辑：
  1. 大户账户多空比(topLongShortAccountRatio) = 大户方向偏好
  2. 大户持仓多空比(topLongShortPositionRatio) = 大户实际仓位
  3. 散户账户多空比(globalLongShortAccountRatio) = 散户行为
  4. 大户-散户背离 = 聪明钱方向（背离大→大户与散户反向）
  5. 持仓趋势 = 大户是在加仓还是减仓

数据源：Binance fapi futures/data（免费公开，无需API Key）
"""


# ╔══ INTERFACE CONTRACT ═══════════════════════════════════════════╗
# ║ Interface : get_smart_money_signal(symbol)->dict
# ║ Output    : {signal, score_adj, big_pos_long, whale_retail_gap, confidence}
# ║ Call Freq : 随brahma_core评分，2分钟缓存
# ║ Deps      : requests(fapi futures/data)
# ╚════════════════════════════════════════════════════════════════╝
import requests
import time
from typing import Optional

_CACHE: dict = {}
_CACHE_TTL = 120  # 2分钟


def _get(url: str, timeout: int = 8) -> Optional[list]:
    now = time.time()
    if url in _CACHE and now - _CACHE[url]['ts'] < _CACHE_TTL:
        return _CACHE[url]['data']
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        _CACHE[url] = {'data': data, 'ts': now}
        return data
    except Exception:
        return None


def get_smart_money_signal(symbol: str = 'BTCUSDT') -> dict:
    """
    聪明钱流向综合分析

    返回：
      big_acct_long     : 大户账户多仓比（0~1）
      big_pos_long      : 大户持仓多仓比（0~1）
      retail_long       : 散户多仓比（0~1）
      whale_retail_gap  : 大户-散户多仓背离（正=大户比散户更多）
      pos_trend_5h      : 大户持仓5小时趋势（正=在加多仓）
      signal            : SMART_MONEY_SHORT / SMART_MONEY_LONG / DIVERGE_SHORT / NEUTRAL
      score_adj         : 评分调整（做空视角，负=大户看多=不利做空）
      note              : 描述
      confidence        : 数据置信度 0~1
    """
    default = {
        'big_acct_long': 0.5, 'big_pos_long': 0.5, 'retail_long': 0.5,
        'whale_retail_gap': 0.0, 'pos_trend_5h': 0.0,
        'signal': 'NEUTRAL', 'score_adj': 0, 'note': 'N/A', 'confidence': 0.0
    }

    cache_key = f'smart_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']

    try:
        acc_url = f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={symbol}&period=1h&limit=6'
        pos_url = f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=1h&limit=6'
        ret_url = f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=6'

        acc_data = _get(acc_url)
        pos_data = _get(pos_url)
        ret_data = _get(ret_url)

        if not acc_data or not pos_data or not ret_data:
            default['note'] = '数据拉取失败'
            return default

        # 最新值
        ba = float(acc_data[0]['longAccount'])   # 大户账户多仓比
        bp = float(pos_data[0]['longAccount'])   # 大户持仓多仓比
        rr = float(ret_data[0]['longAccount'])   # 散户多仓比

        # 背离指标
        whale_gap = round(ba - rr, 4)   # 正=大户比散户更多
        pos_gap   = round(ba - bp, 4)   # 大户账户多 但持仓少 = 大户实际轻仓（口是心非）

        # 持仓趋势（5H变化）
        n = min(len(acc_data), len(pos_data), 6)
        pos_trend = float(pos_data[0]['longAccount']) - float(pos_data[n-1]['longAccount'])

        # 信号逻辑
        score_adj = 0
        notes = []

        # 核心判断1：大户持仓偏空（<0.45）→ 聪明钱做空
        if bp < 0.45:
            score_adj += 4
            notes.append(f'大户持仓偏空({bp:.2%}) 聪明钱看空 +4')
        elif bp < 0.50:
            score_adj += 2
            notes.append(f'大户持仓中性偏空({bp:.2%}) +2')
        elif bp > 0.60:
            score_adj -= 3
            notes.append(f'大户持仓偏多({bp:.2%}) 聪明钱看多 -3')

        # 核心判断2：大户-散户背离
        if whale_gap < -0.05:
            # 大户比散户更空 = 强烈看空信号
            score_adj += 3
            notes.append(f'大户比散户更空(差{whale_gap:.3f}) 背离做空 +3')
        elif whale_gap > 0.08:
            # 大户比散户更多 = 聪明钱反向做多，做空风险
            score_adj -= 2
            notes.append(f'大户比散户更多(差{whale_gap:.3f}) 注意反转 -2')

        # 核心判断3：持仓趋势（过去5H大户在加什么仓）
        if pos_trend < -0.02:
            # 大户在减多仓/加空仓
            score_adj += 2
            notes.append(f'大户5H减多仓({pos_trend:+.3f}) 趋势做空 +2')
        elif pos_trend > 0.03:
            score_adj -= 1
            notes.append(f'大户5H加多仓({pos_trend:+.3f}) 逆势预警 -1')

        # 综合信号
        if score_adj >= 5:
            signal = 'SMART_MONEY_SHORT'
        elif score_adj <= -3:
            signal = 'SMART_MONEY_LONG'
        elif whale_gap < -0.03 and bp < 0.50:
            signal = 'DIVERGE_SHORT'
        else:
            signal = 'NEUTRAL'

        result = {
            'big_acct_long':    round(ba, 4),
            'big_pos_long':     round(bp, 4),
            'retail_long':      round(rr, 4),
            'whale_retail_gap': round(whale_gap, 4),
            'pos_trend_5h':     round(pos_trend, 4),
            'signal':           signal,
            'score_adj':        min(max(score_adj, -5), 8),
            'note':             ' | '.join(notes) if notes else f'大户持仓={bp:.2%} 散户={rr:.2%}',
            'confidence':       0.75 if (acc_data and pos_data and ret_data) else 0.3,
        }

        _CACHE[cache_key] = {'data': result, 'ts': now}
        return result

    except Exception as e:
        default['note'] = f'error: {str(e)[:40]}'
        return default


if __name__ == '__main__':
    import json
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = get_smart_money_signal(sym)
        print(f'\n=== {sym} 聪明钱信号 ===')
        print(f'  大户账户多={r["big_acct_long"]:.2%} 持仓多={r["big_pos_long"]:.2%} 散户多={r["retail_long"]:.2%}')
        print(f'  大户-散户背离={r["whale_retail_gap"]:+.4f}  持仓趋势5H={r["pos_trend_5h"]:+.4f}')
        print(f'  信号={r["signal"]}  score_adj={r["score_adj"]:+d}')
        print(f'  说明={r["note"]}')
