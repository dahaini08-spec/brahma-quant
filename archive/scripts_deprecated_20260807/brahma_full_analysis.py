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
from brahma_brain.math_utils import rsi as _mu_rsi, ema as _mu_ema  # INT-1统一实现
from brahma_brain.math_utils import _ema  # P1修复(2026-07-12): 补充_ema导入，修复NameError

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

def _rsi(closes, n=14):

    # [INT-1] 统一实现已移至 math_utils.rsi，此函数保留兼容
    d = np.diff(np.array(closes[-(n+2):], dtype=float))
    g = np.where(d > 0, d, 0); lo = np.where(d < 0, -d, 0)
    ag = g.mean(); al = lo.mean()
    return round(100 - 100 / (1 + ag / al), 1) if al > 0 else 99.0

def _macd_hist(closes):
    c = [float(x) for x in closes]  # 确保list而非numpy array传入_ema
    m12 = _ema(c, 12); m26 = _ema(c, 26)
    ml = [a - b for a, b in zip(m12, m26)]; sig = _ema(ml, 9)
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

        # ══ [v5.6 CANONICAL v2.0 自愈补全 2026-07-13 设计院封印] ══
        # 每次分析必须覆盖全部维度，自愈机制确保无缺口

        # S4: Bull OB / Bear OB 精确价位 + PD Zone + 流动性猎杀池
        'bull_ob': _calc_bull_ob(k4h, price),
        'bear_ob': _calc_bear_ob(k4h, price),
        'pd_zone': _calc_pd_zone(h4, l4, price),
        'liq_above': max(sh4) if sh4 else round(price * 1.025, 2),
        'liq_below': min(sl4) if sl4 else round(price * 0.975, 2),

        # S5: CVD订单流近似（1H近20根，正=买方主导）
        'cvd_1h': _calc_cvd(c1, v1, n=20),

        # S6: N系列信号 / Kelly / B2接近度 / VaR95
        'n03_session': _calc_n03_session(),
        'n08_regime_rsi': _calc_n08(regime, rsi_1h),
        'n16_atr_zone': _calc_n16_atr(atr_4h, h4, l4, c4),
        'mtf_resonance': _calc_mtf_resonance(rsi_15m, rsi_1h, rsi_4h, rsi_1d, regime),
        'b2_ema200_4h_gap': _calc_b2_gap(c4, price),
        'kelly_pct': _calc_kelly(regime),
        'var95_daily': _calc_var95(atr_4h, price),
        'bull_bonus_detail': _calc_bull_bonus(regime, rsi_1h, rsi_4h, price,
                                               ema200_1h, params),
        'timing_score': raw.get('timing_score', raw.get('timing_filter_score')),
        # VIP策略卡BRAHMA标签
        'brahma_tag': _calc_brahma_tag(sym, score_final, raw, params),
    }
    return result


# ══════════════════════════════════════════════════════════
# [v5.6] CANONICAL v2.0 自愈辅助函数
# ══════════════════════════════════════════════════════════
def _calc_bull_ob(k4h, price):
    """找最近Bull OB（下跌K后收阳，在价格下方）"""
    opens=[float(x[1]) for x in k4h]
    closes=[float(x[4]) for x in k4h]
    highs=[float(x[2]) for x in k4h]
    lows=[float(x[3]) for x in k4h]
    candidates=[]
    for i in range(len(k4h)-6, len(k4h)-1):
        if closes[i] < opens[i]:  # 下跌K
            candidates.append({'lo':round(lows[i],4),'hi':round(highs[i],4),
                                'mid':round((highs[i]+lows[i])/2,4)})
    below=[c for c in candidates if c['mid']<price]
    if not below and candidates: below=[min(candidates,key=lambda x:abs(x['mid']-price))]
    if not below: below=[{'lo':round(price*0.985,4),'hi':round(price*0.995,4),'mid':round(price*0.99,4)}]
    ob=below[-1]
    ob['dist_pct']=round((price-ob['mid'])/price*100,2)
    return ob

def _calc_bear_ob(k4h, price):
    """找最近Bear OB（上涨K，在价格上方）"""
    opens=[float(x[1]) for x in k4h]
    closes=[float(x[4]) for x in k4h]
    highs=[float(x[2]) for x in k4h]
    lows=[float(x[3]) for x in k4h]
    candidates=[]
    for i in range(len(k4h)-6, len(k4h)-1):
        if closes[i] > opens[i]:  # 上涨K
            candidates.append({'lo':round(lows[i],4),'hi':round(highs[i],4),
                                'mid':round((highs[i]+lows[i])/2,4)})
    above=[c for c in candidates if c['mid']>price]
    if not above and candidates: above=[min(candidates,key=lambda x:abs(x['mid']-price))]
    if not above: above=[{'lo':round(price*1.005,4),'hi':round(price*1.015,4),'mid':round(price*1.01,4)}]
    ob=above[0]
    ob['dist_pct']=round((ob['mid']-price)/price*100,2)
    return ob

