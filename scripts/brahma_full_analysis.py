#!/usr/bin/env python3
"""
梵天全系统35维深度分析 — 固化标准模板
设计院 · 顶级量化 × 达摩院
每次分析必须调用此脚本，不得使用浅层替代

用法：
  python3 scripts/brahma_full_analysis.py BTCUSDT ETHUSDT
  python3 scripts/brahma_full_analysis.py SOLUSDT
"""
import sys, os, time, json, math
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
import numpy as np
from brahma_brain import brahma_core as bc

# ══════════════════════════════════════════════════════════
# 辅助工具
# ══════════════════════════════════════════════════════════
def _req(url, params=None, timeout=8):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json()
    except Exception:
        return {}

def _klines(sym, interval, limit):
    d = _req('https://fapi.binance.com/fapi/v1/klines',
             {'symbol': sym, 'interval': interval, 'limit': limit})
    return d if isinstance(d, list) else []

def _ema(arr, n):
    k = 2 / (n + 1); e = [arr[0]]
    for x in arr[1:]: e.append(x * k + e[-1] * (1 - k))
    return np.array(e)

def _rsi(closes, n=14):
    d = np.diff(np.array(closes[-(n+2):], dtype=float))
    g = np.where(d > 0, d, 0); lo = np.where(d < 0, -d, 0)
    ag = g.mean(); al = lo.mean()
    return round(100 - 100 / (1 + ag / al), 1) if al > 0 else 99.0

def _macd_hist(closes):
    c = np.array(closes, dtype=float)
    m12 = _ema(c, 12); m26 = _ema(c, 26)
    ml = m12 - m26; sig = _ema(ml, 9)
    return round(float(ml[-1] - sig[-1]), 4)

def _swing_points(h, l, n=3):
    sh, sl = [], []
    for i in range(n, len(h) - n):
        if all(h[i] >= h[i-j] for j in range(1,n+1)) and all(h[i] >= h[i+j] for j in range(1,n+1)):
            sh.append(round(float(h[i]), 4))
        if all(l[i] <= l[i-j] for j in range(1,n+1)) and all(l[i] <= l[i+j] for j in range(1,n+1)):
            sl.append(round(float(l[i]), 4))
    return sh[-5:], sl[-5:]

def _fvg(klines):
    bull, bear = [], []
    for i in range(1, len(klines) - 1):
        l_prev = float(klines[i-1][3]); h_next = float(klines[i+1][2])
        h_prev = float(klines[i-1][2]); l_next = float(klines[i+1][3])
        if l_prev > h_next:
            bull.append({'lo': round(h_next,4), 'hi': round(l_prev,4)})
        if h_prev < l_next:
            bear.append({'lo': round(h_prev,4), 'hi': round(l_next,4)})
    px = float(klines[-1][4])
    bull_valid = [f for f in bull if f['hi'] < px][-3:]
    bear_valid = [f for f in bear if f['lo'] > px][-3:]
    return bull_valid, bear_valid

