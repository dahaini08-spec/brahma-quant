#!/usr/bin/env python3
"""
brahma_deep_analysis.py — 梵天2.0强制深度分析引擎
[封印 2026-10-19 苏摩111]

设计原则（首席专家版）:
  1. 所有维度数据逐一显示，无隐藏
  2. 推理逻辑链清晰可见（每个结论说明原因）
  3. 40年实战经验深度融合（不是数据罗列）
  4. GEX/FVG/清算/LSR/OI/Taker 七维共振完整输出
  5. council三方Groq并发投票（0.57s）
  6. 最终VIP策略必须通过ATR验证

接入: 苏摩发"分析BTC" / "梵天分析" / "看盘" 触发
"""
import sys, json, time, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent.parent  # trading-system根目录（不管从scripts/还是根目录调用）
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))


def fetch(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=7) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _rsi(closes: list) -> float:
    if len(closes) < 3:
        return 50.0
    diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]
    ag = sum(gains) / len(gains) or 0.001
    al = sum(losses) / len(losses) or 0.001
    return round(100 - 100 / (1 + ag / al), 1)


def _atr4h(k4h: list) -> float:
    if len(k4h) < 2:
        return 0
    trs = [max(float(k[2]) - float(k[3]),
               abs(float(k[2]) - float(k4h[i-1][4])),
               abs(float(k[3]) - float(k4h[i-1][4])))
           for i, k in enumerate(k4h[1:], 1)]
    return round(sum(trs) / len(trs), 1)


# ── Step1: 全量数据拉取（U本位宪法）─────────────────────────────────────

