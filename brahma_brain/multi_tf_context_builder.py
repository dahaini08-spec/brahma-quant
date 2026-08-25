#!/usr/bin/env python3
"""
multi_tf_context_builder.py — 梵天多周期快照构建器
══════════════════════════════════════════════════
设计院 2026-08-25 苏摩111 Step1封印

使命：
  给AI议会注入完整的5个周期快照
  让AI议会看到和人类交易员一样的全周期视角

输出格式（注入brahma_context_injector第4层）：
  【多周期快照】
    15m: 结构=UPTREND FVG=$78,922↑ OB=$78,619↑ EMA9>21✅ RSI=47
    1H:  结构=UPTREND FVG=$78,971↑ OB=$78,619↑ EMA20>50✅ RSI=48
    4H:  结构=BULL    FVG=$77,564↑ OB=$77,000↑ EMA20>200✅ RSI=61
    1D:  结构=BULL    FVG=$72,000↑ EMA50>200✅ RSI=81⚠️超买
    1W:  结构=BULL                  EMA20>50✅  RSI=75
  【共振评分】
    OB共振 ✅+8 / FVG共振 ✅+5 / EMA共振 ✅+10 / RSI异常⚠️-5
"""
import sys
from pathlib import Path

_BASE = Path(__file__).parent
sys.path.insert(0, str(_BASE))

import json
import time
import requests
from functools import lru_cache


# ── 工具：拉取K线 ──────────────────────────────────────────────
def _fetch_klines(symbol: str, interval: str, limit: int = 200) -> list:
    try:
        url = 'https://fapi.binance.com/fapi/v1/klines'
        r = requests.get(url, params={'symbol': symbol, 'interval': interval,
                                       'limit': limit}, timeout=8)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _parse_ohlcv(raw: list) -> dict:
    if not raw:
        return {'o': [], 'h': [], 'l': [], 'c': [], 'v': []}
    return {
        'o': [float(k[1]) for k in raw],
        'h': [float(k[2]) for k in raw],
        'l': [float(k[3]) for k in raw],
        'c': [float(k[4]) for k in raw],
        'v': [float(k[5]) for k in raw],
    }


# ── 工具：EMA计算 ─────────────────────────────────────────────
def _ema(closes: list, period: int) -> float:
    """返回最新一根EMA值"""
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return round(ema, 4)


# ── 工具：RSI计算 ─────────────────────────────────────────────
def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