def _calc_pd_zone(h4, l4, price):
    """Price Delivery Zone: 30日高低区间内的价格位置"""
    high30=max(h4[-90:] if len(h4)>=90 else h4)
    low30=min(l4[-90:] if len(l4)>=90 else l4)
    pos=(price-low30)/(high30-low30) if high30>low30 else 0.5
    zone='DISCOUNT' if pos<0.45 else ('PREMIUM' if pos>0.55 else 'EQUILIBRIUM')
    bias='机构做多区' if zone=='DISCOUNT' else ('机构做空区' if zone=='PREMIUM' else '均衡中性')
    return {'zone':zone,'pos':round(pos,3),'bias':bias,'high30':round(high30,2),'low30':round(low30,2)}

def _calc_cvd(closes, volumes, n=20):
    """CVD近似：收阳=买方，收阴=卖方"""
    cvd=0.0
    for i in range(-n, 0):
        if closes[i] > closes[i-1]: cvd += volumes[i]
        else: cvd -= volumes[i]
    direction = '买方主导✅' if cvd > 0 else '卖方主导🔴'
    return {'value': round(cvd, 1), 'direction': direction}

def _calc_n03_session():
    """N03时段权重：欧洲/美洲/亚洲开盘高活跃窗口"""
    import datetime
    hour = datetime.datetime.utcnow().hour
    if 9 <= hour < 12:   return 'EU_OPEN✅ 欧洲开盘(09-12UTC) 高活跃'
    elif 13 <= hour < 16: return 'US_OPEN✅ 美洲开盘(13-16UTC) 最高活跃'
    elif 1 <= hour < 4:   return 'AS_OPEN🟡 亚洲开盘(01-04UTC)'
    else:                  return 'MID_SESSION⚪ 盘中低活跃段'

def _calc_n08(regime, rsi_1h):
    """N08体制+RSI共振信号"""
    bull = any(r in (regime or '') for r in ['BULL_TREND','BULL_EARLY','BEAR_RECOVERY'])
    bear = 'BEAR_TREND' in (regime or '')
    if bull and rsi_1h < 30:  return 'N08_STRONG✅ 牛市体制+深度超卖 → 最强反弹窗口'
    if bull and rsi_1h < 40:  return 'N08_OK✅ 牛市体制+超卖 → 反弹候选'
    if bull and rsi_1h > 75:  return 'N08_CAUTION⚠️ 牛市体制+超买 → 谨慎追多'
    if bear and rsi_1h > 70:  return 'N08_BEAR✅ 空头体制+超买 → 做空窗口'
    return f'N08_NEUTRAL🟡 体制={regime} RSI_1H={rsi_1h} 中性区间'

def _calc_n16_atr(atr_4h, h4, l4, c4):
    """N16 ATR禁区检测"""
    trs=[max(h4[i]-l4[i],abs(h4[i]-c4[i-1]),abs(l4[i]-c4[i-1])) for i in range(1,min(31,len(c4)))]
    atr_hist=sum(trs)/len(trs) if trs else atr_4h
    ratio=round(atr_4h/atr_hist,2) if atr_hist>0 else 1.0
    if ratio > 1.8:  return f'N16_FORBID🚨 ATR扩张{ratio}x 禁止入场'
    if ratio > 1.3:  return f'N16_REDUCE⚠️ ATR扩张{ratio}x 建议减仓'
    return f'N16_OK✅ ATR正常{ratio}x 可正常仓位'

def _calc_mtf_resonance(r15, r1h, r4h, r1d, regime):
    """多周期共振评分"""
    bull = any(r in (regime or '') for r in ['BULL','BEAR_RECOVERY'])
    signals=[]
    if r15 < 35: signals.append('15m超卖')
    if r1h < 35: signals.append('1h超卖')
    if r4h < 40: signals.append('4h超卖')
    if r1d > 60: signals.append('1d偏强')
    if r1d > 55 and bull: signals.append('体制多头')
    n=len(signals)
    grade='STRONG✅' if n>=4 else ('OK🟡' if n>=2 else 'WEAK⚪')
    return {'grade':grade,'n':n,'signals':signals}