def fetch_all(sym: str) -> dict:
    s = sym.upper() + 'USDT'
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            'price':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={s}'),
            'stats':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={s}'),
            'k1h':    ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&limit=15'),
            'k4h':    ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=4h&limit=10'),
            'k1d':    ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1d&limit=5'),
            'fr':     ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}'),
            'oi':     ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/openInterest?symbol={s}'),
            'oi_h':   ex.submit(fetch, f'https://fapi.binance.com/futures/data/openInterestHist?symbol={s}&period=1h&limit=6'),
            'lsr_g':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=5m&limit=6'),
            'lsr_t':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={s}&period=5m&limit=3'),
            'lsr_p':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={s}&period=5m&limit=3'),
            'taker':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=5m&limit=6'),
            'depth':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/depth?symbol={s}&limit=20'),
        }
        raw = {k: f.result(timeout=10) for k, f in futs.items()}

    px   = float(raw['price'].get('price', 0))
    fr   = raw['fr']
    mark = float(fr.get('markPrice', px))
    idx  = float(fr.get('indexPrice', px))
    fr_v = float(fr.get('lastFundingRate', 0)) * 100
    basis= round((mark - idx) / idx * 100, 4) if idx else 0
    stats= raw['stats']
    hi24 = float(stats.get('highPrice', px))
    lo24 = float(stats.get('lowPrice', px))
    chg  = float(stats.get('priceChangePercent', 0))
    vol24= float(stats.get('quoteVolume', 0)) / 1e9

    k1h = raw['k1h'] if isinstance(raw['k1h'], list) else []
    k4h = raw['k4h'] if isinstance(raw['k4h'], list) else []
    k1d = raw['k1d'] if isinstance(raw['k1d'], list) else []

    rsi_1h = _rsi([float(k[4]) for k in k1h[-15:]]) if len(k1h) >= 3 else 50
    rsi_4h = _rsi([float(k[4]) for k in k4h[-15:]]) if len(k4h) >= 3 else 50
    rsi_1d = _rsi([float(k[4]) for k in k1d[-5:]])  if len(k1d) >= 3 else 50
    atr4h  = _atr4h(k4h)
    atr1h  = _atr4h(k1h[-5:]) if len(k1h) >= 2 else 0

    # 近4根1H K线结构
    candles = []
    for k in (k1h[-4:] if k1h else []):
        o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        rng = h - l
        body = abs(c - o)
        candles.append({
            'dir': '↑' if c > o else '↓',
            'body_pct': round(body / rng * 100) if rng > 0 else 0,
            'shadow_up': round((h - max(o,c)) / rng * 100) if rng > 0 else 0,
            'shadow_dn': round((min(o,c) - l) / rng * 100) if rng > 0 else 0,
            'c': round(c, 2),
        })

    # FVG检测（4H Bull/Bear）
    fvg_bull = fvg_bear = None
    if len(k4h) >= 3:
        for i in range(len(k4h)-1, 1, -1):
            p2h = float(k4h[i-2][2]); cl = float(k4h[i][3])
            p2l = float(k4h[i-2][3]); ch = float(k4h[i][2])
            if cl > p2h and not fvg_bull:
                mid = (p2h + cl) / 2
                fvg_bull = {'lo': round(p2h,1), 'hi': round(cl,1),
                            'mid': round(mid,1), 'dist_pct': round((mid-px)/px*100,2)}
            if ch < p2l and not fvg_bear:
                mid = (ch + p2l) / 2
                fvg_bear = {'lo': round(ch,1), 'hi': round(p2l,1),
                            'mid': round(mid,1), 'dist_pct': round((mid-px)/px*100,2)}
            if fvg_bull and fvg_bear:
                break

    # OI趋势
    oi_h = raw['oi_h'] if isinstance(raw['oi_h'], list) else []
    oi_chg = []
    if len(oi_h) >= 2:
        for i in range(1, len(oi_h)):
            p = float(oi_h[i-1].get('sumOpenInterest', 0))
            c = float(oi_h[i].get('sumOpenInterest', 0))
            if p > 0: oi_chg.append(round((c-p)/p*100, 3))
    oi_cur = float(raw['oi'].get('openInterest', 0))
    oi_usd = round(oi_cur * px / 1e9, 2)
    oi_dir = ('SHORT_BUILD' if oi_chg and oi_chg[-1] > 0.3 else
              'LONG_UNWIND' if oi_chg and oi_chg[-1] < -0.3 else 'FLAT')
    oi_8h  = round(sum(oi_chg), 3) if oi_chg else 0

    # LSR三层
    lsr_g_list = raw['lsr_g'] if isinstance(raw['lsr_g'], list) else []
    lsr_t_list = raw['lsr_t'] if isinstance(raw['lsr_t'], list) else []
    lsr_p_list = raw['lsr_p'] if isinstance(raw['lsr_p'], list) else []
    lsr_g  = round(float(lsr_g_list[-1]['longShortRatio']), 3) if lsr_g_list else 1
    lsr_t  = round(float(lsr_t_list[-1]['longShortRatio']), 3) if lsr_t_list else 1
    lsr_p  = round(float(lsr_p_list[-1]['longShortRatio']), 3) if lsr_p_list else 1
    lsr_g_vals = [round(float(x['longShortRatio']),3) for x in lsr_g_list]
    lsr_g_trend = '↑' if len(lsr_g_vals)>1 and lsr_g_vals[-1]>lsr_g_vals[0] else '↓'

    # Taker
    tak_list = raw['taker'] if isinstance(raw['taker'], list) else []
    tak_vals = [round(float(x.get('buySellRatio',1)),3) for x in tak_list]
    tak_now  = tak_vals[-1] if tak_vals else 1
    tak_avg  = round(sum(tak_vals)/len(tak_vals),3) if tak_vals else 1
    tak_trend= '↑' if len(tak_vals)>1 and tak_vals[-1]>tak_vals[0] else '↓'

    # 盘口
    depth = raw['depth']
    bid_w = ask_w = 0
    if depth:
        bids = [(float(p), float(q)) for p, q in depth.get('bids', [])]
        asks = [(float(p), float(q)) for p, q in depth.get('asks', [])]
        bid_w = sum(p*q for p, q in bids)
        ask_w = sum(p*q for p, q in asks)
    ba = round(bid_w / ask_w, 2) if ask_w > 0 else 1

    # state补充（GEX/Hurst/清算）
    sd = {}
    sf = BASE / 'data' / f'brahma_state_{sym.lower()}.json'
    if sf.exists():
        try: sd = json.load(open(sf))
        except: pass

    return {
        'sym': sym, 'px': px, 'mark': mark, 'fr': fr_v, 'basis': basis,
        'hi24': hi24, 'lo24': lo24, 'chg': chg, 'vol24_b': round(vol24, 1),
        'rsi_1h': rsi_1h, 'rsi_4h': rsi_4h, 'rsi_1d': rsi_1d,
        'atr4h': atr4h, 'atr1h': round(atr1h, 1),
        'candles': candles,
        'fvg_bull': fvg_bull, 'fvg_bear': fvg_bear,
        'oi_dir': oi_dir, 'oi_chg': oi_chg, 'oi_8h': oi_8h, 'oi_usd_b': oi_usd,
        'lsr_g': lsr_g, 'lsr_g_trend': lsr_g_trend, 'lsr_g_vals': lsr_g_vals,
        'lsr_t': lsr_t, 'lsr_p': lsr_p,
        'tak': tak_now, 'tak_avg': tak_avg, 'tak_trend': tak_trend, 'tak_vals': tak_vals,
        'ba': ba,
        'regime':  sd.get('regime', 'CHOP_MID'),
        'hurst':   sd.get('hurst', 0),
        'zf':      sd.get('zero_flip', 0),
        'liq_up':  sd.get('liq_short', 0),
        'liq_dn':  sd.get('liq_long', 0),
        'liq_up2': sd.get('liq_short2', 0),
        'liq_dn2': sd.get('liq_long2', 0),
        'iv':      sd.get('iv_rank', 0),
        'bb_width':sd.get('bb_width', 2.0),
        'score':   sd.get('score_final', 0),
        # OB字段（梵天大脑核心SMC）
        'ob_list':  [o for o in sd.get('ob_list', []) if o.get('valid', True) and o.get('age', 999) < 50],
        'ob_1h_lo': sd.get('ob_1h_lo', 0),
        'ob_1h_hi': sd.get('ob_1h_hi', 0),
        'ob_4h_lo': sd.get('ob_4h_lo', 0),
        'ob_4h_hi': sd.get('ob_4h_hi', 0),
        # P0① 多周期FVG磁铁共振
        'fvg_15m_magnet': sd.get('fvg_15m_magnet', 0),
        'fvg_1h_magnet':  sd.get('fvg_1h_magnet', 0),
        'fvg_4h_magnet':  sd.get('fvg_4h_magnet', 0) or sd.get('fvg_magnet', 0),
        'fvg_1d_magnet':  sd.get('fvg_1d_magnet', 0),
        'fvg_consensus':  sd.get('fvg_consensus', 'NEUTRAL'),
        'fvg_votes':      sd.get('fvg_votes', {}),
        # P0② HAR-RV波动率边界
        'harv_hi': sd.get('harv_hi', 0),
        'harv_lo': sd.get('harv_lo', 0),
        # P0③ RSI 15分钟
        'rsi_15m': sd.get('rsi_15m', 50),
        # P0④ Step11系统裁决（避免双套逻辑）
        'step11_verdict': sd.get('step11_verdict', 'WAIT'),
        'step11_sl':      sd.get('sl', 0),
        'step11_tp1':     sd.get('tp1', 0),
        'step11_tp2':     sd.get('tp2', 0),
        'step11_rr':      sd.get('rr', 0),
        'ev_pct':         sd.get('ev_pct', 0),
        # P0⑤ BB宽度squeeze
        'bb_width':  sd.get('bb_width', 2.0),
        # P1 补充
        'cvd_1h':    sd.get('cvd_1h', 0),
        'cvd_4h':    sd.get('cvd_4h', 0),
        'cvd_dir_1h':sd.get('cvd_dir_1h', '?'),
        'gex_val':   sd.get('gex', 0),
        'gex_dir':   sd.get('gex_direction', 'POSITIVE'),
        'iv_rank':   sd.get('iv_rank', 0),
        'kappa':     sd.get('kappa', 0),
        'fear_greed':sd.get('fear_greed', 50),
        'lsr_big_pct':   sd.get('lsr_big', 50),
        'lsr_retail_pct':sd.get('lsr_retail', 50),
        'confluence_score': sd.get('confluence', {}).get('score', 0) if isinstance(sd.get('confluence'), dict) else 0,
        'oi_direction':  sd.get('oi_direction', 'FLAT'),
        'session':       sd.get('session', 'UNKNOWN'),
        'btc_eth_corr':  sd.get('btc_eth_corr', 0.85),
        'score':         sd.get('score_final', 0),
    }


# ── Step2: 方仓+主力手法 ──────────────────────────────────────────────────

