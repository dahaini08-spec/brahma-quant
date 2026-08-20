"""
brahma_multiframe.py — 全周期FVG/OB扫描引擎
设计院封印 2026-08-20 苏摩指令：15m/1H/4H/日线/周线全周期验证

核心原则：
  不能只看短期！FVG/OB必须跨周期共振才能入场
  短期看方向，中期看结构，长期看方向

输出：
  multiframe_context — 各周期FVG/OB位置摘要
  mtf_bias          — 综合偏向（BULL/BEAR/NEUTRAL）
  mtf_score_adj     — 分数调整（±5~15分）
  mtf_summary       — 人类可读摘要
"""

import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from brahma_bus import bus

TIMEFRAMES = [
    ('15m', 200, '15分钟'),
    ('1h',  500, '1小时'),
    ('4h',  300, '4小时'),
    ('1d',  200, '日线'),
    ('1w',  100, '周线'),
]

MIN_GAP_PCT = 0.25  # 最小FVG缺口过滤噪音
PRICE_RANGE = 0.20  # 扫描当前价±20%


def _scan_fvg(highs, lows, closes, times, price, tf):
    """扫描单周期FVG，返回分类列表"""
    lo, hi = price * (1 - PRICE_RANGE), price * (1 + PRICE_RANGE)
    above, below, cross = [], [], []

    for i in range(len(closes) - 2):
        k1h, k3l = highs[i], lows[i + 2]
        k1l, k3h = lows[i], highs[i + 2]

        # Bull FVG（向上跳空）
        if k1h < k3l:
            gap = (k3l - k1h) / k1h * 100
            if gap >= MIN_GAP_PCT:
                entry = {'type': 'Bull', 'bot': round(k1h, 1), 'top': round(k3l, 1),
                         'gap': round(gap, 2), 'tf': tf, 'ts': times[i]}
                if k3l > price and lo < k3l < hi:
                    above.append(entry)
                elif k1h < price <= k3l:
                    cross.append(entry)
                elif k3l < price and k1h > lo:
                    below.append(entry)

        # Bear FVG（向下跳空）
        if k1l > k3h:
            gap = (k1l - k3h) / k3h * 100
            if gap >= MIN_GAP_PCT:
                entry = {'type': 'Bear', 'bot': round(k3h, 1), 'top': round(k1l, 1),
                         'gap': round(gap, 2), 'tf': tf, 'ts': times[i]}
                if k3h > price and lo < k3h < hi:
                    above.append(entry)
                elif k3h <= price < k1l:
                    cross.append(entry)
                elif k1l < price and k1l > lo:
                    below.append(entry)

    return above, below, cross


def _scan_ob(highs, lows, closes, times, price, tf, lookback=30):
    """扫描Order Block（最近N根K线）"""
    obs_bull, obs_bear = [], []
    start = max(1, len(closes) - lookback)
    for i in range(start, len(closes) - 1):
        # Bull OB：阴线后价格向上突破该K线高点
        if closes[i] < opens_approx(closes, i) and i + 1 < len(closes):
            if closes[i + 1] > highs[i] and lows[i] > price * 0.80:
                obs_bull.append({
                    'bot': round(lows[i], 1), 'top': round(highs[i], 1),
                    'tf': tf, 'ts': times[i],
                    'dist_pct': round((price - highs[i]) / price * 100, 2),
                    'above': highs[i] > price
                })
        # Bear OB：阳线后价格向下跌破该K线低点
        if closes[i] > opens_approx(closes, i) and i + 1 < len(closes):
            if closes[i + 1] < lows[i] and highs[i] < price * 1.20:
                obs_bear.append({
                    'bot': round(lows[i], 1), 'top': round(highs[i], 1),
                    'tf': tf, 'ts': times[i],
                    'dist_pct': round((highs[i] - price) / price * 100, 2),
                    'above': lows[i] > price
                })
    return obs_bull[-3:], obs_bear[-3:]


def opens_approx(closes, i):
    """用上根收盘近似开盘（Binance K线无独立开盘价字段）"""
    return closes[i - 1] if i > 0 else closes[i]