def _calc_b2_gap(c4, price):
    """B2: 距EMA200_4H的距离（过远=风险）"""
    try:
        e=c4[0]; k=2/(200+1)
        for c in c4[1:]: e=c*k+e*(1-k)
        gap=round((price-e)/e*100,2)
        status='✅扎实' if abs(gap)<3 else ('🟡适中' if abs(gap)<8 else '⚠️过远')
        return {'ema200_4h':round(e,2),'gap_pct':gap,'status':status}
    except: return {'ema200_4h':None,'gap_pct':None,'status':'?'}

def _calc_kelly(regime):
    """Kelly仓位建议（基于体制历史WR和RR）"""
    wr_map={'BULL_TREND':0.72,'BULL_EARLY':0.68,'BEAR_RECOVERY':0.72,
            'CHOP_MID':0.58,'BEAR_EARLY':0.60,'BEAR_TREND':0.65}
    rr_map={'BULL_TREND':2.0,'BULL_EARLY':1.85,'BEAR_RECOVERY':2.0,
            'CHOP_MID':1.0,'BEAR_EARLY':1.2,'BEAR_TREND':1.5}
    wr=wr_map.get(regime or 'CHOP_MID',0.60); rr=rr_map.get(regime or 'CHOP_MID',1.0)
    kelly=max(0,round((wr*rr-(1-wr))/rr*100,1))
    return {'kelly_pct':kelly,'wr':wr,'rr':rr,'note':f'系统限制执行5~10%NAV'}

def _calc_var95(atr_4h, price):
    """VaR95单日估算（1.65×ATR_4H×6根/日×价格比）"""
    var_usdt=round(atr_4h*1.65,4)
    var_pct=round(var_usdt/price*100,2) if price>0 else 0
    return {'var_usdt':var_usdt,'var_pct':var_pct}

def _calc_bull_bonus(regime, rsi_1h, rsi_4h, price, ema200_1h, params):
    """BullBonus逐项拆解"""
    bonus=[]
    bull_regime=any(r in (regime or '') for r in ['BULL_TREND','BULL_EARLY','BEAR_RECOVERY'])
    if bull_regime: bonus.append(('体制多头','+10'))
    if rsi_1h < 35: bonus.append(('RSI_1H超卖<35','+12'))
    if rsi_4h < 40: bonus.append(('RSI_4H超卖<40','+8'))
    if ema200_1h and price > ema200_1h: bonus.append(('价格>EMA200_1H','+8'))
    if params.get('rr1',0) and float(params.get('rr1',0))>=2.0: bonus.append(('RR≥2.0','+5'))
    total=sum(int(v.replace('+','').replace('-','')) for _,v in bonus)
    return {'items':bonus,'total':total}