def enrich(d: dict) -> dict:
    sym = d['sym']
    # 自动注入最新state（修复断裂点①：state过期问题）
    try:
        from brahma_state_refresh import _inject_gex_to_state, _inject_fangcang_to_state
        _inject_gex_to_state()
        _inject_fangcang_to_state()
    except Exception:
        pass
    try:
        import fangcang_engine as fe
        fe._CACHE_LOADED = False; fe._FANGCANG_CACHE = []
        from fangcang_engine import fangcang_context_match as fcm
        bbw = float(d.get('bb_width', 2.0))
        sig = 'LONG' if d['rsi_1h'] < 50 else 'SHORT'
        fc = fcm(sym, bbw, d['rsi_1h'], d['regime'], sig)
        d['fc_n'] = fc.get('n_similar', 0)
        d['fc_long'] = fc.get('long_pct', 0)
        d['fc_short'] = fc.get('short_pct', 0)
        d['fc_burst'] = fc.get('avg_burst_atr_mult', 0)
    except Exception:
        d['fc_n'] = 0; d['fc_long'] = 0.5; d['fc_short'] = 0.5; d['fc_burst'] = 0

    try:
        from brahma_smart_money_patterns import detect_patterns
        patterns = detect_patterns(sym, dict(d, liq_long=d['liq_dn'], zero_flip=d['zf']))
        d['patterns'] = [(p, c, e) for p, c, e in patterns]
    except Exception:
        d['patterns'] = []

    return d


# ── Step3: 40年实战推理引擎 ──────────────────────────────────────────────