# ── 单周期快照 ────────────────────────────────────────────────
def _snapshot_one_tf(symbol: str, interval: str, signal_dir: str) -> dict:
    """
    返回单周期的核心指标快照
    """
    limit_map = {'15m': 500, '1h': 300, '4h': 200, '1d': 100, '1w': 60}
    limit = limit_map.get(interval, 200)
    raw = _fetch_klines(symbol, interval, limit)
    ohlc = _parse_ohlcv(raw)
    if not ohlc['c']:
        return {'tf': interval, 'error': '无数据'}

    c = ohlc['c']
    h = ohlc['h']
    l = ohlc['l']
    price = c[-1]

    # RSI
    rsi = _rsi(c, 14)
    rsi_status = '超买⚠️' if rsi > 70 else ('超卖⚠️' if rsi < 30 else '中性')

    # EMA多组
    ema_configs = {
        '15m': [(9, 21)],
        '1h':  [(20, 50)],
        '4h':  [(20, 200)],
        '1d':  [(50, 200)],
        '1w':  [(20, 50)],
    }
    ema_pair = ema_configs.get(interval, [(20, 50)])
    p1, p2 = ema_pair[0]
    ema1 = _ema(c, p1)
    ema2 = _ema(c, p2)
    ema_bull = ema1 > ema2
    ema_str = f'EMA{p1}{">" if ema_bull else "<"}EMA{p2} {"✅" if ema_bull else "❌"}'
    ema_label = f'EMA{p1}={ema1:,.0f}'

    # 价格vs EMA位置
    price_vs_ema1 = 'above' if price > ema1 else 'below'
    price_vs_ema2 = 'above' if price > ema2 else 'below'

    # 简单结构判断（基于EMA+RSI）
    if ema_bull and rsi > 50:
        struct = 'UPTREND'
    elif not ema_bull and rsi < 50:
        struct = 'DOWNTREND'
    else:
        struct = 'RANGING'

    # FVG（最近一个有效FVG）
    fvg_bull, fvg_bear = None, None
    n = len(c)
    for i in range(n - 2, max(n - 50, 2), -1):
        if l[i] > h[i-2]:  # Bull FVG
            fvg_bull = {'bottom': h[i-2], 'top': l[i], 'mid': (h[i-2]+l[i])/2}
            break
    for i in range(n - 2, max(n - 50, 2), -1):
        if h[i] < l[i-2]:  # Bear FVG
            fvg_bear = {'bottom': h[i], 'top': l[i-2], 'mid': (h[i]+l[i-2])/2}
            break

    # OB（最近一个有效OB）
    ob_bull, ob_bear = None, None
    for i in range(n - 3, max(n - 30, 3), -1):
        # Bull OB: 阴线后跟阳线突破
        if c[i] < ohlc['o'][i] and c[i+1] > h[i]:
            ob_bull = {'low': l[i], 'high': h[i], 'mid': (l[i]+h[i])/2}
            break
    for i in range(n - 3, max(n - 30, 3), -1):
        # Bear OB: 阳线后跟阴线突破
        if c[i] > ohlc['o'][i] and c[i+1] < l[i]:
            ob_bear = {'low': l[i], 'high': h[i], 'mid': (l[i]+h[i])/2}
            break

    # 清算/流动性位（等高/等低点）
    eqh, eql = None, None
    recent_highs = h[-20:]
    recent_lows  = l[-20:]
    max_h = max(recent_highs)
    min_l = min(recent_lows)
    # 多个高点接近最高点 → EQH
    near_top = [x for x in recent_highs if abs(x - max_h)/max_h < 0.003]
    near_bot = [x for x in recent_lows  if abs(x - min_l)/min_l < 0.003]
    if len(near_top) >= 2:
        eqh = max_h
    if len(near_bot) >= 2:
        eql = min_l

    return {
        'tf':          interval,
        'price':       price,
        'rsi':         rsi,
        'rsi_status':  rsi_status,
        'ema_str':     ema_str,
        'ema_label':   ema_label,
        'ema1':        ema1,
        'ema2':        ema2,
        'ema_bull':    ema_bull,
        'price_above_ema1': price > ema1,
        'price_above_ema2': price > ema2,
        'structure':   struct,
        'fvg_bull':    fvg_bull,
        'fvg_bear':    fvg_bear,
        'ob_bull':     ob_bull,
        'ob_bear':     ob_bear,
        'eqh':         eqh,
        'eql':         eql,
    }


# ── 共振评分 ──────────────────────────────────────────────────
def _calc_resonance(snapshots: dict, signal_dir: str) -> dict:
    """
    计算跨周期共振评分
    """
    score = 0
    details = []
    tfs = ['15m', '1h', '4h', '1d']
    is_long = signal_dir in ('LONG', 'UP', 'long')

    # EMA共振：多个TF同向
    ema_agree = sum(1 for tf in tfs if snapshots.get(tf, {}).get('ema_bull') == is_long)
    if ema_agree >= 4:
        score += 12
        details.append(f'EMA全周期共振✅ +12 ({ema_agree}/4 TF同向)')
    elif ema_agree >= 3:
        score += 7
        details.append(f'EMA大周期共振✅ +7 ({ema_agree}/4 TF同向)')
    elif ema_agree <= 1:
        score -= 8
        details.append(f'EMA周期分裂❌ -8 (仅{ema_agree}/4 TF同向)')

    # 结构共振
    struct_agree = sum(1 for tf in ['1h','4h','1d']
                       if ('UPTREND' in snapshots.get(tf,{}).get('structure','')) == is_long)
    if struct_agree >= 3:
        score += 8
        details.append(f'结构共振✅ +8 (1H/4H/1D全对齐)')
    elif struct_agree == 2:
        score += 4
        details.append(f'结构部分共振 +4 ({struct_agree}/3 TF对齐)')

    # FVG重叠：15m FVG在1H FVG范围内
    s15 = snapshots.get('15m', {})
    s1h = snapshots.get('1h', {})
    fvg_key = 'fvg_bull' if is_long else 'fvg_bear'
    fvg15 = s15.get(fvg_key)
    fvg1h = s1h.get(fvg_key)
    if fvg15 and fvg1h:
        # 检查15m FVG与1H FVG是否重叠
        overlap = (fvg15['bottom'] <= fvg1h['top'] and fvg15['top'] >= fvg1h['bottom'])
        if overlap:
            score += 6
            details.append(f'FVG跨周期重叠✅ +6 (15m∩1H FVG)')
        else:
            score += 2
            details.append(f'FVG存在但不重叠 +2')

    # OB共振：15m OB在1H OB区间内
    ob_key = 'ob_bull' if is_long else 'ob_bear'
    ob15 = s15.get(ob_key)
    ob1h = s1h.get(ob_key)
    if ob15 and ob1h:
        in_range = (ob15['low'] >= ob1h['low'] * 0.995 and
                    ob15['high'] <= ob1h['high'] * 1.005)
        if in_range:
            score += 8
            details.append(f'OB共振✅ +8 (15m OB踩在1H OB内)')
        else:
            score += 3
            details.append(f'OB存在但不共振 +3')

    # RSI异常惩罚
    for tf in ['1d', '1w']:
        s = snapshots.get(tf, {})
        rsi = s.get('rsi', 50)
        if rsi > 75 and is_long:
            score -= 6
            details.append(f'{tf} RSI={rsi}超买⚠️ -6')
        elif rsi < 30 and not is_long:
            score -= 6
            details.append(f'{tf} RSI={rsi}超卖⚠️ -6')

    return {'score': score, 'details': details, 'ema_agree': ema_agree}