def _calc_brahma_tag(sym, score, raw, params):
    """生成标准BRAHMA标签"""
    import hashlib, time
    direction=raw.get('direction','LONG')
    regime=raw.get('regime','?')
    ts=time.strftime('%Y%m%d%H%M')
    sha8=hashlib.sha256(f'{sym}{score}{direction}{ts}'.encode()).hexdigest()[:8]
    action_code='ENTER' if (raw.get('valid_signal') and score>=155) else ('WATCH' if score>=130 else 'SKIP')
    return f'[BRAHMA:{action_code}:FULL:{sym}:{score:.0f}:{direction}:{regime}:{ts}:{sha8}]'


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
    print(f'  时机徽章    : {r["timing_badge"] or "—"}  score={r.get("timing_score") or "—"}')

    # ── S4_EX. SMC精确完整结构（自愈补全）——————————————————
    print('\n《S4_EX · SMC精确全景—Bull OB / Bear OB / PD Zone / 流动性池》')
    bob = r.get('bull_ob', {})
    beob = r.get('bear_ob', {})
    pd = r.get('pd_zone', {})
    liq_a = r.get('liq_above', 0); liq_b = r.get('liq_below', 0)
    print(f'  Bull OB(做多订单块): ${fmt(bob.get("lo",0),2)}~${fmt(bob.get("hi",0),2)}'
          f'  中位=${fmt(bob.get("mid",0),2)}  dist={bob.get("dist_pct","?")}%')
    _ob_in = '✅ 当前价在OB内' if bob.get('lo',0)<=r['price']<=bob.get('hi',0) else '⏳ 等待进入OB区'
    print(f'    含义: 机构上轮在此区大量买入, {_ob_in}')
    print(f'  Bear OB(做空订单块): ${fmt(beob.get("lo",0),2)}~${fmt(beob.get("hi",0),2)}'  
          f'  中位=${fmt(beob.get("mid",0),2)}  dist=+{beob.get("dist_pct","?")}%')
    print(f'  PD Zone: {pd.get("zone","?")}({pd.get("pos",0):.1%})  机构偏向={pd.get("bias","?")}'
          f'  30日高=${fmt(pd.get("high30",0),0)} 低=${fmt(pd.get("low30",0),0)}')
    liq_a_pct=round((liq_a-r['price'])/r['price']*100,2) if liq_a else 0
    liq_b_pct=round((r['price']-liq_b)/r['price']*100,2) if liq_b else 0
    print(f'  上方流动性猎杀池: ${fmt(liq_a,2)}(+{liq_a_pct}%) ← 空头止损密集⇒诱多突破目标')
    print(f'  下方流动性猎杀池: ${fmt(liq_b,2)}(-{liq_b_pct}%) ← 多头止损密集⇒洗盘风险区')

    # ── S5_EX. CVD + 多周期共振（自愈补全）————————————————
    print('\n《S5_EX · CVD订单流 + 多周期共振》')
    cvd = r.get('cvd_1h', {})
    mtf = r.get('mtf_resonance', {})
    print(f'  CVD(1H近20根近似): {cvd.get("value",0):+.1f} → {cvd.get("direction","?")}')
    print(f'  多周期共振: {mtf.get("grade","?")} n={mtf.get("n",0)}/5'
          f'  [{" + ".join(mtf.get("signals",[]))}]')

    # ── S6_EX. N系列 / Kelly / B2 / VaR95（自愈补全）————————
    print('\n《S6_EX · N系列 / Kelly / B2 / VaR95》')
    b2 = r.get('b2_ema200_4h_gap', {})
    kelly = r.get('kelly_pct', {})
    var95 = r.get('var95_daily', {})
    bb_detail = r.get('bull_bonus_detail', {})
    print(f'  N03时段 : {r.get("n03_session","?")}') 
    print(f'  N08信号 : {r.get("n08_regime_rsi","?")}')
    print(f'  N16 ATR : {r.get("n16_atr_zone","?")}')
    print(f'  B2接近度: EMA200_4H={fmt(b2.get("ema200_4h",0),2)}'  
          f'  gap={b2.get("gap_pct","?"):+}%  {b2.get("status","?")}')
    print(f'  Kelly仓位: {kelly.get("kelly_pct","?")}% NAV'
          f'  (WR={kelly.get("wr",0):.0%} RR={kelly.get("rr",0)}) → {kelly.get("note","")}')
    print(f'  VaR95   : 单日下行风险 ≈ ${var95.get("var_usdt",0):.2f} ({var95.get("var_pct",0):.2f}%)')
    print(f'  BullBonus拆解:')
    for item,val in bb_detail.get('items',[]): print(f'    {item}: {val}')
    print(f'    小计加成: +{bb_detail.get("total",0)}分')

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

    # ── VIP策略卡（分数达标时自动输出）————————————————
    if r['score_final'] >= 130:
        print()
        print('《VIP策略卡 · BRAHMA标签》')
        tag = r.get('brahma_tag', '')
        print(f'  {tag}')
        if r.get('entry_lo'):
            print(f'  方向 : {r["_raw"].get("direction","LONG")} | 体制 : {r["regime"]} | Score : {r["score_final"]:.0f}')
            print(f'  入场 : ${fmt(r["entry_lo"],2)}~${fmt(r["entry_hi"],2)}')
            print(f'  止损 : ${fmt(r["sl"],2)} SL%={fmt(r["sl_pct"],2)}%')
            print(f'  TP1   : ${fmt(r["tp1"],2)}  TP2 : ${fmt(r["tp2"],2)}  RR={fmt(r["rr1"],2)}')
            print(f'  尺寸  : {kelly.get("kelly_pct",5)}% NAV 建议 | VaR95: {var95.get("var_pct",0):.2f}%/日')
    print()
    print(sep)


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════
# [设计院 2026-07-13 封口封印] 全景矩阵输出替换
# 所有分析数据按照全景矩阵格式输出（brahma_panorama_report）
# print_report 保留兼容，但主入口已升级为全景模式
# ══════════════════════════════════════════════════════════