def reason_40yr(d: dict) -> dict:
    """
    40年实战经验推理链。
    每个结论必须有明确的数据依据，不允许笼统描述。
    """
    sym = d['sym']; px = d['px']
    atr4h = d['atr4h']; atr1h = d['atr1h']
    rsi = d['rsi_1h']; rsi_4h = d['rsi_4h']
    lsr_g = d['lsr_g']; lsr_p = d['lsr_p']
    tak = d['tak']; tak_avg = d['tak_avg']
    oi_dir = d['oi_dir']; oi_8h = d['oi_8h']
    ba = d['ba']
    zf = d['zf']; liq_up = d['liq_up']; liq_dn = d['liq_dn']
    fvg_bull = d.get('fvg_bull')
    fc_long = d['fc_long']; fc_short = d['fc_short']; fc_n = d['fc_n']
    patterns = d['patterns']
    candles = d['candles']
    regime = d['regime']; hurst = d['hurst']

    chain = []   # 推理链，每条含[信号,结论,权重]
    bias = 0     # 正=偏多 负=偏空

    # ① K线结构读取
    if candles:
        last = candles[-1]
        if last['dir'] == '↓' and last['body_pct'] > 50:
            chain.append(('K线', f"最后一根↓实体{last['body_pct']}% = 卖盘有力", -1))
            bias -= 1
        elif last['dir'] == '↑' and last['body_pct'] > 50:
            chain.append(('K线', f"最后一根↑实体{last['body_pct']}% = 买盘有力", +1))
            bias += 1
        else:
            chain.append(('K线', f"最后一根{last['dir']}实体{last['body_pct']}% = 方向不明", 0))

    # ② Taker主力意图
    tak_str = f"当前{tak}(6H均{tak_avg})"
    if tak >= 1.5 and tak_avg < 1.0:
        chain.append(('Taker', f"{tak_str} = 脉冲式追涨，散户行为，主力可能在出货", -1))
        bias -= 1
    elif tak < 0.6:
        chain.append(('Taker', f"{tak_str} = 持续主动卖，空头主导", -2))
        bias -= 2
    elif tak >= 1.0 and tak_avg >= 1.0:
        chain.append(('Taker', f"{tak_str} = 主动买持续，多头主导", +2))
        bias += 2
    else:
        chain.append(('Taker', f"{tak_str} = 中性，无明确方向", 0))

    # ③ 盘口深度
    if ba > 3.0:
        chain.append(('盘口', f"买/卖={ba}x = 大量承接盘，有人在吃货", +2))
        bias += 2
    elif ba < 0.4:
        chain.append(('盘口', f"买/卖={ba}x = 卖盘压倒性，抛压极重", -2))
        bias -= 2
    else:
        chain.append(('盘口', f"买/卖={ba}x = 均衡", 0))

    # ④ LSR散户多空
    if lsr_g > 2.5:
        chain.append(('LSR散户', f"={lsr_g}(>2.5⚡) = 猎杀结构完整，清算池近", -2))
        bias -= 2
    elif lsr_g > 1.8:
        chain.append(('LSR散户', f"={lsr_g} = 偏多但未到猎杀阈值", -1))
        bias -= 1
    # 大户仓位 vs 散户账户分离
    if lsr_p > 1.5 and lsr_g > 2.0:
        chain.append(('LSR背离', f"大户仓位{lsr_p}<散户{lsr_g} = 大户做多但散户更多，主力可能在等清算", -1))
        bias -= 1
    elif lsr_p > 1.5:
        chain.append(('LSR大户', f"大户仓位={lsr_p} = 大户真实偏多，不宜做空", +1))
        bias += 1

    # ④.5 CVD真实方向（Taker序列累积计算，区分散户买入vs主力建仓）
    cvd_1h = d.get('cvd_1h', 0)
    tak_vals_raw = d.get('tak_vals', [])
    # 真实CVD：用Taker序列累积计算方向（>1=净买入 <1=净卖出）
    if tak_vals_raw and len(tak_vals_raw) >= 3:
        net_buy = sum(1 for t in tak_vals_raw if t > 1.05)
        net_sell = sum(1 for t in tak_vals_raw if t < 0.95)
        cvd_real_dir = 'BUY' if net_buy > net_sell else ('SELL' if net_sell > net_buy else 'NEUTRAL')
        cvd_momentum = round(tak_vals_raw[-1] - tak_vals_raw[0], 3)
        if cvd_real_dir == 'BUY' and cvd_momentum > 0:
            chain.append(('CVD', f"Taker累积净买({net_buy}/{len(tak_vals_raw)}) 动量{cvd_momentum:+.3f} = 主力持续吸筹", +2))
            bias += 2
        elif cvd_real_dir == 'BUY' and cvd_momentum < -0.3:
            chain.append(('CVD', f"Taker累积净买但动量衰减{cvd_momentum:+.3f} = 买盘减弱，警惕反转", 0))
        elif cvd_real_dir == 'SELL':
            chain.append(('CVD', f"Taker累积净卖({net_sell}/{len(tak_vals_raw)}) = 主力级别出货", -2))
            bias -= 2
        else:
            chain.append(('CVD', f"CVD中性 买{net_buy}/卖{net_sell}/中{len(tak_vals_raw)-net_buy-net_sell}", 0))
    elif cvd_1h > 50:
        chain.append(('CVD', f"state CVD_1H={cvd_1h:+.0f} = 净买入", +1)); bias += 1
    elif cvd_1h < -50:
        chain.append(('CVD', f"state CVD_1H={cvd_1h:+.0f} = 净卖出", -1)); bias -= 1
    else:
        chain.append(('CVD', f"CVD中性，无明确主力方向", 0))

    # ⑤ OI资金流
    if oi_dir == 'SHORT_BUILD' and tak < 0.8:
        chain.append(('OI', f"SHORT_BUILD+Taker卖 = 主力主动建空仓，方向向下", -2))
        bias -= 2
    elif oi_dir == 'LONG_UNWIND' and tak > 1.2:
        chain.append(('OI', f"LONG_UNWIND+Taker买 = 逼空行情，空头被迫平仓", +1))
        bias += 1
    elif oi_dir == 'FLAT':
        chain.append(('OI', f"FLAT OI8H={oi_8h:+.2f}% = 无明确建仓方向", 0))

    # ④.8 Session时段（流动性风险）
    import time as _t
    utc_hour = _t.gmtime().tm_hour
    if 0 <= utc_hour < 4:
        session_label = 'ASIA_LATE'
        session_risk  = '亚洲盘尾盘，流动性低，插针风险高'
        chain.append(('Session', f"UTC{utc_hour:02d}:00 {session_label} = {session_risk}", -1))
        bias -= 1
    elif 6 <= utc_hour < 10:
        session_label = 'EU_OPEN'
        session_risk  = '欧洲盘开盘，流动性切换，大波动高发'
        chain.append(('Session', f"UTC{utc_hour:02d}:00 {session_label}⚡ = {session_risk}", -1))
        bias -= 1
    elif 13 <= utc_hour < 17:
        session_label = 'US_OPEN'
        session_risk  = '美国盘开盘，最强流动性，趋势确认时段'
        chain.append(('Session', f"UTC{utc_hour:02d}:00 {session_label}✅ = {session_risk}", +1))
        bias += 1
    elif 21 <= utc_hour < 24:
        session_label = 'US_CLOSE'
        session_risk  = '美国盘收盘，持仓调整，方向确认'
        chain.append(('Session', f"UTC{utc_hour:02d}:00 {session_label} = {session_risk}", 0))
    else:
        session_label = 'TRANSITION'
        chain.append(('Session', f"UTC{utc_hour:02d}:00 过渡时段，流动性一般", 0))

    # ⑤.1 RSI_15M 短线时机（P0③）
    rsi_15m = d.get('rsi_15m', 50)
    rsi_4h  = d.get('rsi_4h', 50)
    if rsi_15m <= 30:
        chain.append(('RSI15M', f"={rsi_15m}(超卖⚡) + RSI_1H={rsi} = 短线洗盘底部，15M企稳信号", +2))
        bias += 2
    elif rsi_15m <= 40:
        chain.append(('RSI15M', f"={rsi_15m}(偏低) + RSI_1H={rsi} = 短线回调中，等15M反弹>50确认", +1))
        bias += 1
    elif rsi_15m >= 75:
        chain.append(('RSI15M', f"={rsi_15m}(超买⚡) = 短线过热，不追多", -1))
        bias -= 1
    else:
        chain.append(('RSI15M', f"={rsi_15m}  RSI多周期: 15M={rsi_15m}/1H={rsi}/4H={rsi_4h}", 0))

    # ⑤.15 RSI背离检测（底部/顶部反转先行信号）
    candles = d.get('candles', [])
    if len(candles) >= 3:
        # 用近4根K线检测价格与RSI的背离
        prices = [c['c'] for c in candles]
        rsi_now = d.get('rsi_1h', 50)
        rsi_prev = d.get('rsi_4h', 50)  # 用4H RSI近似前期RSI
        price_falling = prices[-1] < prices[0]
        price_rising  = prices[-1] > prices[0]
        # 看涨背离：价格新低但RSI未新低（>30）
        if price_falling and rsi_now > 28 and rsi_now < 45:
            chain.append(('RSI背离', f"价格↓新低但RSI={rsi_now}未破超卖 = 看涨背离⚡ 底部反转信号", +2))
            bias += 2
        # 看跌背离：价格新高但RSI未新高（<75）
        elif price_rising and rsi_now < 72 and rsi_now > 55:
            chain.append(('RSI背离', f"价格↑新高但RSI={rsi_now}未破超买 = 看跌背离⚡ 顶部反转信号", -1))
            bias -= 1

    # ⑤.2 HAR-RV波动率边界（P0②）
    harv_hi = d.get('harv_hi', 0); harv_lo = d.get('harv_lo', 0)
    if harv_lo > 0 and harv_hi > 0:
        harv_dist_lo = round((px - harv_lo) / px * 100, 2)
        harv_dist_hi = round((harv_hi - px) / px * 100, 2)
        if abs(harv_dist_lo) < 0.3:
            chain.append(('HAR-RV', f"价格${px:,.0f}≈HAR-RV下边界${harv_lo:,.0f}({harv_dist_lo:+.2f}%) = 波动率模型支撑测试，关键位", +1))
            bias += 1
        elif px < harv_lo:
            chain.append(('HAR-RV', f"跌破HAR-RV下界${harv_lo:,.0f}(-{abs(harv_dist_lo):.2f}%) = 波动率模型看空，下一支撑OB/GEX", -1))
            bias -= 1
        else:
            chain.append(('HAR-RV', f"区间内 下界${harv_lo:,.0f}({harv_dist_lo:+.2f}%) 上界${harv_hi:,.0f}({harv_dist_hi:+.2f}%)", 0))

    # ⑤.3 BB squeeze预警（P0⑤）
    bb_width = d.get('bb_width', 2.0)
    if bb_width < 1.2:
        chain.append(('BB-squeeze', f"bb_width={bb_width:.3f}(极窄⚡<1.2) = 即将方向性突破，OI+Taker决定方向", -1 if tak < 0.8 else +1))
        bias += (-1 if tak < 0.8 else +1)
    elif bb_width < 1.5:
        chain.append(('BB宽度', f"bb_width={bb_width:.3f}(收窄) = 震荡收敛中，突破在即", 0))

    # ⑤.4 EV期望值（P1）
    ev_pct = d.get('ev_pct', 0)
    if ev_pct > 0:
        if ev_pct >= 0.5:
            chain.append(('EV', f"={ev_pct:.3f}%(≥0.5% ✅) = 数学期望值充足，值得入场", +1))
            bias += 1
        elif ev_pct >= 0.3:
            chain.append(('EV', f"={ev_pct:.3f}%(0.3~0.5%) = 期望值偏低，边缘", 0))
        else:
            chain.append(('EV', f"={ev_pct:.3f}%(<0.3% ⚠️) = 期望值不足，数学上WAIT依据", -1))
            bias -= 1

    # ⑥ OB有效性（梵天铁律：age<50且valid才算有效OB）
    ob_list = d.get('ob_list', [])
    ob_1h_lo = d.get('ob_1h_lo', 0); ob_1h_hi = d.get('ob_1h_hi', 0)
    ob_4h_lo = d.get('ob_4h_lo', 0); ob_4h_hi = d.get('ob_4h_hi', 0)
    if ob_1h_lo > 0:
        in_ob_1h = ob_1h_lo <= px <= ob_1h_hi
        ob_age_1h = next((o['age'] for o in ob_list if o.get('tf')=='1H' and o.get('side')=='BULL'), 999)
        ob_breached = px < ob_1h_lo * 0.998  # 跌破OB下沿0.2%确认穿越
        if in_ob_1h:
            chain.append(('OB1H', f"价格在1H Bull OB内[${ob_1h_lo:,.0f}~${ob_1h_hi:,.0f}] age={ob_age_1h}bar = 支撑区有效，偏多", +2))
            bias += 2
        elif ob_breached:
            chain.append(('OB1H', f"⚡OB1H被穿越！跌破${ob_1h_lo:,.0f} = 多头支撑失守，1H OB作废，下一支撑OB4H/GEX ZF", -2))
            bias -= 2
        elif px < ob_1h_lo:
            chain.append(('OB1H', f"价格在1H OB下方${ob_1h_lo:,.0f} = OB未触达，等回踩", 0))
        else:
            chain.append(('OB1H', f"价格突破1H OB上沿${ob_1h_hi:,.0f} = OB被消耗，失效", -1))
            bias -= 1
    if ob_4h_lo > 0 and ob_4h_lo != ob_1h_lo:
        in_ob_4h = ob_4h_lo <= px <= ob_4h_hi
        ob_age_4h = next((o['age'] for o in ob_list if o.get('tf')=='4H'), 999)
        if in_ob_4h:
            chain.append(('OB4H', f"价格在4H Bull OB内[${ob_4h_lo:,.0f}~${ob_4h_hi:,.0f}] age={ob_age_4h}bar = 强支撑，主力成本区", +2))
            bias += 2

    # ⑥.5 IV排名推理（波动率环境判断）
    iv_rank = d.get('iv_rank', 0)
    gex_val = d.get('gex_val', 0)
    kappa = d.get('kappa', 0)
    if iv_rank > 0:
        if iv_rank < 10:
            chain.append(('IV排名', f"={iv_rank:.1f}(极低⚡<10) = 历史低波动末期，大波动临近；期权便宜=方向突破时赔率高", -1))
            bias -= 1  # 低IV通常在下跌前
        elif iv_rank > 80:
            chain.append(('IV排名', f"={iv_rank:.1f}(极高>80) = 恐慌高位，往往是底部（VIX顶=市场底）", +1))
            bias += 1
        else:
            chain.append(('IV排名', f"={iv_rank:.1f}(正常区间)  GEX量级=${gex_val/1e6:.1f}M  kappa={kappa:.4f}", 0))

    # ⑦ GEX ZeroFlip
    if zf > 0:
        zf_dist = (px - zf) / px * 100
        if abs(zf_dist) < 0.3:
            chain.append(('GEX', f"ZF=${zf:,.0f} 距今{zf_dist:+.2f}% = 做市商对冲敏感区，插针高概率", -1))
            bias -= 1
        elif zf_dist > 0:
            chain.append(('GEX', f"ZF=${zf:,.0f} 站上ZF({zf_dist:+.2f}%) = 做市商Call对冲，上方阻力减小", +1))
            bias += 1
        else:
            chain.append(('GEX', f"ZF=${zf:,.0f} 在ZF下方({zf_dist:+.2f}%) = 空头对冲压力", -1))
            bias -= 1

    # ⑦ 清算地图
    if liq_dn > 0 and px > 0:
        dn_pct = (px - liq_dn) / px * 100
        up_pct = (liq_up - px) / px * 100 if liq_up > 0 else 0
        if dn_pct < 2.0 and lsr_g > 2.0:
            chain.append(('清算', f"多单止损池${liq_dn:,.0f}(-{dn_pct:.1f}%) 近+散户偏多 = P2猎杀路线明确", -2))
            bias -= 2
        elif up_pct < 1.5:
            chain.append(('清算', f"空单止损墙${liq_up:,.0f}(+{up_pct:.1f}%) 近 = 主力可能先拉上去扫空头", +1))
            bias += 1

    # ⑧ 方仓历史
    if fc_n >= 10:
        if fc_long >= 0.75:
            chain.append(('方仓', f"LONG={fc_long:.0%} n={fc_n} = 历史高置信多头", +2))
            bias += 2
        elif fc_short >= 0.75:
            chain.append(('方仓', f"SHORT={fc_short:.0%} n={fc_n} = 历史高置信空头", -2))
            bias -= 2
        else:
            chain.append(('方仓', f"LONG={fc_long:.0%}/SHORT={fc_short:.0%} n={fc_n} = 历史中性", 0))

    # ⑨ 主力手法
    if patterns:
        pid, conf, ev = patterns[0]
        from brahma_smart_money_patterns import SMART_MONEY_PATTERNS
        p = SMART_MONEY_PATTERNS.get(pid, {})
        chain.append(('主力手法', f"{p.get('name','?')}({conf:.0%}): {p.get('implication','')}", 0))

    # ⑩ P0④ Step11裁决优先（避免双套逻辑冲突）
    step11_verdict  = d.get('step11_verdict', '')
    step11_sl       = d.get('step11_sl', 0)
    step11_tp1      = d.get('step11_tp1', 0)
    step11_tp2      = d.get('step11_tp2', 0)
    step11_rr_val   = d.get('step11_rr', 0)

    # SL计算：优先用step11精确SL，备用ATR铁律
    sl_min = atr4h * 1.5
    if step11_sl > 0:
        sl_long  = round(step11_sl, 1)
        sl_short = round(px + (px - step11_sl), 1)  # 对称SL
        chain.append(('Step11-SL', f"系统精确SL=${step11_sl:,.1f}(含11道闸门验证) RR={step11_rr_val:.2f}", 0))
    else:
        sl_long  = round(px - sl_min, 1)
        sl_short = round(px + sl_min, 1)

    # GEX插针风险：SL必须在ZF下方
    if zf > 0 and sl_long > zf * 0.999:
        old_sl = sl_long
        sl_long = round(zf - atr4h * 0.5, 1)
        chain.append(('SL修正', f"SL${old_sl:,.1f}在ZF${zf:,.0f}上方 = 必被插针扫损，修正为${sl_long:,.1f}(ZF下方)", 0))

    # 最终方向判断：bias≥3或step11一致
    if bias >= 3:
        direction = 'LONG'
        confidence = 'HIGH' if bias >= 5 else 'MED'
    elif bias <= -3:
        direction = 'SHORT'
        confidence = 'HIGH' if bias <= -5 else 'MED'
    else:
        direction = 'WAIT'
        confidence = 'LOW'

    # Step11裁决校验：若Step11=WAIT但bias≥3，保守降级
    if step11_verdict == 'WAIT' and direction != 'WAIT':
        chain.append(('Step11', f"verdict=WAIT（11道闸门拦截）vs 推理bias={bias:+d}(偏{direction}) = 系统裁决优先，降级WAIT", 0))
        direction = 'WAIT'
        confidence = 'MED'
    elif step11_verdict in ('ENTER','WATCH') and direction == 'WAIT':
        chain.append(('Step11', f"verdict={step11_verdict}（系统放行）vs 推理bias={bias:+d} = 参考Step11，提升为WATCH", 0))
        direction = 'WATCH'

    # RR验证（优先用step11_rr）
    rr = step11_rr_val if step11_rr_val > 0 else 0
    if rr == 0:
        if direction == 'LONG' and liq_up > 0:
            rr = round((liq_up - px) / (px - sl_long), 2) if (px - sl_long) > 0 else 0
        elif direction == 'SHORT' and liq_dn > 0:
            rr = round((px - liq_dn) / (sl_short - px), 2) if (sl_short - px) > 0 else 0

    if rr > 0 and rr < 1.2 and direction not in ('WAIT','WATCH'):
        chain.append(('RR', f"RR={rr:.2f}<1.2 = 风险回报不达标，降级WAIT", 0))
        direction = 'WAIT'

    return {
        'chain': chain, 'bias': bias,
        'direction': direction, 'confidence': confidence,
        'sl_long': sl_long, 'sl_short': sl_short,
        'sl_min': sl_min, 'rr': rr,
        'step11_sl': step11_sl, 'step11_tp1': step11_tp1,
        'step11_tp2': step11_tp2, 'step11_verdict': step11_verdict,
    }


