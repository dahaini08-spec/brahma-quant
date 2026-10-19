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
        'iv':      sd.get('iv_rank', 0),
        'bb_width':sd.get('bb_width', 2.0),
        'score':   sd.get('score_final', 0),
    }


# ── Step2: 方仓+主力手法 ──────────────────────────────────────────────────

def enrich(d: dict) -> dict:
    sym = d['sym']
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

    # ⑤ OI资金流
    if oi_dir == 'SHORT_BUILD' and tak < 0.8:
        chain.append(('OI', f"SHORT_BUILD+Taker卖 = 主力主动建空仓，方向向下", -2))
        bias -= 2
    elif oi_dir == 'LONG_UNWIND' and tak > 1.2:
        chain.append(('OI', f"LONG_UNWIND+Taker买 = 逼空行情，空头被迫平仓", +1))
        bias += 1
    elif oi_dir == 'FLAT':
        chain.append(('OI', f"FLAT OI8H={oi_8h:+.2f}% = 无明确建仓方向", 0))

    # ⑥ GEX ZeroFlip
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

    # ⑩ SL计算（ATR铁律）
    sl_min = atr4h * 1.5
    sl_long  = round(px - sl_min, 1)
    sl_short = round(px + sl_min, 1)

    # GEX插针风险：SL必须在ZF下方
    if zf > 0 and sl_long > zf * 0.999:
        old_sl = sl_long
        sl_long = round(zf - atr4h * 0.5, 1)
        chain.append(('SL修正', f"SL${old_sl:,.1f}在ZF${zf:,.0f}上方 = 必被插针扫损，修正为${sl_long:,.1f}(ZF下方)", 0))

    # 最终方向判断
    if bias >= 3:
        direction = 'LONG'
        confidence = 'HIGH' if bias >= 5 else 'MED'
    elif bias <= -3:
        direction = 'SHORT'
        confidence = 'HIGH' if bias <= -5 else 'MED'
    else:
        direction = 'WAIT'
        confidence = 'LOW'

    # RR验证
    rr = 0
    if direction == 'LONG' and liq_up > 0:
        rr = round((liq_up - px) / (px - sl_long), 2) if (px - sl_long) > 0 else 0
    elif direction == 'SHORT' and liq_dn > 0:
        rr = round((px - liq_dn) / (sl_short - px), 2) if (sl_short - px) > 0 else 0

    if rr > 0 and rr < 1.2:
        chain.append(('RR', f"RR={rr}<1.2 = 风险回报不达标，降级WAIT", 0))
        direction = 'WAIT'

    return {
        'chain': chain, 'bias': bias,
        'direction': direction, 'confidence': confidence,
        'sl_long': sl_long, 'sl_short': sl_short,
        'sl_min': sl_min, 'rr': rr,
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
        f"【GEX+FVG+清算】",
        f" GEX ZF=${zf:,.0f}({'站上ZF' if zf_pct>0 else 'ZF下方'} {zf_pct:+.2f}%)",
        f" FVG4H Bull磁铁={fvg_str}",
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
        f"【综合判断】",
        f" 方向={direction}  偏向分={reasoning['bias']:+d}  RR={reasoning['rr']}",
        f" SL(多)≥${sl_long:,.1f}  SL(空)≤${sl_short:,.1f}",
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

    if direction == 'LONG':
        tp1 = round(liq_up * 0.998, 1) if liq_up > px * 1.005 else round(px + atr4h * 2, 1)
        tp2 = round(liq_up * 1.005, 1) if liq_up > px * 1.005 else round(px + atr4h * 3, 1)
        rr_str = f"{rr:.2f}" if rr > 0 else "计算中"
        long_line  = f"🟢 多单｜挂单区 ${px*0.997:,.1f}~${px*0.999:,.1f}"
        long_sl    = f"止损${sl_long:,.1f}｜目标${tp1:,.1f}→${tp2:,.1f}"
        short_line = "🔴 暂无空单｜等待结构"
        warn = f"{'LSR>2.5猎杀结构，等清算池' if d['lsr_g']>2.5 else f'方向={direction} RR={rr_str}'}"
        bust = f"跌破${sl_long:,.1f}+OI减少作废"
    elif direction == 'SHORT':
        tp1 = round(liq_dn * 1.002, 1) if 0 < liq_dn < px * 0.995 else round(px - atr4h * 2, 1)
        tp2 = round(liq_dn * 0.995, 1) if 0 < liq_dn < px * 0.995 else round(px - atr4h * 3, 1)
        rr_str = f"{rr:.2f}" if rr > 0 else "计算中"
        long_line  = "🟢 暂无多单｜等待结构"
        short_line = f"🔴 空单｜挂单区 ${px*1.001:,.1f}~${px*1.003:,.1f}"
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