def print_panorama_report(r: dict):
    """
    全景矩阵报告输出 — 封口版（替代原 print_report）
    融合：35维评分 + 外部扩展层 + 时机过滤 + 风险标志
    """
    try:
        import sys as _psys, os as _pos
        _psys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.brahma_analysis_runner import run_analysis_full as _raf
        from brahma_brain.formatter import brahma_panorama_report as _pano

        # 优先使用已有的 _panorama_full 字段（run_analysis_full 自动挂载）
        pano_text = r.get('_panorama_full', '')
        if not pano_text:
            # fallback: 直接生成（brahma_full_analysis 的 r 与 run_analysis 格式略有差异，做适配）
            _adapt = {
                'symbol':       r.get('sym', 'BTCUSDT'),
                'score_final':  r.get('score_final', 0),
                'score':        r.get('score_final', 0),
                'regime':       r.get('regime', '?'),
                'direction':    r.get('_raw', {}).get('direction', r.get('signal_dir', 'LONG')),
                'valid_signal': r.get('valid', False),
                'valid':        r.get('valid', False),
                'action':       r.get('action', 'WATCH'),
                'timing_status': r.get('timing_status', 'UNKNOWN'),
                'params':       {
                    'entry_lo':  r.get('entry_lo'),
                    'entry_hi':  r.get('entry_hi'),
                    'stop_loss': r.get('sl'),
                    'tp1':       r.get('tp1'),
                    'tp2':       r.get('tp2'),
                    'rr1':       r.get('rr1'),
                },
                'confluence':   {'score': r.get('score_final', 0), 'breakdown': {}},
                '_ext_score_bonus': 0,
                '_ext_score_detail': {},
            }
            pano_text = _pano(_adapt, compact=False)
        print(pano_text)
    except Exception as _pe:
        # 降级：使用原始 print_report
        print(f'[PANORAMA降级] {_pe}')
        print_report(r)


if __name__ == '__main__':
    import time as _time_main
    # [P1-A 设计院封印 2026-07-23 苏摩111] 播报模式优先
    # 根因: brahma_1hao(30m)+brahma_full(1h)同时分析BTC/ETH → 每小时第0min双重推送
    # 方案: 1H内已有有效信号 → 直接播报; 无信号才重新分析
    _now_main = _time_main.time()
    _DATA_MAIN = Path(__file__).parent.parent / 'data'
    _lsl_main  = _DATA_MAIN / 'live_signal_log.jsonl'

    def _recent_valid(syms, window_sec=3600):
        """1H内有效信号列表"""
        _res = []
        if not _lsl_main.exists():
            return _res
        for _ln in _lsl_main.read_text().split('\n')[-500:]:
            try:
                _d = json.loads(_ln)
                if _d.get('symbol','') not in syms: continue
                if not _d.get('valid'): continue
                _ts = float(_d.get('ts', 0) or 0)
                if _now_main - _ts > window_sec: continue
                _exp = _d.get('expires_at','')
                if _exp:
                    from datetime import timezone as _tz
                    _exp_ts = datetime.fromisoformat(_exp.replace('Z','+00:00')).timestamp()
                    if _now_main > _exp_ts: continue
                _res.append(_d)
            except: pass
        return _res

    symbols = [s for s in sys.argv[1:] if not s.startswith('--')]
    if not symbols:
        symbols = ['BTCUSDT', 'ETHUSDT']
    use_panorama = '--no-panorama' not in sys.argv
    _force = '--force' in sys.argv  # --force 强制重分析

    for sym in symbols:
        try:
            sym_u = sym.upper()
            if not sym_u.endswith('USDT'):
                sym_u += 'USDT'

            # 播报模式：1H内已有有效信号且非强制
            if not _force:
                _existing = _recent_valid({sym_u}, window_sec=3600)
                if _existing:
                    _s = max(_existing, key=lambda x: x.get('score', 0))
                    _age = int((_now_main - float(_s.get('ts', _now_main))) / 60)
                    print(f'\n📊 [{sym_u}] 播报模式 (1H内已有信号，跳过重分析)')
                    print(f'  信号: {_s.get("signal_id","")[:8]}  score={_s.get("score",0):.0f}  {_age}min前')
                    print(f'  入场: ${_s.get("entry_lo",0)}~${_s.get("entry_hi",0)}')
                    print(f'  体制: {_s.get("regime","")}  {_s.get("timing_badge","")}')
                    print(f'  [播报模式] 避免与auto-1hao重复推送')
                    continue

            # 无有效信号 → 正常35维分析
            print(f'\n⏳ 正在分析 {sym_u}（梵天35维全系统 · 全景模式）...\n')
            r = full_analysis(sym_u)
            if use_panorama:
                print_panorama_report(r)
            else:
                print_report(r)
        except Exception as e:
            import traceback
            traceback.print_exc()
        _time_main.sleep(0.5)