# ── Step4: 格式化输出 ─────────────────────────────────────────────────────

def format_full(d: dict, reasoning: dict, council: dict) -> str:
    sym = d['sym']; px = d['px']
    atr4h = d['atr4h']
    zf = d['zf']; liq_up = d['liq_up']; liq_dn = d['liq_dn']
    sl_long = reasoning['sl_long']; sl_short = reasoning['sl_short']
    direction = reasoning['direction']
    chain = reasoning['chain']
    fvg_bull = d.get('fvg_bull')
    candles = d['candles']
    now_str = time.strftime('%H:%M UTC')

    zf_pct = round((px-zf)/px*100,2) if zf else 0
    up_pct = round((liq_up-px)/px*100,2) if liq_up else 0
    dn_pct = round((liq_dn-px)/px*100,2) if liq_dn else 0
    fvg_str = f"${fvg_bull['mid']:,.1f}({fvg_bull['dist_pct']:+.2f}%)" if fvg_bull else "无"

    lines = [
        f"{'─'*60}",
        f" {sym}/USDT  ${px:,.2f}  {now_str}",
        f"{'─'*60}",
        f"",
        f"【全维度数据】",
        f" 体制={d['regime']}  score={d.get('score',0):.0f}  Hurst={d['hurst']:.3f}",
        f" RSI: 1H={d['rsi_1h']}  4H={d['rsi_4h']}  1D={d['rsi_1d']}",
        f" ATR: 1H=${d['atr1h']:.1f}  4H=${atr4h:.1f}  SL最小=${reasoning['sl_min']:.1f}",
        f" FR={d['fr']:+.4f}%  基差={d['basis']:+.4f}%  OI=${d['oi_usd_b']:.2f}B",
        f" 24H: H=${d['hi24']:,.2f}  L=${d['lo24']:,.2f}  振幅={(d['hi24']-d['lo24'])/d['lo24']*100:.2f}%  Vol=${d['vol24_b']:.1f}B",
        f"",
        f"【OI资金流】",
        f" 方向={d['oi_dir']}  8H累积={d['oi_8h']:+.3f}%  逐H={d['oi_chg']}",
        f"",
        f"【LSR多空结构】",
        f" 全局散户={d['lsr_g']}({d['lsr_g_trend']}) 6H趋势={d['lsr_g_vals']}",
        f" 大户账户={d['lsr_t']}  大户仓位={d['lsr_p']}",
        f" {'⚡P2猎杀结构(>2.5)' if d['lsr_g']>2.5 else '正常区间'}",
        f"",
        f"【Taker主动资金】",
        f" 当前={d['tak']}({d['tak_trend']})  6周期均={d['tak_avg']}  序列={d['tak_vals']}",
        f" {'主动买主导✅' if d['tak']>=1 else '主动卖主导⚡'}",
        f"",
        f"【盘口深度】",
        f" 买/卖={d['ba']}x  {'买盘厚✅' if d['ba']>1.5 else '卖盘厚⚡' if d['ba']<0.7 else '均衡'}",
        f"",
        f"【GEX+FVG+OB+清算】",
        f" GEX ZF=${zf:,.0f}({'站上ZF' if zf_pct>0 else 'ZF下方'} {zf_pct:+.2f}%)  GEX量级=${d.get('gex_val',0)/1e6:.1f}M {d.get('gex_dir','?')}  IV排名={d.get('iv_rank',0):.1f}",
        f" FVG磁铁共振: 15M=${d.get('fvg_15m_magnet',0):,.0f} 1H=${d.get('fvg_1h_magnet',0):,.0f} 4H=${d.get('fvg_4h_magnet',0):,.0f} 1D=${d.get('fvg_1d_magnet',0):,.0f}",
        f" FVG共识={d.get('fvg_consensus','?')}  目标价带=${min(v for v in [d.get('fvg_1h_magnet',0),d.get('fvg_4h_magnet',0),d.get('fvg_1d_magnet',0)] if v>0):,.0f}~${max(v for v in [d.get('fvg_1h_magnet',0),d.get('fvg_4h_magnet',0),d.get('fvg_1d_magnet',0)] if v>0):,.0f}" if any(v>0 for v in [d.get('fvg_1h_magnet',0),d.get('fvg_4h_magnet',0),d.get('fvg_1d_magnet',0)]) else f" FVG共识={d.get('fvg_consensus','?')}",
        f" HAR-RV: 支撑=${d.get('harv_lo',0):,.1f}  阻力=${d.get('harv_hi',0):,.1f}  BB宽度={d.get('bb_width',0):.3f}{'⚡squeeze' if d.get('bb_width',2)<1.2 else ''}",
    ]
    # OB显示
    ob_list = d.get('ob_list', [])
    ob_1h_lo = d.get('ob_1h_lo',0); ob_1h_hi = d.get('ob_1h_hi',0)
    ob_4h_lo = d.get('ob_4h_lo',0); ob_4h_hi = d.get('ob_4h_hi',0)
    if ob_1h_lo > 0:
        in_ob = '📍在OB内' if ob_1h_lo <= d['px'] <= ob_1h_hi else ('↑OB上方' if d['px'] > ob_1h_hi else '↓OB下方等回踩')
        age_1h = next((o['age'] for o in ob_list if o.get('tf')=='1H' and o.get('side')=='BULL'), '?')
        lines.append(f" OB 1H Bull [${ob_1h_lo:,.0f}~${ob_1h_hi:,.0f}] age={age_1h}bar {in_ob}")
    if ob_4h_lo > 0 and ob_4h_lo != ob_1h_lo:
        in_ob4 = '📍在OB内' if ob_4h_lo <= d['px'] <= ob_4h_hi else ('↑OB上方' if d['px'] > ob_4h_hi else '↓等回踩')
        age_4h = next((o['age'] for o in ob_list if o.get('tf')=='4H'), '?')
        lines.append(f" OB 4H Bull [${ob_4h_lo:,.0f}~${ob_4h_hi:,.0f}] age={age_4h}bar {in_ob4}")
    # 清算地图（二级清算池也显示）
    liq_up2 = d.get('liq_up2',0); liq_dn2 = d.get('liq_dn2',0)
    lines += [
        f" 清算↑${liq_up:,.0f}({up_pct:+.2f}%)  ↓${liq_dn:,.0f}({dn_pct:+.2f}%)",
        f"",
        f"【近4根1H K线】",
    ]
    candle_str = ' > '.join([str(c['dir'])+str(c['body_pct'])+'%' for c in candles])
    lines.append(f" {candle_str}")
    lines += [
        f"",
        f"【方仓历史（40年）】",
        f" n={d['fc_n']}  LONG={d['fc_long']:.0%}  SHORT={d['fc_short']:.0%}  burst={d['fc_burst']:.2f}x",
        f"",
        f"【主力手法识别】",
    ]

    if d['patterns']:
        for pid, conf, ev in d['patterns']:
            lines.append(f" {pid}({conf:.0%}): {ev}")
    else:
        lines.append(" 无明确手法（正常震荡区间）")

    lines += [
        f"",
        f"【40年推理链】",
    ]
    for src, txt, w in chain:
        icon = '▲' if w > 0 else ('▼' if w < 0 else '►')
        lines.append(f" {icon}[{src}] {txt}")

    lines += [
        f"",
        f"【council三方投票 {d['sym']}】",
        f" 宏观={council['votes'].get('macro','?')}  结构={council['votes'].get('structure','?')}  量化={council['votes'].get('quant','?')}",
        f" 裁决={council['verdict']} 置信={council['confidence']}",
        f"",
        f"【Step11系统裁决】",
        f" verdict={reasoning.get('step11_verdict') or 'N/A'}  SL=${reasoning.get('step11_sl',0):,.1f}  TP1=${reasoning.get('step11_tp1',0):,.1f}  TP2=${reasoning.get('step11_tp2',0):,.1f}",
        f" EV={d.get('ev_pct',0):.3f}%  RR={reasoning['rr']:.2f}  恐贪={d.get('fear_greed',50)}({'贪婪' if d.get('fear_greed',50)>75 else '恐惧' if d.get('fear_greed',50)<25 else '中性'})",
        f" Session={d.get('session','?')}  BTC-ETH相关={d.get('btc_eth_corr',0.85):.2f}",
        f"",
        f"【综合判断】",
        f" 梵天方向={direction}  偏向分={reasoning['bias']:+d}  置信={reasoning.get('confidence','?')}  Confluence={d.get('confluence_score',0):.0f}",
        f" SL(多)≥${sl_long:,.1f}  SL(空)≤${sl_short:,.1f}  (ATR4H×1.5=${reasoning['sl_min']:,.0f})",
        f" RSI多周期: 15M={d.get('rsi_15m',50)} / 1H={d['rsi_1h']} / 4H={d['rsi_4h']} / 1D={d['rsi_1d']}",
    ]

    return '\n'.join(lines)