def scan(symbol: str, direction: str = 'LONG') -> dict:
    """
    全周期FVG/OB扫描主函数
    返回：multiframe_context, mtf_bias, mtf_score_adj, mtf_summary
    """
    try:
        price = float(bus.price(symbol))
    except Exception:
        return _empty()

    tf_results = {}
    for tf, limit, label in TIMEFRAMES:
        try:
            klines = bus.klines(symbol, tf, limit)
            if not klines or len(klines) < 10:
                continue
            highs  = [float(k[2]) for k in klines]
            lows   = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            times  = [k[0] for k in klines]

            fvg_above, fvg_below, fvg_cross = _scan_fvg(
                highs, lows, closes, times, price, tf)
            ob_bull, ob_bear = _scan_ob(
                highs, lows, closes, times, price, tf)

            # 最近3个上方/下方，按距离排序
            fvg_above_sorted = sorted(fvg_above, key=lambda x: x['bot'])[:3]
            fvg_below_sorted = sorted(fvg_below, key=lambda x: -x['top'])[:3]

            tf_results[tf] = {
                'label':      label,
                'fvg_above':  fvg_above_sorted,
                'fvg_below':  fvg_below_sorted,
                'fvg_cross':  fvg_cross[-3:],
                'ob_bull':    ob_bull,
                'ob_bear':    ob_bear,
            }
        except Exception as e:
            tf_results[tf] = {'label': label, 'error': str(e)[:60]}

    # ── 综合偏向判断 ──────────────────────────────────────────────────────────
    bias_score = 0  # 正=多头偏向，负=空头偏向

    # 规则1：日线/周线上方有Bull FVG磁铁 → 多头拉力
    for tf in ['1d', '1w']:
        d = tf_results.get(tf, {})
        for f in d.get('fvg_above', []):
            if f['type'] == 'Bull' and f['bot'] < price * 1.05:
                bias_score += 2  # 近距离Bull FVG磁铁
            elif f['type'] == 'Bear':
                bias_score -= 1  # Bear FVG天花板

    # 规则2：日线/周线穿越中的FVG类型
    for tf in ['1d', '1w']:
        d = tf_results.get(tf, {})
        for f in d.get('fvg_cross', []):
            if f['type'] == 'Bull':
                bias_score += 1  # 价格在多头FVG内=支撑
            else:
                bias_score -= 1  # 价格在空头FVG内=压制

    # 规则3：4H上方无FVG = 中期无结构支撑
    d4h = tf_results.get('4h', {})
    if not d4h.get('fvg_above'):
        bias_score -= 2

    # 规则4：短期（1H/15m）Bull OB在价格下方=支撑
    for tf in ['1h', '15m']:
        d = tf_results.get(tf, {})
        for ob in d.get('ob_bull', []):
            if not ob.get('above') and ob['dist_pct'] < 3.0:
                bias_score += 1

    # 规则5：日线下方有大缺口Bear FVG = 下行风险
    for f in tf_results.get('1d', {}).get('fvg_below', []):
        if f['type'] == 'Bear' and f['gap'] > 2.0:
            bias_score -= 2

    if bias_score >= 3:
        mtf_bias = 'BULL'
        score_adj = min(bias_score, 8)
    elif bias_score <= -3:
        mtf_bias = 'BEAR'
        score_adj = max(bias_score, -8)
    else:
        mtf_bias = 'NEUTRAL'
        score_adj = bias_score

    # 方向过滤：做多时空头偏向减分，做空时多头偏向减分
    if direction == 'LONG' and mtf_bias == 'BEAR':
        score_adj = min(score_adj, -3)
    elif direction == 'SHORT' and mtf_bias == 'BULL':
        score_adj = max(score_adj, 3)

    # ── 生成摘要文本 ──────────────────────────────────────────────────────────
    lines = [f'\n╬══ 🌐 全周期FVG/OB地图 (MTF Confluence) ══╬']
    lines.append(f'  当前价: ${price:,.0f}  综合偏向: {mtf_bias}  MTF调分: {score_adj:+d}')

    for tf, limit, label in TIMEFRAMES:
        d = tf_results.get(tf, {})
        if 'error' in d:
            continue
        has_data = any([d.get('fvg_above'), d.get('fvg_below'),
                        d.get('fvg_cross'), d.get('ob_bull'), d.get('ob_bear')])
        if not has_data:
            continue

        lines.append(f'\n  [{label} {tf.upper()}]')

        for f in d.get('fvg_above', []):
            dist = (f['bot'] - price) / price * 100
            lines.append(f'    ↑ {f["type"]}FVG ${f["bot"]:,.0f}~${f["top"]:,.0f} '
                         f'gap={f["gap"]}% 距+{dist:.1f}% 🧲上方磁铁')

        for f in d.get('fvg_cross', []):
            lines.append(f'    ↔ {f["type"]}FVG ${f["bot"]:,.0f}~${f["top"]:,.0f} '
                         f'gap={f["gap"]}% {"⚠️空头压制" if f["type"]=="Bear" else "✅多头支撑"}')

        for f in d.get('fvg_below', [])[:2]:
            dist = (price - f['top']) / price * 100
            lines.append(f'    ↓ {f["type"]}FVG ${f["bot"]:,.0f}~${f["top"]:,.0f} '
                         f'gap={f["gap"]}% 距-{dist:.1f}% 下方磁铁')

        for ob in d.get('ob_bull', []):
            tag = '上方' if ob.get('above') else f'下方{ob["dist_pct"]:.1f}%'
            lines.append(f'    🟢 Bull OB ${ob["bot"]:,.0f}~${ob["top"]:,.0f} [{tag}]')

        for ob in d.get('ob_bear', []):
            tag = '上方' if ob.get('above') else f'下方{ob["dist_pct"]:.1f}%'
            lines.append(f'    🔴 Bear OB ${ob["bot"]:,.0f}~${ob["top"]:,.0f} [{tag}]')

    lines.append(f'\n  MTF综合: bias={mtf_bias} adj={score_adj:+d}')
    lines.append('╬══════════════════════════════════════════╬')

    return {
        'price':              price,
        'mtf_bias':           mtf_bias,
        'mtf_score_adj':      score_adj,
        'mtf_summary':        '\n'.join(lines),
        'multiframe_context': tf_results,
    }


def _empty():
    return {
        'price': 0, 'mtf_bias': 'NEUTRAL',
        'mtf_score_adj': 0, 'mtf_summary': '',
        'multiframe_context': {}
    }


# ── 快速测试 ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    r = scan(sym)
    print(r['mtf_summary'])
    print(f'\nbias={r["mtf_bias"]} adj={r["mtf_score_adj"]:+d}')
