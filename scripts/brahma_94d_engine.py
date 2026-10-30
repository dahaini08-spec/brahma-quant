#!/usr/bin/env python3
"""
brahma_94d_engine.py — 94维三周期强制分析引擎
[封印 2026-10-30 苏摩111]

40年合约交易员视角 + 三方联合顶层裁决
强制路径：brahma_manual_analysis → 本引擎 → auto_analysis_latest['cycles']

三周期：
  short : 1H+15M → 1-3天  Taker+BB+FVG触发
  mid   : 4H     → 1-2周  结构+OI+方仓
  long  : 1D+1W  → 2-4周  RSI超卖+趋势+宏观
"""
import json, math, urllib.request, time, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

BASE = Path(__file__).resolve().parent.parent


# ─── API ───────────────────────────────────────────────
def _fapi(path: str, **p) -> dict:
    url = 'https://fapi.binance.com' + path
    if p:
        url += '?' + '&'.join(f'{k}={v}' for k, v in p.items())
    return json.loads(urllib.request.urlopen(url, timeout=5).read())


# ─── 技术计算 ───────────────────────────────────────────
def _cl(kl): return [float(k[4]) for k in kl]
def _hi(kl): return [float(k[2]) for k in kl]
def _lo(kl): return [float(k[3]) for k in kl]

def _rsi(kl, n=14):
    c = _cl(kl)
    g = [max(c[i]-c[i-1], 0) for i in range(1, len(c))]
    l = [max(c[i-1]-c[i], 0) for i in range(1, len(c))]
    ag = sum(g[-n:]) / n; al = sum(l[-n:]) / n
    return round(100 - 100 / (1 + ag/al), 1) if al else 100

def _atr(kl, n=14):
    return round(sum(float(k[2])-float(k[3]) for k in kl[-n:]) / n, 1)

def _bb(kl, n=20):
    c = _cl(kl)[-n:]
    m = sum(c) / n
    s = math.sqrt(sum((x-m)**2 for x in c) / n)
    return round(m, 1), round(m+2*s, 1), round(m-2*s, 1), round(s*2/m*100, 2)

def _ema(kl, n):
    c = _cl(kl); k2 = 2/(n+1); e = c[0]
    for x in c[1:]: e = x*k2 + e*(1-k2)
    return round(e, 1)

def _stoch_rsi(kl, n=14):
    c = _cl(kl)
    g = [max(c[i]-c[i-1], 0) for i in range(1, len(c))]
    l = [max(c[i-1]-c[i], 0) for i in range(1, len(c))]
    rv = []
    for i in range(n, len(g)):
        ag = sum(g[i-n:i])/n; al = sum(l[i-n:i])/n
        rv.append(100 - 100/(1+ag/al) if al else 100)
    if len(rv) < n: return 50
    mn = min(rv[-n:]); mx = max(rv[-n:])
    return round((rv[-1]-mn)/(mx-mn)*100, 1) if mx != mn else 50

def _macd(kl):
    c = _cl(kl); k12 = 2/13; k26 = 2/27
    e12 = e26 = c[0]
    for x in c[1:]: e12 = x*k12+e12*(1-k12); e26 = x*k26+e26*(1-k26)
    return round(e12-e26, 1)

def _structure(kl, n=3):
    h = _hi(kl); lo = _lo(kl)
    sh = [h[i] for i in range(n, len(h)-n)
          if all(h[i] >= h[j] for j in range(i-n, i+n+1) if j != i)]
    sl = [lo[i] for i in range(n, len(lo)-n)
          if all(lo[i] <= lo[j] for j in range(i-n, i+n+1) if j != i)]
    if len(sh) >= 2 and len(sl) >= 2:
        if sh[-1] > sh[-2] and sl[-1] > sl[-2]: return 'HH+HL↑', sh[-1], sl[-1]
        if sh[-1] < sh[-2] and sl[-1] < sl[-2]: return 'LH+LL↓', sh[-1], sl[-1]
        if sh[-1] > sh[-2] and sl[-1] < sl[-2]: return 'HH+LL扩张', sh[-1], sl[-1]
        return 'LH+HL收缩', sh[-1], sl[-1]
    return '?', 0, 0