def format_vip(d: dict, reasoning: dict) -> str:
    """VIP策略卡片（姓赵不宣格式）"""
    sym = d['sym']; px = d['px']
    direction = reasoning['direction']
    sl_long = reasoning['sl_long']; sl_short = reasoning['sl_short']
    liq_up = d['liq_up']; liq_dn = d['liq_dn']
    zf = d['zf']; atr4h = d['atr4h']
    rr = reasoning['rr']
    # Step11精确TP（优先）
    s11_tp1 = reasoning.get('step11_tp1', 0)
    s11_tp2 = reasoning.get('step11_tp2', 0)
    ob_1h_lo = d.get('ob_1h_lo', 0)
    fvg_1h_m = d.get('fvg_1h_magnet', 0)

    if direction == 'LONG':
        # TP: step11 > 清算 > FVG > ATR估算
        tp1 = s11_tp1 if s11_tp1 > px * 1.005 else (round(liq_up * 0.998, 1) if liq_up > px * 1.005 else round(px + atr4h * 2, 1))
        tp2 = s11_tp2 if s11_tp2 > tp1 * 1.001 else (round(fvg_1h_m, 1) if fvg_1h_m > tp1 else round(px + atr4h * 3, 1))
        # 入场：OB回踩精确位
        if ob_1h_lo > 0 and px > ob_1h_lo * 1.001:
            entry_str = f"等回踩OB ${ob_1h_lo:,.1f}~${ob_1h_lo + atr4h*0.3:,.1f}"
        else:
            entry_str = f"${px*0.997:,.1f}~${px*0.999:,.1f}"
        rr_str = f"{rr:.2f}" if rr > 0 else "计算中"
        long_line  = f"🟢 多单｜{entry_str}"
        long_sl    = f"止损${sl_long:,.1f}｜目标${tp1:,.1f}→${tp2:,.1f}"
        short_line = "🔴 暂无空单｜等待结构"
        warn = f"{'LSR>2.5猎杀结构，等清算池' if d['lsr_g']>2.5 else f'方向={direction} RR={rr_str} EV={d.get(chr(101)+chr(118)+chr(95)+chr(112)+chr(99)+chr(116),0):.3f}%'}"
        bust = f"跌破${sl_long:,.1f}+OI减少作废"
    elif direction == 'SHORT':
        tp1 = s11_tp1 if 0 < s11_tp1 < px * 0.995 else (round(liq_dn * 1.002, 1) if 0 < liq_dn < px * 0.995 else round(px - atr4h * 2, 1))
        tp2 = s11_tp2 if 0 < s11_tp2 < tp1 * 0.999 else (round(liq_dn * 0.995, 1) if 0 < liq_dn < px * 0.995 else round(px - atr4h * 3, 1))
        # 空单入场：GEX ZF阻力区
        if zf > px * 0.998:
            entry_str = f"等ZF阻力区 ${px*1.001:,.1f}~${zf*0.999:,.1f}"
        else:
            entry_str = f"${px*1.001:,.1f}~${px*1.003:,.1f}"
        rr_str = f"{rr:.2f}" if rr > 0 else "计算中"
        long_line  = "🟢 暂无多单｜等待结构"
        short_line = f"🔴 空单｜{entry_str}"
        long_sl    = f"止损${sl_short:,.1f}｜目标${tp1:,.1f}→${tp2:,.1f}"
        warn = f"{'Taker<0.6持续卖压' if d['tak']<0.6 else f'方向={direction} RR={rr_str}'}"
        bust = f"突破${sl_short:,.1f}+OI增加作废"
    else:
        long_line  = "🟢 暂无多单｜等待结构"
        short_line = "🔴 暂无空单｜等待结构"
        long_sl    = ""
        warn = "双侧均在等待，无入场信号"
        bust = f"GEX ZF=${zf:,.0f}为关键位"

    lines = [
        f"🌿 姓赵不宣 | {sym} 当前布局",
        f"——— {sym} ———",
        long_line,
    ]
    if long_sl:
        lines.append(long_sl)
    lines += [
        f"  杠杆3x｜仓位1.5%",
        short_line,
        f"⚠️ {warn}",
        f"🚫 {bust}",
        f"🌿 姓赵不宣 | 不是建议",
    ]
    return '\n'.join(lines)