# ── 主函数：构建全周期快照字符串 ─────────────────────────────────
def build_multi_tf_context(symbol: str, signal_dir: str,
                            max_chars: int = 800) -> str:
    """
    构建AI议会可读的全周期快照字符串
    直接注入 brahma_context_injector 第4层
    """
    TFS = ['15m', '1h', '4h', '1d', '1w']
    snapshots = {}

    for tf in TFS:
        try:
            snapshots[tf] = _snapshot_one_tf(symbol, tf, signal_dir)
        except Exception as e:
            snapshots[tf] = {'tf': tf, 'error': str(e)[:40]}

    price = snapshots.get('15m', {}).get('price', 0)
    is_long = signal_dir in ('LONG', 'UP', 'long')

    lines = ['【多周期快照】']

    tf_labels = {'15m': '15m', '1h': ' 1H', '4h': ' 4H', '1d': ' 1D', '1w': ' 1W'}
    for tf in TFS:
        s = snapshots.get(tf, {})
        if 'error' in s:
            lines.append(f'  {tf_labels[tf]}: 数据获取失败')
            continue

        rsi = s['rsi']
        rsi_str = f'RSI={rsi}{"⚠️" if rsi > 70 or rsi < 30 else ""}'
        struct = s['structure']
        struct_icon = '📈' if 'UP' in struct else ('📉' if 'DOWN' in struct else '↔️')
        ema_str = s['ema_str']

        # FVG
        fvg_key = 'fvg_bull' if is_long else 'fvg_bear'
        fvg = s.get(fvg_key)
        fvg_str = f'FVG=${fvg["mid"]:,.0f}{"↑" if is_long else "↓"}' if fvg else 'FVG=无'

        # OB
        ob_key = 'ob_bull' if is_long else 'ob_bear'
        ob = s.get(ob_key)
        ob_str = f'OB=${ob["mid"]:,.0f}' if ob else 'OB=无'

        # 清算位
        liq = s.get('eqh') if is_long else s.get('eql')
        liq_str = f'清算=${liq:,.0f}' if liq else ''

        line = f'  {tf_labels[tf]}: {struct_icon}{struct}  {fvg_str}  {ob_str}  {ema_str}  {rsi_str}'
        if liq_str:
            line += f'  {liq_str}'
        lines.append(line)

    # 共振评分
    resonance = _calc_resonance(snapshots, signal_dir)
    lines.append('')
    lines.append(f'【跨周期共振评分】{resonance["score"]:+d}分')
    for d in resonance['details']:
        lines.append(f'  {d}')

    # EMA全景
    lines.append('')
    lines.append('【EMA全景】')
    for tf in ['15m', '1h', '4h', '1d']:
        s = snapshots.get(tf, {})
        if 'ema_str' in s:
            price_pos = '价格>' if s.get('price_above_ema1') else '价格<'
            lines.append(f'  {tf_labels[tf]}: {s["ema_str"]}  {price_pos}EMA短')

    result = '\n'.join(lines)

    # 截断保护
    if len(result) > max_chars:
        result = result[:max_chars] + '\n  ...(截断)'

    return result


# ── CLI测试 ──────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'LONG'
    print(f'\n{sym} {dr} 多周期快照\n')
    ctx = build_multi_tf_context(sym, dr, max_chars=2000)
    print(ctx)