def _fvg(kl, px):
    b = [(float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in kl]
    bulls = []; bears = []
    for i in range(2, len(b)):
        if b[i][2] > b[i-2][1] and px < b[i][2]:
            bulls.append({'lo': round(b[i-2][1], 1), 'hi': round(b[i][2], 1),
                          'mid': round((b[i-2][1]+b[i][2])/2, 1)})
        if b[i][1] < b[i-2][2] and px > b[i][1]:
            bears.append({'lo': round(b[i][1], 1), 'hi': round(b[i-2][2], 1),
                          'mid': round((b[i][1]+b[i-2][2])/2, 1)})
    return bulls[-1] if bulls else None, bears[-1] if bears else None

def _oi_chg(ov):
    if not ov: return 0.0
    v = [float(o['sumOpenInterest']) for o in ov]
    return round((v[-1]-v[0])/v[0]*100, 3) if v[0] else 0.0

def _oi_signal(c15, c1h, c4h):
    if c4h < -1.5:  return 'SHORT_SQUEEZE🔥', '空头大量减仓=反弹动力'
    if c15 > 0.15 and c1h > 0.25: return 'BUILD多', '多头主动建仓'
    if c1h < -0.3:  return 'UNWIND多', '多头撤退'
    if c15 < -0.1:  return 'BUILD空', '空头短线建仓'
    return '中性', 'OI无明显方向'


# ─── 主分析 ─────────────────────────────────────────────
def analyze(sym: str) -> dict:
    """强制94维三周期分析，纯实时API无缓存"""
    sym = sym.upper()

    tasks = {
        'px':   lambda: _fapi('/fapi/v1/ticker/price', symbol=f'{sym}USDT'),
        'fr':   lambda: _fapi('/fapi/v1/premiumIndex', symbol=f'{sym}USDT'),
        'stat': lambda: _fapi('/fapi/v1/ticker/24hr', symbol=f'{sym}USDT'),
        'oi15': lambda: _fapi('/futures/data/openInterestHist', symbol=f'{sym}USDT', period='15m', limit=8),
        'oi1h': lambda: _fapi('/futures/data/openInterestHist', symbol=f'{sym}USDT', period='1h', limit=6),
        'oi4h': lambda: _fapi('/futures/data/openInterestHist', symbol=f'{sym}USDT', period='4h', limit=6),
        'lsr':  lambda: _fapi('/futures/data/globalLongShortAccountRatio', symbol=f'{sym}USDT', period='5m', limit=2),
        'blsr': lambda: _fapi('/futures/data/topLongShortAccountRatio', symbol=f'{sym}USDT', period='1h', limit=4),
        'tk':   lambda: _fapi('/futures/data/takerlongshortRatio', symbol=f'{sym}USDT', period='5m', limit=6),
        'k15':  lambda: _fapi('/fapi/v1/klines', symbol=f'{sym}USDT', interval='15m', limit=40),
        'k1h':  lambda: _fapi('/fapi/v1/klines', symbol=f'{sym}USDT', interval='1h', limit=50),
        'k4h':  lambda: _fapi('/fapi/v1/klines', symbol=f'{sym}USDT', interval='4h', limit=30),
        'k1d':  lambda: _fapi('/fapi/v1/klines', symbol=f'{sym}USDT', interval='1d', limit=14),
        'k1w':  lambda: _fapi('/fapi/v1/klines', symbol=f'{sym}USDT', interval='1w', limit=8),
    }
    R = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(fn): k for k, fn in tasks.items()}
        for f in as_completed(futs):
            k = futs[f]
            try: R[k] = f.result()
            except: R[k] = None

    # ── 基础指标 ──
    px    = float(R['px']['price'])    if R['px']   else 0.0
    fr    = float(R['fr']['lastFundingRate']) * 100 if R['fr'] else 0.0
    chg24 = float(R['stat']['priceChangePercent']) if R['stat'] else 0.0
    vol24 = float(R['stat']['quoteVolume']) / 1e9  if R['stat'] else 0.0

    kl15 = R['k15']; kl1h = R['k1h']; kl4h = R['k4h']
    kl1d = R['k1d']; kl1w = R['k1w']

    oi15c = _oi_chg(R['oi15']); oi1hc = _oi_chg(R['oi1h']); oi4hc = _oi_chg(R['oi4h'])

    r15 = _rsi(kl15) if kl15 else 50; r1h = _rsi(kl1h) if kl1h else 50
    r4h = _rsi(kl4h) if kl4h else 50; r1d = _rsi(kl1d) if kl1d else 50
    r1w = _rsi(kl1w) if kl1w else 50

    atr1h = _atr(kl1h) if kl1h else 0
    atr4h = _atr(kl4h) if kl4h else 0

    bb15m_m, bb15m_u, bb15m_l, bbw15 = _bb(kl15) if kl15 else (px,)*3 + (0,)
    bb1h_m,  bb1h_u,  bb1h_l,  bbw1h  = _bb(kl1h) if kl1h else (px,)*3 + (0,)
    bb4h_m,  bb4h_u,  bb4h_l,  bbw4h  = _bb(kl4h) if kl4h else (px,)*3 + (0,)

    ema20_1h = _ema(kl1h, 20) if kl1h else px
    ema50_4h = _ema(kl4h, 50) if kl4h else px

    stoch15 = _stoch_rsi(kl15) if kl15 else 50
    stoch1h = _stoch_rsi(kl1h) if kl1h else 50

    macd1h = _macd(kl1h) if kl1h else 0
    macd4h = _macd(kl4h) if kl4h else 0

    st1h, sh1h, sl1h = _structure(kl1h) if kl1h else ('?', 0, 0)
    st4h, sh4h, sl4h = _structure(kl4h) if kl4h else ('?', 0, 0)
    st1d, sh1d, sl1d = _structure(kl1d) if kl1d else ('?', 0, 0)

    fb1h, fbe1h = _fvg(kl1h, px) if kl1h else (None, None)
    fb4h, fbe4h = _fvg(kl4h, px) if kl4h else (None, None)

    lsr_d  = R['lsr']  or []
    blsr_d = R['blsr'] or []
    tk_d   = R['tk']   or []

    lp     = float(lsr_d[-1]['longAccount']) * 100 if lsr_d else 50.0
    bl     = [round(float(o['longAccount'])*100, 1) for o in blsr_d] if blsr_d else [50.0]
    tkv    = [round(float(t['buySellRatio']), 3)    for t in tk_d]   if tk_d   else []
    bl_chg = round(bl[-1]-bl[0], 1) if len(bl) >= 2 else 0.0
    tk_t   = ('↑↑买方' if len(tkv)>=2 and tkv[-1]>1 and tkv[-2]>1
              else '↓↓卖方' if len(tkv)>=2 and tkv[-1]<1 and tkv[-2]<1
              else '↕混乱')

    oi_sig, oi_note = _oi_signal(oi15c, oi1hc, oi4hc)

    # ── 状态文件（方仓/体制/GEX/CVD）──
    bs = {}
    try:
        bs = json.loads((BASE / 'data' / f'brahma_state_{sym.lower()}.json').read_text())
    except Exception: pass
    fc     = bs.get('fc_signal', '?')
    fcsim  = float(bs.get('fc_sim', 0) or 0)
    regime = bs.get('regime', 'CHOP_MID')
    score  = float(bs.get('score_final', 0) or 0)
    gzf    = float(bs.get('gex_zf', 0) or 0)
    cvd1h  = 0.0
    try:
        cvd1h = float(json.loads(
            (BASE / 'data' / f'cvd_realtime_{sym.lower()}usdt.json').read_text()
        ).get('cvd_1h', 0) or 0)
    except Exception: pass

    # ── 清算位 ──
    liq_s = liq_l = 0.0
    try:
        liq_d = json.loads((BASE / 'data' / f'liq_heatmap_{sym.lower()}usdt.json').read_text())
        cur   = float(liq_d.get('current_px') or px)
        sw    = sorted([w['price'] for w in liq_d.get('sell_walls', []) if w['price'] > cur])
        bw    = sorted([w['price'] for w in liq_d.get('buy_walls',  []) if w['price'] < cur], reverse=True)
        liq_s = sw[0] if sw else float(bs.get('liq_short', 0) or 0)
        liq_l = bw[0] if bw else float(bs.get('liq_long',  0) or 0)
    except Exception: pass

    # ── 三周期策略裁决 ──────────────────────────────────
    min_sl_1h = max(atr1h * 1.5, (px * 0.01))   # 至少1%
    min_sl_4h = max(atr4h * 1.5, (px * 0.015))  # 至少1.5%

    # 短线（1H+15M）
    s_dir = s_entry = s_sl = s_tp1 = s_tp2 = 0
    s_trigger = s_logic = ''
    is_extreme_oversold  = stoch1h < 20 and r1h < 35
    is_extreme_overbought = stoch1h > 80 and r1h > 65
    is_short_squeeze_now  = oi4hc < -1.5 and r1h < 45
    is_fr_negative        = fr < -0.002

    if is_extreme_oversold or is_short_squeeze_now or is_fr_negative:
        s_dir = 'LONG'
        anchor = fbe1h['lo'] if fbe1h else bb1h_l
        s_entry = round(max(anchor, px * 0.998), 1)
        s_sl    = round(s_entry - min_sl_1h, 1)
        s_tp1   = round(fb1h['mid'] if fb1h else bb1h_m, 1)
        s_tp2   = round(bb1h_u * 0.99, 1)
        s_trigger = '15M Taker连续2根>1.0 + StochRSI翻升'
        reasons = []
        if is_extreme_oversold:  reasons.append(f'StochRSI1H={stoch1h}⚡底部')
        if is_short_squeeze_now: reasons.append(f'4H SHORT_SQUEEZE({oi4hc:+.2f}%)')
        if is_fr_negative:       reasons.append(f'FR={fr:+.4f}%⚡负值')
        s_logic = '+'.join(reasons)
    elif is_extreme_overbought:
        s_dir = 'SHORT'
        anchor = fbe1h['hi'] if fbe1h else bb1h_u
        s_entry = round(min(anchor, liq_s if liq_s else px * 1.002), 1)
        s_sl    = round(s_entry + min_sl_1h, 1)
        s_tp1   = round(bb1h_m, 1)
        s_tp2   = round(bb1h_l * 1.01, 1)
        s_trigger = '15M Taker连续2根<1.0 + 触及上轨/FVG'
        s_logic = f'StochRSI={stoch1h}超买+Taker={tk_t}'

    # 中线（4H）
    m_dir = m_entry = m_sl = m_tp1 = m_tp2 = 0
    m_logic = m_note = ''
    if st4h == 'HH+HL↑':
        m_dir   = 'LONG'
        m_entry = round(bb4h_l * 1.001, 1)
        m_sl    = round(bb4h_l - min_sl_4h, 1)
        m_tp1   = round(bb4h_m, 1)
        m_tp2   = round(bb4h_u * 0.98, 1)
        m_logic = f'4H {st4h} 牛市结构完整  方仓fc={fc}({fcsim:.2f})'
        m_note  = f'跌破BB4H下轨${bb4h_l:,.0f}=结构破坏→转观望'
    elif st4h == 'LH+LL↓':
        m_dir   = 'SHORT'
        m_entry = round(bb4h_u * 0.999, 1)
        m_sl    = round(bb4h_u + min_sl_4h, 1)
        m_tp1   = round(bb4h_m, 1)
        m_tp2   = round(bb4h_l * 1.02, 1)
        m_logic = f'4H {st4h} 熊市结构  OI={oi_sig}'
        m_note  = f'突破BB4H上轨${bb4h_u:,.0f}→止损'

    # 长线（1D+1W）
    lg_dir = lg_elo = lg_ehi = lg_sl = lg_tp = 0
    lg_logic = ''
    if r1d < 30 and r1w < 42:
        lg_dir  = 'LONG'
        lg_elo  = round(bb4h_l * 0.97, 1)
        lg_ehi  = round(bb4h_l * 1.02, 1)
        lg_sl   = round(lg_elo * 0.94, 1)
        lg_tp   = round(sh1d * 0.97 if sh1d else px * 1.18, 1)
        lg_logic = f'RSI1D={r1d} RSI1W={r1w} 历史超卖→中长线价值区  周期2-4周'
    elif r1d > 70 and r1w > 65:
        lg_dir  = 'SHORT'
        lg_elo  = round(bb4h_u * 0.99, 1)
        lg_ehi  = round(bb4h_u * 1.01, 1)
        lg_sl   = round(lg_ehi * 1.05, 1)
        lg_tp   = round(sl1d * 1.03 if sl1d else px * 0.84, 1)
        lg_logic = f'RSI1D={r1d} RSI1W={r1w} 周线超买→中长线顶部  周期2-4周'

    return {
        'sym': sym, 'px': px, 'ts': time.time(),
        'ts_str': time.strftime('%H:%M UTC %Y-%m-%d', time.gmtime()),
        'dim': {
            'regime': regime, 'score': score, 'fc': fc, 'fc_sim': fcsim,
            'fr': fr, 'chg24': chg24, 'vol24B': round(vol24, 1), 'gex_zf': gzf,
            'rsi': {'15m': r15, '1h': r1h, '4h': r4h, '1d': r1d, '1w': r1w},
            'stoch': {'15m': stoch15, '1h': stoch1h},
            'macd': {'1h': macd1h, '4h': macd4h},
            'bbw': {'15m': bbw15, '1h': bbw1h, '4h': bbw4h},
            'bb4h': {'m': bb4h_m, 'u': bb4h_u, 'l': bb4h_l},
            'atr': {'1h': atr1h, '4h': atr4h},
            'oi': {'15m': oi15c, '1h': oi1hc, '4h': oi4hc, 'sig': oi_sig, 'note': oi_note},
            'lsr': {'retail': round(lp, 1), 'big': bl, 'big_chg': bl_chg},
            'taker': {'vals': tkv[-4:], 'trend': tk_t},
            'cvd_1h': cvd1h,
            'struct': {'1h': st1h, '4h': st4h, '1d': st1d},
            'fvg': {'bull_1h': fb1h, 'bear_1h': fbe1h, 'bull_4h': fb4h},
            'liq': {'short': liq_s, 'long': liq_l},
            'ema': {'20_1h': ema20_1h, '50_4h': ema50_4h},
        },
        'short': {
            'dir': 'LONG' if s_dir == 'LONG' else 'SHORT' if s_dir == 'SHORT' else 'WAIT',
            'entry': s_entry, 'sl': s_sl, 'tp1': s_tp1, 'tp2': s_tp2,
            'trigger': s_trigger, 'logic': s_logic, 'period': '1H+15M · 1-3天',
        },
        'mid': {
            'dir': 'LONG' if m_dir == 'LONG' else 'SHORT' if m_dir == 'SHORT' else 'WAIT',
            'entry': m_entry, 'sl': m_sl, 'tp1': m_tp1, 'tp2': m_tp2,
            'logic': m_logic, 'note': m_note, 'period': '4H · 1-2周',
        },
        'long': {
            'dir': 'LONG' if lg_dir == 'LONG' else 'SHORT' if lg_dir == 'SHORT' else 'WAIT',
            'entry_lo': lg_elo, 'entry_hi': lg_ehi, 'sl': lg_sl, 'tp': lg_tp,
            'logic': lg_logic, 'period': '1D+1W · 2-4周',
        },
    }