# ── 主入口 ────────────────────────────────────────────────────────────────

def run_deep_analysis(symbols: list = None) -> str:
    if symbols is None:
        symbols = ['BTC', 'ETH']

    t_total = time.time()

    # 并发拉取+enrichment
    with ThreadPoolExecutor(max_workers=len(symbols)) as ex:
        raw_futs = {ex.submit(fetch_all, sym): sym for sym in symbols}
        raw_data = {}
        for fut in as_completed(raw_futs):
            sym = raw_futs[fut]
            d = fut.result()
            raw_data[sym] = enrich(d)

    # council三方并发
    from free_llm_client import council_three_way
    with ThreadPoolExecutor(max_workers=len(symbols)) as ex:
        cv_futs = {ex.submit(council_three_way,
            sym, raw_data[sym]['px'], raw_data[sym]['regime'], 40,
            'BULL' if raw_data[sym]['rsi_1h']>55 else 'BEAR',
            raw_data[sym]['fvg_bull']['mid'] if raw_data[sym]['fvg_bull'] else raw_data[sym]['px'],
            raw_data[sym]['oi_dir'], 'NEUTRAL',
            raw_data[sym]['lsr_p']*50, raw_data[sym]['hurst'], 0, raw_data[sym]['px'],
            raw_data[sym]['px']*0.997, raw_data[sym]['px']*1.003,
            raw_data[sym]['liq_up'], raw_data[sym]['liq_dn'], 'neutral', 50
        ): sym for sym in symbols}
        councils = {}
        for fut in as_completed(cv_futs):
            sym = cv_futs[fut]
            councils[sym] = fut.result()

    # 40年推理
    reasonings = {sym: reason_40yr(raw_data[sym]) for sym in symbols}

    # 组装输出
    output = [f"\n{'='*60}",
              f"🏛️ 梵天2.0 强制深度分析",
              f"{'='*60}"]

    for sym in symbols:
        output.append(format_full(raw_data[sym], reasonings[sym], councils[sym]))
        output.append("")
        output.append(format_vip(raw_data[sym], reasonings[sym]))
        output.append("")

    # 双标的一致性（铁律⑤）
    if len(symbols) == 2:
        d1 = reasonings[symbols[0]]['direction']
        d2 = reasonings[symbols[1]]['direction']
        output += [
            f"{'─'*60}",
            f"【铁律⑤双标的方向一致性】",
            f" {symbols[0]}={d1}  {symbols[1]}={d2}",
            f" {'✅ 方向一致' if d1==d2 or 'WAIT' in (d1,d2) else '⚠️ 方向冲突→以方仓置信度高者为准'}",
        ]

    elapsed = round(time.time()-t_total, 2)
    output.append(f"\n总耗时={elapsed}s")
    result = '\n'.join(output)

    # 保存快照（神经感知用）
    try:
        snap = {
            'ts': time.time(), 'elapsed': elapsed,
            'output': result,
            'symbols': {sym: {
                'px': raw_data[sym]['px'],
                'direction': reasonings[sym]['direction'],
                'sl_long': reasonings[sym]['sl_long'],
                'sl_short': reasonings[sym]['sl_short'],
                'bias': reasonings[sym]['bias'],
                'council': councils[sym]['verdict'],
                'vip': format_vip(raw_data[sym], reasonings[sym]),
            } for sym in symbols}
        }
        import json as _j
        _snap_f = BASE / 'data' / 'deep_analysis_latest.json'
        _snap_f.write_text(_j.dumps(snap, ensure_ascii=False))
        for sym in symbols:
            _vip_f = BASE / 'data' / f'vip_snapshot_{sym.lower()}.json'
            _vip_f.write_text(_j.dumps({
                'ts': time.time(), 'sym': sym,
                'px': raw_data[sym]['px'],
                'direction': reasonings[sym]['direction'],
                'vip': format_vip(raw_data[sym], reasonings[sym]),
                'bias': reasonings[sym]['bias'],
                'council': councils[sym],
            }, ensure_ascii=False))
    except Exception as _se:
        pass

    return result


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=['BTC','ETH'])
    args = ap.parse_args()
    print(run_deep_analysis(args.symbols))