# ══════════════════════════════════════════════════════════
# 梵天核心 + 补充数据拉取
# ══════════════════════════════════════════════════════════
def full_analysis(sym: str) -> dict:
    sym = sym.upper().strip()
    if not sym.endswith('USDT'): sym += 'USDT'

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # ── 1. 梵天核心35维 ────────────────────────────────────
    raw = bc.analyze(sym, signal_dir=None, deep=True)
    price = float(raw.get('price', 0))
    regime = raw.get('regime', '?')
    grade  = raw.get('grade', 0)
    score_final = raw.get('score_final', raw.get('score', 0))
    valid  = raw.get('valid_signal', False)
    action = raw.get('action', '?')
    params = raw.get('params', {})
    momentum = raw.get('momentum', {})
    sentiment = raw.get('sentiment', {})
    smc_raw = raw.get('smc', {})
    swing_4h = raw.get('swing_4h', {})
    key_levels = raw.get('key_levels', {})
    wave = raw.get('wave', {})
    confluence = raw.get('confluence', {})

    # ── 2. K线补充 ─────────────────────────────────────────
    k1h  = _klines(sym, '1h',  200)
    k4h  = _klines(sym, '4h',  100)
    k15m = _klines(sym, '15m', 100)
    k1d  = _klines(sym, '1d',  30)

    c1 = [float(k[4]) for k in k1h]
    h1 = [float(k[2]) for k in k1h]
    l1 = [float(k[3]) for k in k1h]
    v1 = [float(k[5]) for k in k1h]

    c4 = [float(k[4]) for k in k4h]
    h4 = [float(k[2]) for k in k4h]
    l4 = [float(k[3]) for k in k4h]

    c1d = [float(k[4]) for k in k1d]
    v1d = [float(k[7]) for k in k1d]

    # EMA
    ema20_1h  = round(float(_ema(c1, 20)[-1]),  2)
    ema50_1h  = round(float(_ema(c1, 50)[-1]),  2)
    ema200_1h = round(float(_ema(c1, 200)[-1]), 2) if len(c1) >= 200 else None
    ema20_4h  = round(float(_ema(c4, 20)[-1]),  2)
    ema50_4h  = round(float(_ema(c4, 50)[-1]),  2)

    # RSI
    rsi_15m = _rsi([float(k[4]) for k in k15m])
    rsi_1h  = _rsi(c1)
    rsi_4h  = _rsi(c4)
    rsi_1d  = _rsi(c1d)

    # MACD hist
    macd_1h = _macd_hist(c1)
    macd_4h = _macd_hist(c4)

    # BB 1H
    sma20 = np.mean(c1[-20:]); std20 = np.std(c1[-20:])
    bb_up = round(float(sma20 + 2*std20), 2)
    bb_lo = round(float(sma20 - 2*std20), 2)
    bb_pct = round((price - float(sma20 - 2*std20)) / max(float(4*std20), 0.0001) * 100, 1)

    # ATR
    def atr(h, l, c, n=14):
        tr = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, n+2)]
        return round(float(np.mean(tr)), 4)
    atr_1h = atr(h1, l1, c1)
    atr_4h = atr(h4, l4, c4)

    # 量价
    vol_avg5d = float(np.mean(v1d[-6:-1])) if len(v1d) >= 6 else 1
    vol_today = float(v1d[-1]) if v1d else 0
    vol_ratio = round(vol_today / vol_avg5d, 2) if vol_avg5d > 0 else 0

    # 摆动点 + FVG
    sh4, sl4 = _swing_points(np.array(h4), np.array(l4))
    sh1, sl1 = _swing_points(np.array(h1), np.array(l1), n=2)
    fvg_bull_4h, fvg_bear_4h = _fvg(k4h[-40:])
    fvg_bull_1h, fvg_bear_1h = _fvg(k1h[-48:])

    # ── 3. 链上 / 衍生品数据 ──────────────────────────────
    t24 = _req('https://fapi.binance.com/fapi/v1/ticker/24hr', {'symbol': sym})
    oi_now = _req('https://fapi.binance.com/fapi/v1/openInterest', {'symbol': sym})
    fr_now = _req('https://fapi.binance.com/fapi/v1/premiumIndex', {'symbol': sym})
    ls_now = _req('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                  {'symbol': sym, 'period': '1h', 'limit': 3})

    # OI历史（判断OI趋势）
    oi_hist = _req('https://fapi.binance.com/futures/data/openInterestHist',
                   {'symbol': sym, 'period': '4h', 'limit': 12})
    oi_vals = [float(r['sumOpenInterestValue']) for r in oi_hist] if isinstance(oi_hist, list) else []
    oi_trend = 'UP' if len(oi_vals) >= 3 and oi_vals[-1] > oi_vals[-3] else \
               ('DOWN' if len(oi_vals) >= 3 and oi_vals[-1] < oi_vals[-3] else 'FLAT')

    # 多空比历史
    ls_list = ls_now if isinstance(ls_now, list) else []
    ls_latest = ls_list[-1] if ls_list else {}
    ls_ratio  = round(float(ls_latest.get('longShortRatio', 0)), 3)
    ls_long   = round(float(ls_latest.get('longAccount', 0)) * 100, 1)
    ls_short  = round(float(ls_latest.get('shortAccount', 0)) * 100, 1)

    fr_val = round(float(fr_now.get('lastFundingRate', 0)) * 100, 4) if fr_now else 0
    oi_val = float(oi_now.get('openInterest', 0)) if oi_now else 0
    oi_val_b = round(float(t24.get('quoteVolume', 0)) / 1e9, 2) if t24 else 0
    chg24h   = float(t24.get('priceChangePercent', 0)) if t24 else 0
    high24h  = float(t24.get('highPrice', 0)) if t24 else 0
    low24h   = float(t24.get('lowPrice', 0)) if t24 else 0
    vol24h_m = round(float(t24.get('quoteVolume', 0)) / 1e6, 0) if t24 else 0

    # ── 4. 组装最终结果 ────────────────────────────────────
    result = {
        # 基础
        'symbol': sym, 'time': now, 'price': price,
        'chg24h': chg24h, 'high24h': high24h, 'low24h': low24h,
        'vol24h_m': vol24h_m, 'vol_ratio_vs_5d': vol_ratio,
        # 梵天核心
        'regime': regime, 'grade': grade,
        'score_final': score_final, 'valid': valid, 'action': action,
        'wave': wave.get('wave','?'), 'wave_note': wave.get('note',''),
        'consensus': raw.get('consensus','?'),
        'summary': raw.get('summary',''),
        # 入场参数
        'entry_lo': params.get('entry_lo'), 'entry_hi': params.get('entry_hi'),
        'sl': params.get('stop_loss'), 'tp1': params.get('tp1'), 'tp2': params.get('tp2'),
        'rr1': params.get('rr1'), 'sl_pct': params.get('sl_pct'),
        'size_pct': raw.get('pos_pct_sizer'), 'pos_level': raw.get('pos_level_sizer'),
        # 多框架技术
        'rsi_15m': rsi_15m, 'rsi_1h': rsi_1h, 'rsi_4h': rsi_4h, 'rsi_1d': rsi_1d,
        'macd_hist_1h': macd_1h, 'macd_hist_4h': macd_4h,
        'ema20_1h': ema20_1h, 'ema50_1h': ema50_1h, 'ema200_1h': ema200_1h,
        'ema20_4h': ema20_4h, 'ema50_4h': ema50_4h,
        'bb_upper': bb_up, 'bb_lower': bb_lo, 'bb_pct': bb_pct,
        'atr_1h': atr_1h, 'atr_4h': atr_4h,
        # OB / FVG / 结构
        'ob_4h_lo': params.get('entry_lo'), 'ob_4h_hi': params.get('entry_hi'),
        'ob_grade': raw.get('smc', {}).get('ob_grade', grade),
        'fvg_bull_4h': fvg_bull_4h, 'fvg_bear_4h': fvg_bear_4h,
        'fvg_bull_1h': fvg_bull_1h, 'fvg_bear_1h': fvg_bear_1h,
        'swing_highs_4h': sh4, 'swing_lows_4h': sl4,
        'swing_highs_1h': sh1, 'swing_lows_1h': sl1,
        'fib_levels': key_levels.get('fib', {}),
        'choch': smc_raw.get('structure', {}).get('choch', []),
        'bos': smc_raw.get('structure', {}).get('bos', []),
        # 清算 / 持仓
        'oi': oi_val, 'oi_usd_b': oi_val_b, 'oi_trend': oi_trend,
        'fr': fr_val,
        'ls_ratio': ls_ratio, 'ls_long': ls_long, 'ls_short': ls_short,
        # 外部维度 (从raw提取)
        'gex': raw.get('s22_gex'), 'gex_strength': raw.get('s22_gex_strength'),
        'gex_zero_flip': raw.get('s22_zero_flip'),
        'magnet_strike': raw.get('s22_magnet_strike'),
        'kronos_p_up': raw.get('s23_p_up'),
        'vol_exh': raw.get('vol_exh_type', raw.get('vol_exh')),
        'vol_exh_score': raw.get('vol_exh_score'),
        'multi_tf_div': raw.get('multi_tf_div'),
        'ob_heatmap_score': raw.get('ob_heatmap_score'),
        'causal_verdict': raw.get('causal_verdict'),
        'causal_conf': raw.get('causal_conf'),
        # 宏观拦截
        'ema200_daily': raw.get('ema200_bear'),
        'n21_adj': raw.get('fib_macro_adj'),
        'p0b_blocked': raw.get('p0b_blocked', True),
        'switch_noise': raw.get('switch_noise'),
        'timing_badge': raw.get('timing_badge'),
        # 全量raw备用
        '_raw': raw,
    }
    return result


# ══════════════════════════════════════════════════════════
# 固化报告格式 — 每次输出必须包含这些模块
# ══════════════════════════════════════════════════════════
def print_report(r: dict):
    sym = r['symbol']
    sep = '=' * 70

    def fmt(v, dec=2):
        if v is None: return '—'
        if isinstance(v, float): return f'{v:.{dec}f}'
        return str(v)

    def flag(cond): return '✅' if cond else '❌'
    def trend_icon(v, ref): return '📈' if v > ref else ('📉' if v < ref else '➡️')

    print(sep)
    print(f'🏛️ 梵天设计院 · {sym}  {r["time"]}')
    print(f'   顶级量化 × 达摩院 · 35维完整深度分析')
    print(sep)

    # ── A. 宏观战场定性 ──────────────────────────────────
    print('\n【A · 宏观战场 & 体制定性】')
    print(f'  体制    : {r["regime"]}  grade={r["grade"]}')
    print(f'  波浪    : {r["wave"]}  → {r["wave_note"]}')
    print(f'  共识    : {r["consensus"]}')
    print(f'  EMA200日: ${fmt(r["ema200_daily"],0)}  '
          f'{"🔴 熊市反弹(BEAR_BOUNCE)" if r.get("p0b_blocked") else "✅ 宏观多头"}')
    print(f'  宏观N21 : {fmt(r["n21_adj"],0)}分惩罚 '
          f'（价格距EMA200日 = {round((r["price"]/r["ema200_daily"]-1)*100,1) if r["ema200_daily"] else "?"}%）')
    print(f'  体制摘要: {r["summary"]}')

    # ── B. 当前价格快照 ──────────────────────────────────
    print('\n【B · 价格快照 & 量价结构】')
    print(f'  现价    : ${fmt(r["price"],2)}   24H: {r["chg24h"]:+.2f}%')
    print(f'  区间    : ${fmt(r["low24h"],2)} ~ ${fmt(r["high24h"],2)}')
    print(f'  成交额  : ${r["vol24h_m"]:.0f}M   vs 5日均量比: {r["vol_ratio_vs_5d"]}x '
          f'{"⚠️ 缩量" if r["vol_ratio_vs_5d"] < 0.5 else "📊 正常"}')

    # ── C. 多时间框架技术面 ───────────────────────────────
    print('\n【C · 多时间框架技术面（MTF）】')
    print("  {:<10} {:>8} {:>8} {:>8} {:>8}".format("维度","15M","1H","4H","1D"))
    print("  " + "-"*46)
    print(f'  {"RSI":<10} {fmt(r["rsi_15m"],1):>8} {fmt(r["rsi_1h"],1):>8} '
          f'{fmt(r["rsi_4h"],1):>8} {fmt(r["rsi_1d"],1):>8}')
    print(f'  {"MACD Hist":<10} {"—":>8} {fmt(r["macd_hist_1h"],1):>8} '
          f'{fmt(r["macd_hist_4h"],1):>8} {"—":>8}')
    print(f'  {"BB%":<10} {"—":>8} {fmt(r["bb_pct"],1)+"%" :>8} {"—":>8} {"—":>8}')
    print()
    print(f'  EMA结构(1H): 价格{trend_icon(r["price"],r["ema20_1h"])}EMA20({fmt(r["ema20_1h"],0)})  '
          f'{trend_icon(r["price"],r["ema50_1h"])}EMA50({fmt(r["ema50_1h"],0)})  '
          f'{trend_icon(r["price"],r["ema200_1h"]) if r["ema200_1h"] else "—"}EMA200({fmt(r["ema200_1h"],0)})')
    print(f'  EMA结构(4H): 价格{trend_icon(r["price"],r["ema20_4h"])}EMA20({fmt(r["ema20_4h"],0)})  '
          f'{trend_icon(r["price"],r["ema50_4h"])}EMA50({fmt(r["ema50_4h"],0)})')

    # RSI状态标注
    rsi_note = ''
    if r['rsi_1h'] < 25: rsi_note = '🔥 深度超卖 — 弹弓蓄力'
    elif r['rsi_1h'] < 35: rsi_note = '⚡ 超卖 — 历史高胜率区'
    elif r['rsi_1h'] < 45: rsi_note = '🟡 偏低 — 回调区'
    elif r['rsi_1h'] > 75: rsi_note = '⚠️ 超买 — 注意顶背离'
    elif r['rsi_1h'] > 65: rsi_note = '📈 强势区'
    if rsi_note: print(f'  RSI_1H 信号: {rsi_note}')

    # ── D. SMC结构（OB / FVG / CHoCH / BOS）────────────
    print('\n【D · SMC结构 — OB / FVG / CHoCH / BOS】')
    print(f'  4H OB入场区 : ${fmt(r["ob_4h_lo"],2)} ~ ${fmt(r["ob_4h_hi"],2)}  grade={r["ob_grade"]}')
    print(f'  价格在OB区  : {flag(r["ob_4h_lo"] and r["ob_4h_lo"] <= r["price"] <= r["ob_4h_hi"])}')
    print()
    print(f'  4H看涨FVG(下方支撑) : {r["fvg_bull_4h"] if r["fvg_bull_4h"] else "无"}')
    print(f'  4H看跌FVG(上方阻力) : {r["fvg_bear_4h"] if r["fvg_bear_4h"] else "无"}')
    print(f'  1H看涨FVG           : {r["fvg_bull_1h"][-2:] if r["fvg_bull_1h"] else "无"}')
    print(f'  1H看跌FVG           : {r["fvg_bear_1h"][-2:] if r["fvg_bear_1h"] else "无"}')
    print()
    print(f'  4H摆动高点(阻力) : {r["swing_highs_4h"]}')
    print(f'  4H摆动低点(支撑) : {r["swing_lows_4h"]}')
    choch_list = r.get('choch', [])
    bos_list   = r.get('bos', [])
    print(f'  CHoCH信号 : {choch_list[-2:] if choch_list else "未见结构转变"}')
    print(f'  BOS信号   : {bos_list[-2:] if bos_list else "无"}')

    # 斐波那契
    fib = r.get('fib_levels', {})
    if fib:
        print()
        print(f'  关键斐波那契位:')
        for lvl in ['0.236','0.382','0.500','0.618','0.786']:
            v = fib.get(lvl)
            if v:
                dist = round((float(v) - r['price']) / r['price'] * 100, 2)
                print(f'    Fib {lvl} = ${fmt(float(v),2)}  ({dist:+.1f}%)')

    # ── E. 清算层分析 ────────────────────────────────────
    print('\n【E · 清算层 & 持仓结构】')
    print(f'  多空比(散户) : 多{r["ls_long"]}% / 空{r["ls_short"]}%  L/S={r["ls_ratio"]}')
    crowd = '⚠️ 多头过度拥挤，空头猎杀风险' if r['ls_long'] > 60 else \
            ('⚠️ 空头过度拥挤，逼空风险' if r['ls_short'] > 60 else '✅ 中性')
    print(f'  偏向判断     : {crowd}')
    print(f'  OI          : {fmt(r["oi"],0)}  OI趋势={r["oi_trend"]}')
    print(f'  资金费率     : {fmt(r["fr"],4)}%  '
          f'{"多头付费偏高→空头有利" if r["fr"] > 0.03 else ("空头付费→多头有利" if r["fr"] < -0.01 else "中性")}')
    print(f'  清算磁铁     : 上方空头堆积={r["swing_highs_4h"][-1] if r["swing_highs_4h"] else "?"}'
          f'  下方多头防线={r["swing_lows_4h"][-1] if r["swing_lows_4h"] else "?"}')

    # ── F. 外部信号层 ─────────────────────────────────────
    print('\n【F · 外部信号 — GEX / Kronos / 跨所 / 宏观】')
    print(f'  GEX         : {fmt(r["gex"],"")  if r["gex"] else "—"}  '
          f'强度={r["gex_strength"] or "—"}  '
          f'ZeroFlip={r["gex_zero_flip"] or "—"}')
    print(f'  磁铁Strike  : ${fmt(r["magnet_strike"],0) if r["magnet_strike"] else "—"}')
    print(f'  Kronos p_up : {fmt(r["kronos_p_up"],2) if r["kronos_p_up"] else "—"}  '
          f'{"🔴 低置信(短期偏空)" if r["kronos_p_up"] and r["kronos_p_up"] < 0.35 else ("✅ 中性偏多" if r["kronos_p_up"] and r["kronos_p_up"] > 0.55 else "🟡 中性")}')
    print(f'  量能耗尽    : {r["vol_exh"] or "—"}  分={r["vol_exh_score"] or 0}')
    print(f'  多框架背离  : {r["multi_tf_div"] or "—"}')
    print(f'  OB热力图    : {r["ob_heatmap_score"] or "—"}')
    print(f'  因果验证    : {r["causal_verdict"] or "—"}  conf={fmt(r["causal_conf"],2)}')
    print(f'  体制噪声    : SwitchNoise={r["switch_noise"] or "—"}')
    print(f'  时机徽章    : {r["timing_badge"] or "—"}')

    # ── G. 梵天评分汇总 ───────────────────────────────────
    print('\n【G · 梵天35维评分汇总】')
    print(f'  最终得分  : {fmt(r["score_final"],1)} / 200')
    _gap = round(155 - r['score_final'], 1)
    _ok  = '✅ 达标' if r['score_final'] >= 155 else ('❌ 差%s分' % _gap)
    print('  有效门槛  : 155分（当前%s）' % _ok)
    print(f'  信号有效  : {flag(r["valid"])}  行动建议: {r["action"] or "待触发"}')
    print(f'  仓位建议  : {r["size_pct"]}% NAV  级别={r["pos_level"]}')

    # ── H. 精确开单参数 ───────────────────────────────────
    print('\n【H · 精确入场参数（梵天v4.2宪法）】')
    if r.get('entry_lo') and r.get('entry_hi'):
        in_zone = r['entry_lo'] <= r['price'] <= r['entry_hi']
        print(f'  入场区  : ${fmt(r["entry_lo"],2)} ~ ${fmt(r["entry_hi"],2)}'
              f'  {"✅ 当前价格在区内" if in_zone else "⏳ 等待价格进区"}')
        print(f'  止损    : ${fmt(r["sl"],2)}  ({fmt(r["sl_pct"],2)}%)')
        print(f'  TP1     : ${fmt(r["tp1"],2)}')
        print(f'  TP2     : ${fmt(r["tp2"],2)}')
        print(f'  RR比    : {fmt(r["rr1"],2)}')
    else:
        print('  ⚠️ 入场参数待计算（信号未触发）')
    print()
    print(f'  ⚠️  宏观拦截: {"生效 — BEAR_BOUNCE熊市反弹，自动降低仓位" if r.get("p0b_blocked") else "未触发"}')
    _dir2 = '>' if r['price'] > r['ema20_1h'] else '<'
    _act2 = '允许入场 ✅' if r['price'] > r['ema20_1h'] else '暂停做多 ⏳'
    print('  新宪法规则: RSI_1H达标 AND 价格%sEMA20_1H → %s' % (_dir2, _act2))
    print()
    print(sep)


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
if __name__ == '__main__':
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    for sym in symbols:
        try:
            print(f'\n⏳ 正在分析 {sym}（梵天35维全系统）...\n')
            r = full_analysis(sym)
            print_report(r)
        except Exception as e:
            import traceback
            pass  # [静默]
            traceback.print_exc()
        time.sleep(0.5)