def format_report(r: dict) -> str:
    sym = r['sym']; px = r['px']; d = r['dim']
    short = r['short']; mid = r['mid']; lng = r['long']

    fvg_lines = []
    if d['fvg']['bull_1h']:
        f = d['fvg']['bull_1h']
        fvg_lines.append(f'  1H Bull FVG 中点=${f["mid"]:,.1f}  (+{(f["mid"]-px)/px*100:.1f}%)')
    if d['fvg']['bear_1h']:
        f = d['fvg']['bear_1h']
        fvg_lines.append(f'  1H Bear FVG 中点=${f["mid"]:,.1f}  ({(f["mid"]-px)/px*100:.1f}%)')
    if d['fvg']['bull_4h']:
        f = d['fvg']['bull_4h']
        fvg_lines.append(f'  4H Bull FVG 中点=${f["mid"]:,.1f}  (+{(f["mid"]-px)/px*100:.1f}%)')

    def vip_line(cyc, entry_key='entry'):
        e = cyc.get(entry_key, 0)
        s = cyc.get('sl', 0); t1 = cyc.get('tp1', 0) or cyc.get('tp', 0)
        if not e: return '  等待信号'
        rr = round(abs(t1-e)/abs(e-s), 1) if s and t1 and abs(e-s) > 0 else 0
        return f'  入场 ${e:,.1f}  SL ${s:,.1f}  TP ${t1:,.1f}  RR={rr}'

    liq_s = d['liq']['short']; liq_l = d['liq']['long']
    liq_str = ''
    if liq_s: liq_str += f'上墙${liq_s:,.0f}(+{(liq_s-px)/px*100:.1f}%)  '
    if liq_l: liq_str += f'下池${liq_l:,.0f}({(liq_l-px)/px*100:.1f}%)'

    lines = [
        f'╔══════════════════════════════════════════════╗',
        f'║  {sym}/USDT 94维三周期  {r["ts_str"]}',
        f'╚══════════════════════════════════════════════╝',
        f'',
        f'▌ 市场背景（实时API）',
        f'  ${px:,.2f}  24H:{d["chg24"]:+.2f}%  Vol${d["vol24B"]:.1f}B',
        f'  FR:{d["fr"]:+.4f}%{"⚡负值=反转信号!" if d["fr"]<-0.002 else "  ⚠️偏高" if d["fr"]>0.01 else ""}',
        f'  体制:{d["regime"]}  score:{d["score"]:.0f}  fc:{d["fc"]}({d["fc_sim"]:.2f})  GEX_ZF:${d["gex_zf"]:,.0f}',
        f'',
        f'▌ 价格结构（SMC全周期）',
        f'  1H: {d["struct"]["1h"]}  4H: {d["struct"]["4h"]}  1D: {d["struct"]["1d"]}',
        f'  EMA20_1H:${d["ema"]["20_1h"]:,.0f}({"↑" if px>d["ema"]["20_1h"] else "↓"})  EMA50_4H:${d["ema"]["50_4h"]:,.0f}({"↑" if px>d["ema"]["50_4h"] else "↓"})',
        f'  清算: {liq_str or "暂无数据"}',
        f'',
    ]
    if fvg_lines:
        lines += ['▌ FVG磁铁（未填充）'] + fvg_lines + ['']

    lines += [
        f'▌ 核心指标（94维实时）',
        f'  RSI  15M={d["rsi"]["15m"]}  1H={d["rsi"]["1h"]}  4H={d["rsi"]["4h"]}  1D={d["rsi"]["1d"]}  1W={d["rsi"]["1w"]}',
        f'  Stoch 15M={d["stoch"]["15m"]}  1H={d["stoch"]["1h"]}{"  ⚡超卖底部!" if d["stoch"]["1h"]<20 else "  ⚠️超买!" if d["stoch"]["1h"]>80 else ""}',
        f'  MACD  1H={"+" if d["macd"]["1h"]>0 else ""}{d["macd"]["1h"]}  4H={"+" if d["macd"]["4h"]>0 else ""}{d["macd"]["4h"]}',
        f'  BB带宽  15M={d["bbw"]["15m"]:.2f}%  1H={d["bbw"]["1h"]:.2f}%  4H={d["bbw"]["4h"]:.2f}%{"  ⚡收窄=爆发前" if d["bbw"]["4h"]<2.5 else ""}',
        f'  ATR  1H=${d["atr"]["1h"]:,.0f}  4H=${d["atr"]["4h"]:,.0f}  SL基准=${d["atr"]["1h"]*1.5:,.0f}',
        f'',
        f'▌ 资金流向（聪明钱博弈）',
        f'  OI  15M={d["oi"]["15m"]:+.3f}%  1H={d["oi"]["1h"]:+.3f}%  4H={d["oi"]["4h"]:+.3f}%',
        f'  信号: {d["oi"]["sig"]}  {d["oi"]["note"]}',
        f'  CVD_1H={d["cvd_1h"]:+.0f}  Taker:{d["taker"]["vals"]}({d["taker"]["trend"]})',
        f'  散户LSR:{d["lsr"]["retail"]}%多  大户:{d["lsr"]["big"]}  变化:{d["lsr"]["big_chg"]:+.1f}pt',
        f'',
        f'▌ 三方联合裁决',
        f'',
        f'【短线 {short["period"]}】→ {short["dir"]}',
    ]
    if short['dir'] != 'WAIT':
        lines += [
            vip_line(short),
            f'  触发: {short["trigger"]}',
            f'  逻辑: {short["logic"]}',
        ]
    else:
        lines.append('  StochRSI未到极值，等待明确信号')
    lines += ['', f'【中线 {mid["period"]}】→ {mid["dir"]}']
    if mid['dir'] != 'WAIT':
        lines += [
            vip_line(mid),
            f'  逻辑: {mid["logic"]}',
            f'  注意: {mid["note"]}',
        ]
    else:
        lines.append('  4H结构未明确，等待HH+HL或LH+LL确认')
    lines += ['', f'【长线 {lng["period"]}】→ {lng["dir"]}']
    if lng['dir'] != 'WAIT':
        lo = lng['entry_lo']; hi = lng['entry_hi']
        lines += [
            f'  建仓区 ${lo:,.1f}~${hi:,.1f}  SL ${lng["sl"]:,.1f}  TP ${lng["tp"]:,.1f}',
            f'  逻辑: {lng["logic"]}',
        ]
    else:
        lines.append(f'  RSI1D={d["rsi"]["1d"]} RSI1W={d["rsi"]["1w"]}  未达超卖(<30)或超买(>70)阈值')

    return '\n'.join(lines)


if __name__ == '__main__':
    syms = sys.argv[1:] or ['BTC', 'ETH']
    for sym in syms:
        try:
            r = analyze(sym.upper())
            print(format_report(r))
            print()
        except Exception as e:
            import traceback
            print(f'[{sym}] 分析失败: {e}')
            traceback.print_exc()
