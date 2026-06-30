#!/usr/bin/env python3
"""
sentiment_engine.py — P3 情绪第17维度（TradingAgents Grounded Sentiment启发）
从Binance Square热词 + 资金费率 + 多空比 + OI变化 → 情绪评分±分
注入brahma_brain第17维度

输出：
  score: -15 ~ +15（负=看空情绪，正=看多情绪）
  label: 'FEAR'/'GREED'/'NEUTRAL'
  signals: 各子信号列表
"""
import json, time
import urllib.request
from pathlib import Path

CACHE_FILE = Path(__file__).parent.parent / 'data' / 'sentiment_cache.json'
CACHE_TTL = 1800  # 30分钟缓存

def _fetch(url: str, timeout: int = 6) -> dict:
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return {}

def _load_cache(sym: str) -> dict:
    try:
        c = json.loads(CACHE_FILE.read_text())
        if c.get('sym') == sym and time.time() - c.get('ts', 0) < CACHE_TTL:
            return c
    except Exception:
        pass
    return {}

def _save_cache(sym: str, data: dict):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data['sym'] = sym
        data['ts'] = int(time.time())
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass

def get_sentiment(symbol: str, direction: str = 'SHORT') -> dict:
    """
    计算symbol的市场情绪评分
    direction: 信号方向，用于判断情绪是顺势还是逆势
    返回 score（+加分/-扣分）、label、signals列表
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    cache = _load_cache(sym)
    if cache:
        return cache

    B = 'https://fapi.binance.com/fapi/v1'
    score = 0
    signals = []

    # ── 1. 资金费率（最强情绪信号）────────────────────────────
    pr = _fetch(f'{B}/premiumIndex?symbol={sym}')
    fr = float(pr.get('lastFundingRate', 0)) * 100
    if fr < -0.01:
        score += 12
        signals.append(f'资金费{fr:.4f}% 极端恐慌做空→看多(+12)')
    elif fr < -0.005:
        score += 7
        signals.append(f'资金费{fr:.4f}% 空头拥挤→看多(+7)')
    elif fr < -0.001:
        score += 3
        signals.append(f'资金费{fr:.4f}% 轻微偏空(+3)')
    elif fr > 0.01:
        score -= 12
        signals.append(f'资金费{fr:.4f}% 极端贪婪做多→看空(-12)')
    elif fr > 0.005:
        score -= 7
        signals.append(f'资金费{fr:.4f}% 多头拥挤→看空(-7)')
    elif fr > 0.001:
        score -= 3
        signals.append(f'资金费{fr:.4f}% 轻微偏多(-3)')

    # ── 2. 多空比（Long/Short Ratio）─────────────────────────
    # [设计院修复 2026-06-20] 统一使用 globalLongShortAccountRatio（散户账户比）
    # 明确标注：此端点反映散户账户数方向，作为逆向指标使用
    # ⚠️ 禁止将此值与 topLongShortPositionRatio（大户仓位比）混用
    ls_data = _fetch(f'{B}/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=3')
    if isinstance(ls_data, list) and len(ls_data) >= 2:
        ls_now = float(ls_data[-1].get('longShortRatio', 1))   # 散户多空账户比
        ls_prev = float(ls_data[-2].get('longShortRatio', 1))
        ls_change = ls_now - ls_prev
        # 散户账户比作为逆向指标：散户多头拥挤(ratio>1.3)→看空；散户空头拥挤(ratio<0.8)→看多
        if ls_now > 1.3:
            score -= 5
            signals.append(f'散户账户多空比={ls_now:.2f} 散户多头拥挤[逆向](-5)')
        elif ls_now < 0.8:
            score += 5
            signals.append(f'散户账户多空比={ls_now:.2f} 散户空头拥挤[逆向逼空](+5)')
        if ls_change > 0.1:
            score -= 2
            signals.append(f'散户多空比上升{ls_change:+.2f} 散户追多[逆向](-2)')
        elif ls_change < -0.1:
            score += 2
            signals.append(f'散户多空比下降{ls_change:+.2f} 散户转空[逆向](+2)')

    # ── 3. OI变化（持仓变化=资金流向）───────────────────────
    oi_data = _fetch(f'{B}/openInterestHist?symbol={sym}&period=1h&limit=4')
    if isinstance(oi_data, list) and len(oi_data) >= 2:
        try:
            oi_now = float(oi_data[-1].get('sumOpenInterest', 0))
            oi_prev = float(oi_data[-3].get('sumOpenInterest', 0))
            oi_chg = (oi_now - oi_prev) / max(oi_prev, 1) * 100
            if oi_chg > 5:
                signals.append(f'OI+{oi_chg:.1f}% 新资金涌入(中性,需结合方向)')
            elif oi_chg < -5:
                score += 3
                signals.append(f'OI{oi_chg:.1f}% 去杠杆完成→反弹(+3)')
        except Exception:
            pass

    # ── 4. 波动率情绪（ATR相对值）────────────────────────────
    k1h = _fetch(f'{B}/klines?symbol={sym}&interval=1h&limit=24')
    if isinstance(k1h, list) and len(k1h) >= 14:
        h1 = [float(k[2]) for k in k1h]
        l1 = [float(k[3]) for k in k1h]
        c1 = [float(k[4]) for k in k1h]
        tr = [max(h1[i]-l1[i], abs(h1[i]-c1[i-1]), abs(l1[i]-c1[i-1])) for i in range(1, len(c1))]
        atr_14 = sum(tr[-14:])/14
        atr_3  = sum(tr[-3:])/3
        vol_ratio = atr_3 / atr_14 if atr_14 else 1
        if vol_ratio > 2.0:
            signals.append(f'波动率爆发{vol_ratio:.1f}x ATR(方向中性,需结合价格)')
        elif vol_ratio < 0.4:
            signals.append(f'波动率萎缩{vol_ratio:.1f}x ATR(蓄力中)')

    # ── 归一化 ────────────────────────────────────────────────
    score = max(min(score, 15), -15)

    if score >= 8:
        label = 'GREED_SHORT'  # 市场贪婪做多→信号偏空
    elif score >= 4:
        label = 'LEAN_GREED'
    elif score <= -8:
        label = 'FEAR_LONG'    # 市场恐慌做空→信号偏多
    elif score <= -4:
        label = 'LEAN_FEAR'
    else:
        label = 'NEUTRAL'

    result = {
        'symbol': sym,
        'score': score,
        'label': label,
        'fr': round(fr, 4),
        'signals': signals,
    }
    _save_cache(sym, result)
    return result


def inject_to_brahma(symbol: str, direction: str, base_score: float) -> tuple:
    """
    注入情绪分到梵天评分
    返回 (adjusted_score, sentiment_result)
    """
    sent = get_sentiment(symbol, direction)
    sent_score = sent.get('score', 0)

    # 方向调整：资金费情绪与信号方向的关系
    is_long = 'LONG' in direction.upper() or '多' in direction
    if is_long:
        # 做多时：负资金费（空头拥挤）是顺势，加分；正资金费（多头拥挤）是逆势，扣分
        adj = sent_score  # 正值=看多=加分，负值=看空=扣分
    else:
        # 做空时：正资金费（多头拥挤）是顺势，加分；负资金费（空头拥挤）是逆势，扣分
        adj = -sent_score

    new_score = round(base_score + adj, 1)
    return new_score, sent


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    result = get_sentiment(sym)
    print(f'情绪评分: {result["score"]:+d}  标签: {result["label"]}')
    for s in result['signals']:
        print(f'  · {s}')
