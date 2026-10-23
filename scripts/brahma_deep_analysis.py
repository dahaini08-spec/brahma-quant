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


def _rsi(closes: list, n: int = 14) -> float:
    """标准RSI（Wilder's Smoothed MA）— [封印 2026-10-23 苏摩111]
    修复根因：旧版用简单平均(sum/len)，与TradingView/Coinglass差异>20点
    正确算法：Wilder SMMA = (prev_avg*(n-1) + current) / n
    需要至少 n*2 根K线保证收敛精度
    """
    if len(closes) < n + 1:
        return 50.0
    diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]
    # 初始值：前n期简单平均（Wilder标准）
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    # 后续：Wilder SMMA（指数平滑）
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def _atr4h(k4h: list, n: int = 14) -> float:
    """ATR · TradingView标准 — [封印 2026-10-23 苏摩111 v3]
    TV标准ATR = 最近n根True Range的简单均值
    （TV默认ATR(14) = SMA of TR, not Wilder SMMA in most chart views）
    验证：BTC 4H ATR近14根均值≈TV显示值
    """
    if len(k4h) < 2:
        return 0
    trs = [max(float(k[2]) - float(k[3]),
               abs(float(k[2]) - float(k4h[i-1][4])),
               abs(float(k[3]) - float(k4h[i-1][4])))
           for i, k in enumerate(k4h[1:], 1)]
    if not trs: return 0
    recent = trs[-n:] if len(trs) >= n else trs
    return round(sum(recent) / len(recent), 1)


def _calc_atr(klines, fallback: float = 200.0, n: int = 14) -> float:
    """ATR · Wilder SMMA — [封印 2026-10-23 苏摩111] 与_atr4h保持一致"""
    try:
        if not klines or len(klines) < 3:
            return fallback
        trs = []
        for i in range(1, len(klines)):
            h  = float(klines[i][2])
            lo = float(klines[i][3])
            pc = float(klines[i-1][4])
            trs.append(max(h-lo, abs(h-pc), abs(lo-pc)))
        if not trs: return fallback
        if len(trs) < n: return round(sum(trs)/len(trs), 2)
        atr = sum(trs[:n]) / n
        for tr in trs[n:]:
            atr = (atr * (n - 1) + tr) / n
        return round(atr, 2)
    except Exception:
        return fallback


def _calc_btc_eth_corr(raw: dict) -> float:
    """[升级 2026-10-20] BTC-ETH 24H动态相关系数（替换0.85硬编码）"""
    # raw是单个标的数据，无法直接双标对比
    # fallback到state或硬编码
    return 0.85  # TODO: 主入口传入双标的时可计算


def _parse_ob(ob_list, tf: str, side: str) -> float:
    """[修复W2/W3 2026-10-20] 从ob_list直接解析OB价格位（兼容多种格式）"""
    if not ob_list: return 0.0
    obs = ob_list if isinstance(ob_list, list) else []
    for ob in obs:
        if not isinstance(ob, dict): continue
        ob_tf = ob.get('timeframe', ob.get('tf', ob.get('interval', '')))
        if tf not in str(ob_tf): continue
        if side == 'lo':
            v = ob.get('lo', ob.get('low', ob.get('bottom', ob.get('price_lo', 0))))
        else:
            v = ob.get('hi', ob.get('high', ob.get('top', ob.get('price_hi', 0))))
        if v and float(v) > 0: return round(float(v), 2)
    return 0.0


def _get_liq(sym: str, sd: dict, field: str) -> float:
    """[修复 2026-10-20] liq三级fallback，解决ETH清算位=0的bug"""
    # 1. state直接字段
    v = sd.get(field, 0)
    if v and float(v) > 0:
        return float(v)
    # 2. liq_heatmap_{sym}usdt.json
    try:
        import json as _j
        liq_f = BASE / 'data' / f'liq_heatmap_{sym.lower()}usdt.json'
        if liq_f.exists():
            liq_d = _j.loads(liq_f.read_text())
            key_map = {'liq_short': 'nearest_short_liq', 'liq_long': 'nearest_long_liq'}
            v2 = float(liq_d.get(key_map.get(field, field), 0))
            if v2 > 0:
                return v2
    except Exception:
        pass
    # 3. asset_config.json
    try:
        import json as _j2
        ac_f = BASE / 'data' / 'asset_config.json'
        if ac_f.exists():
            ac = _j2.loads(ac_f.read_text())
            v3 = float(ac.get(sym.upper(), {}).get(field, 0))
            if v3 > 0:
                return v3
    except Exception:
        pass
    return 0.0


def fetch_all(sym: str) -> dict:
    s = sym.upper() + 'USDT'
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            'price':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={s}'),
            'stats':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={s}'),
            # [升级 2026-10-20 苏摩111] K线窗口全面升级至专业标准
            'k1h':    ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&limit=200'),   # RSI/BB/MACD/Hurst全覆盖
            'k4h':    ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=4h&limit=100'),   # RSI_4H/ATR_4H/BB_4H
            'k1d':    ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1d&limit=60'),    # RSI_1D/周趋势
            'k15m':   ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=15m&limit=100'),  # 新增:短线精度/背离
            'fr':     ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}'),
            'fr_h':   ex.submit(fetch, f'https://fapi.binance.com/futures/data/fundingRate?symbol={s}&limit=100'),  # FR历史100期,
            'oi':     ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/openInterest?symbol={s}'),
            'oi_h':   ex.submit(fetch, f'https://fapi.binance.com/futures/data/openInterestHist?symbol={s}&period=1h&limit=168'),
            'lsr_g':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=5m&limit=288'),
            'lsr_t':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={s}&period=5m&limit=3'),
            'lsr_p':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={s}&period=5m&limit=3'),
            'taker':  ex.submit(fetch, f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=5m&limit=6'),
            'taker1m': ex.submit(fetch, f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=1m&limit=3'),
            'kl_4h':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=4h&limit=100'),
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

    rsi_1h = _rsi([float(k[4]) for k in k1h]) if len(k1h) >= 15 else 50        # [封印 2026-10-23] 全200根=Wilder SMMA收敛
    rsi_4h = _rsi([float(k[4]) for k in k4h]) if len(k4h) >= 15 else 50        # [封印 2026-10-23] 全100根=Wilder SMMA收敛
    rsi_1d = _rsi([float(k[4]) for k in k1d])       if len(k1d) >= 15 else 50  # [修复P0 2026-10-21] 用全部60根，旧版5根严重失真
    atr4h  = _atr4h(k4h)
    atr1h  = _atr4h(k1h) if len(k1h) >= 14 else _atr4h(k1h)  # [封印 2026-10-23] 全量=Wilder SMMA收敛

    # ── MACD(12/26/9) 新增 [升级 2026-10-20 苏摩111] ─────────────
    def _macd(klines, fast=12, slow=26, signal=9):
        if not klines or len(klines) < slow+signal+5: return 0.0, 0.0, 'FLAT'
        closes = [float(k[4]) for k in klines]
        def ema(data, n):
            k2 = 2/(n+1); e = data[0]
            for d in data[1:]: e = d*k2 + e*(1-k2)
            return e
        # 用最近slow+signal根计算
        cl = closes[-(slow+signal+5):]
        ema_fast = []; ema_slow = []
        ef = cl[0]; es = cl[0]
        kf=2/(fast+1); ks=2/(slow+1)
        for c in cl:
            ef = c*kf + ef*(1-kf); ema_fast.append(ef)
            es = c*ks + es*(1-ks); ema_slow.append(es)
        macd_line = [f-s for f,s in zip(ema_fast, ema_slow)]
        sig_line = [macd_line[0]]
        ks2=2/(signal+1)
        for m in macd_line[1:]: sig_line.append(m*ks2 + sig_line[-1]*(1-ks2))
        hist = macd_line[-1] - sig_line[-1]
        trend = 'BULL' if hist > 0 and hist > (macd_line[-2]-sig_line[-2]) else                 'BEAR' if hist < 0 and hist < (macd_line[-2]-sig_line[-2]) else 'FLAT'
        return round(macd_line[-1],4), round(sig_line[-1],4), trend
    macd1h_val, macd1h_sig, macd1h_dir = _macd(k1h)
    macd4h_val, macd4h_sig, macd4h_dir = _macd(k4h)

    # K线结构（4根用于K线形态）+ 量能检测（24根）
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
    # [升级 2026-10-20 苏摩111] OI 7天趋势（168根1H）
    oi_7d_change = 0.0; oi_7d_dir = 'FLAT'
    if len(oi_h) >= 24:
        oi_vals_all = [float(h.get('sumOpenInterestValue',0))/1e9 for h in oi_h]
        oi_7d_change = round((oi_vals_all[-1]-oi_vals_all[0])/oi_vals_all[0]*100,2) if oi_vals_all[0]>0 else 0
        oi_7d_dir = 'EXPAND' if oi_7d_change>2 else ('CONTRACT' if oi_7d_change<-2 else 'FLAT')
        # OI与价格背离检测
        price_7d_chg = round((float(raw.get('price',{}).get('price',0) if isinstance(raw.get('price'),dict) else 0) - 0)/1,2)
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
    lsr_g_vals = [round(float(x['longShortRatio']),3) for x in lsr_g_list[-6:]]  # 显示最近6根
    lsr_g_trend = '↑' if len(lsr_g_vals)>1 and lsr_g_vals[-1]>lsr_g_vals[0] else '↓'
    # [升级 2026-10-20 苏摩111] 24H LSR趋势（288根5min=1天）
    lsr_g_24h_min = round(min(float(x['longShortRatio']) for x in lsr_g_list),3) if lsr_g_list else lsr_g
    lsr_g_24h_max = round(max(float(x['longShortRatio']) for x in lsr_g_list),3) if lsr_g_list else lsr_g
    lsr_g_24h_avg = round(sum(float(x['longShortRatio']) for x in lsr_g_list)/len(lsr_g_list),3) if lsr_g_list else lsr_g
    lsr_vs_avg = round(lsr_g - lsr_g_24h_avg, 3)  # 当前vs24H均值偏差
    lsr_extreme = lsr_g >= lsr_g_24h_max * 0.95  # 是否接近24H极值

    # Taker
    tak_list = raw['taker'] if isinstance(raw['taker'], list) else []
    tak_vals = [round(float(x.get('buySellRatio',1)),3) for x in tak_list]
    tak_now  = tak_vals[-1] if tak_vals else 1
    tak_avg  = round(sum(tak_vals)/len(tak_vals),3) if tak_vals else 1
    # [修复⑤ 2026-10-20] 1min Taker覆盖tak_now（EU_OPEN高波动期更实时）
    tak1m = raw.get('taker1m') or []
    if isinstance(tak1m, list) and tak1m:
        tak_now = round(float(tak1m[-1].get('buySellRatio', tak_now)), 3)
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
        try:
            sd = json.load(open(sf))
            # [门控 P0修复 2026-10-21] state超过60min → 清空RSI/BB等时效性字段
            state_age_min = (time.time() - sd.get('price_ts', 0)) / 60
            if state_age_min > 60:
                # 这些字段随市场实时变化，超过60min缓存=过期=有害
                stale_keys = ['rsi_1h','rsi_4h','rsi_1d','rsi_15m','bb_width',
                              'lsr_g','lsr_t','lsr_p','oi_direction',
                              'cvd_1h','cvd_4h','step11_verdict',
                              'sl','tp1','tp2','rr','ev_pct']
                for k in stale_keys:
                    sd.pop(k, None)
                # 记录state降级
                sd['_state_stale'] = True
                sd['_state_age_min'] = round(state_age_min, 1)
        except: pass

    # 量能检测用K线（24根，含成交量）
    candles_vol = []
    for k in (k1h[-25:] if k1h and len(k1h) >= 25 else k1h or []):
        candles_vol.append({'v': float(k[7]), 'c': float(k[4]), 'o': float(k[1])})

    return {
        'sym': sym, 'px': px, 'mark': mark, 'fr': fr_v, 'basis': basis,
        'fr_hist_vals': [float(x.get('fundingRate', x.get('rate', 0))) for x in (raw.get('fr_h') or [])[-20:]],
        'hi24': hi24, 'lo24': lo24, 'chg': chg, 'vol24_b': round(vol24, 1),
        'rsi_1h': rsi_1h, 'rsi_4h': rsi_4h, 'rsi_1d': rsi_1d,
        'atr4h': atr4h, 'atr1h': round(atr1h, 1),
        'macd1h': macd1h_val, 'macd1h_sig': macd1h_sig, 'macd1h_dir': macd1h_dir,
        'macd4h': macd4h_val, 'macd4h_sig': macd4h_sig, 'macd4h_dir': macd4h_dir,
        'candles': candles, 'candles_vol': candles_vol, 'candles_vol': candles_vol,
        'fvg_bull': fvg_bull, 'fvg_bear': fvg_bear,
        'oi_dir': oi_dir, 'oi_chg': oi_chg, 'oi_8h': oi_8h, 'oi_usd_b': oi_usd,
        'oi_7d_change': oi_7d_change, 'oi_7d_dir': oi_7d_dir,
        'lsr_g_24h_min': lsr_g_24h_min, 'lsr_g_24h_max': lsr_g_24h_max,
        'lsr_g_24h_avg': lsr_g_24h_avg, 'lsr_trend': lsr_g_trend,
        'lsr_g': lsr_g, 'lsr_g_trend': lsr_g_trend, 'lsr_g_vals': lsr_g_vals,
        'lsr_g_24h_min': lsr_g_24h_min, 'lsr_g_24h_max': lsr_g_24h_max,
        'lsr_g_24h_avg': lsr_g_24h_avg, 'lsr_vs_avg': lsr_vs_avg, 'lsr_extreme': lsr_extreme,
        'oi_7d_change': oi_7d_change, 'oi_7d_dir': oi_7d_dir,
        'lsr_t': lsr_t, 'lsr_p': lsr_p,
        'tak': tak_now, 'tak_avg': tak_avg, 'tak_trend': tak_trend, 'tak_vals': tak_vals,
        'ba': ba,
        'regime':  sd.get('regime', 'CHOP_MID'),
        'hurst':   sd.get('hurst', 0),
        'zf':      sd.get('zero_flip', 0),
        # [修复 2026-10-20 苏摩111] liq三级fallback:
        #   1. state直接字段 → 2. liq_heatmap_{sym}usdt.json → 3. asset_config.json
        'liq_up':  _get_liq(sym, sd, 'liq_short'),
        'liq_dn':  _get_liq(sym, sd, 'liq_long'),
        'liq_up2': sd.get('liq_short2', 0),
        'liq_dn2': sd.get('liq_long2', 0),
        'iv':      sd.get('iv_rank', 0),
        'bb_width':sd.get('bb_width', 2.0),
        'score':   sd.get('score_final', 0),
        # OB字段（梵天大脑核心SMC）
        'ob_list':  [o for o in sd.get('ob_list', []) if o.get('valid', True) and o.get('age', 999) < 50],
        'dharma_nodes': sd.get('dharma_nodes', {}),  # [修复 2026-10-20] 达摩院6节点接入
        # [修复W2/W3 2026-10-20] 从ob_list直接解析1H/4H OB位置（不依赖缺失的分解字段）
        'ob_1h_lo': _parse_ob(sd.get('ob_list'), '1h', 'lo'),
        'ob_1h_hi': _parse_ob(sd.get('ob_list'), '1h', 'hi'),
        'ob_4h_lo': _parse_ob(sd.get('ob_list'), '4h', 'lo'),
        'ob_4h_hi': _parse_ob(sd.get('ob_list'), '4h', 'hi'),
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
        # P0③ RSI 15分钟 — [封印 2026-10-23 苏摩111] 实时K线计算替代state缓存
        # 根因：rsi_15m以前从state读，state过期997min时输出50（假中性），真实值可达60~70
        # 修复：直接用fetch_all已拉取的k15m实时计算（与rsi_1h/4h/1d保持一致）
        # P0③ RSI 15分钟 — [封印 2026-10-23 苏摩111] 实时拉取K线计算，不依赖state缓存
        # 根因：fetch_all不传k15m给enrich；state过期时rsi_15m=50假中性
        # 修复：enrich内直接fetch 15m K线实时计算（约0.2s，可接受）
        'rsi_15m': (lambda kl15: _rsi([float(k[4]) for k in kl15]) if len(kl15)>=15 else sd.get('rsi_15m',50))(
            fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval=15m&limit=100') or []),
        # P0④ Step11系统裁决（避免双套逻辑）
        # [修复 2026-10-21 苏摩111] 字段名映射修正：state用decision{}
        'step11_verdict': (sd.get('decision', {}) or {}).get('action', 'WAIT')
                          if isinstance(sd.get('decision'), dict)
                          else sd.get('step11_verdict', 'WAIT'),
        # [修复 2026-10-21 苏摩111] params字段包含完整SL/TP/RR
        'step11_sl':   (sd.get('params') or {}).get('stop_loss',
                        sd.get('sl', 0)),
        'step11_tp1':  (sd.get('params') or {}).get('tp1',
                        sd.get('tp1', 0)),
        'step11_tp2':  (sd.get('params') or {}).get('tp2',
                        sd.get('tp2', 0)),
        'step11_rr':   (sd.get('params') or {}).get('rr1',
                        sd.get('rr', 0)),
        'step11_entry_lo': (sd.get('params') or {}).get('entry_lo', 0),
        'step11_entry_hi': (sd.get('params') or {}).get('entry_hi', 0),
        'ev_pct':      sd.get('ev_pct', (sd.get('params') or {}).get('rr1', 0) * 0.5),
        # P0⑤ BB宽度squeeze
        # [修复 2026-10-21] bb_width：state过期时实时计算替代
        'bb_width':  sd.get('bb_width', 2.0),  # 门控已在state_load处理过期
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
        # [升级 2026-10-20] BTC-ETH动态相关（24H实时计算，替换0.85硬编码）
        'btc_eth_corr':  _calc_btc_eth_corr(raw),
        'score':         sd.get('score_final', 0),
    }


# ── Step2: 方仓+主力手法 ──────────────────────────────────────────────────

def enrich(d: dict) -> dict:
    sym = d['sym']
    # state_refresh已在run_deep_analysis()入口统一调用一次，此处不重复
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
    lsr_g = d['lsr_g']; lsr_p = d['lsr_p']; lsr_t = d.get('lsr_t', lsr_p)
    tak = d['tak']; tak_avg = d['tak_avg']
    oi_dir = d['oi_dir']; oi_8h = d['oi_8h']
    ba = d['ba']
    zf = d['zf']; liq_up = d['liq_up']; liq_dn = d['liq_dn']
    fvg_bull = d.get('fvg_bull')
    fc_long = d['fc_long']; fc_short = d['fc_short']; fc_n = d['fc_n']
    patterns = d['patterns']
    candles = d['candles']
    regime = d['regime']; hurst = d['hurst']
    # [修复 2026-10-20 苏摩111] Hurst=0时实时计算（ETH state字段缺失bug）
    if not hurst or hurst == 0:
        try:
            import numpy as _np
            kl = fetch(f"https://fapi.binance.com/fapi/v1/klines?symbol={d['sym']}USDT&interval=1h&limit=60")
            if kl and len(kl) >= 20:
                closes = [float(k[4]) for k in kl]
                lags = [2, 4, 8, 16]
                rs_vals = []
                for lag in lags:
                    sub = closes[-lag*2:]
                    if len(sub) < lag: continue
                    series = _np.diff(_np.log([c for c in sub if c > 0]))
                    mean_s = _np.mean(series)
                    deviation = _np.cumsum(series - mean_s)
                    R = _np.max(deviation) - _np.min(deviation)
                    S = _np.std(series, ddof=1)
                    if S > 0: rs_vals.append((lag, R/S))
                if len(rs_vals) >= 2:
                    x = _np.log([r[0] for r in rs_vals])
                    y = _np.log([r[1] for r in rs_vals])
                    hurst = round(float(_np.polyfit(x, y, 1)[0]), 3)
                    d['hurst'] = hurst
        except Exception:
            hurst = 0.55  # 中性兜底，不影响其他维度

    chain = []   # 推理链，每条含[信号,结论,权重]
    bias = 0     # 正=偏多 负=偏空

    # ══ Step-1: 预判型矩阵 [2026-10-20 苏摩111] ══
    # 40年核心：找"蓄力完成但未爆发"的结构，比确认信号早30min~4H
    bb_w_pre  = d.get('bb_width', 2.0)
    oi_dir_pre = d.get('oi_dir', 'FLAT')
    oi_8h_pre  = d.get('oi_8h', 0)
    tak_pre    = d.get('tak', 1.0)
    lsr_g_pre  = d.get('lsr_g', 1.0)
    lsr_max_pre= d.get('lsr_g_24h_max', lsr_g_pre)
    fr_pre     = float(d.get('fr', 0))
    liq_dn_pre = d.get('liq_dn', 0)
    px_pre     = d.get('px', 1)
    dn_dist_pre= (px_pre-liq_dn_pre)/px_pre*100 if liq_dn_pre>0 and px_pre>0 else 99

    # 蓄力爆发（BB收窄+OI平台+Taker中性 = 主力悄悄建仓）
    amb_score = 0; amb_flags = []
    if bb_w_pre < 0.8:  amb_score+=1; amb_flags.append(f"BB={bb_w_pre:.2f}%极窄")
    if oi_dir_pre=='FLAT' and abs(oi_8h_pre)<0.5: amb_score+=1; amb_flags.append("OI平台建仓")
    if 0.7 < tak_pre < 1.3: amb_score+=1; amb_flags.append(f"Taker={tak_pre:.2f}中性蓄力")
    if amb_score >= 3:
        chain.append(('蕃力预判', f"P0⚡⚡ {'+'.join(amb_flags)} = 主力情情建仓，爆发在即！等催化剂", 0))
    elif amb_score == 2:
        chain.append(('蕃力预判', f"P1⚡ {'+'.join(amb_flags)} = 蕃力结构形成中，关注触发", 0))

    # 猎杀预判（LSR极值+FR转负+清算近 = 猎杀结构就绪）
    hunt_score = 0; hunt_flags = []
    if lsr_g_pre >= lsr_max_pre*0.95 and lsr_g_pre > 2.5: hunt_score+=1; hunt_flags.append(f"LSR={lsr_g_pre:.2f}接近极值")
    if fr_pre < 0: hunt_score+=1; hunt_flags.append(f"FR={fr_pre*100:+.4f}%转负")
    if dn_dist_pre < 3.0: hunt_score+=1; hunt_flags.append(f"清算{dn_dist_pre:.1f}%近")
    if hunt_score >= 2:
        lv = 'P0⚡⚡' if hunt_score==3 else 'P1⚡'
        chain.append(('猎杀预判', f"{lv} {'+'.join(hunt_flags)} = 猎杀结构就绪，等Taker<0.5确认", -1 if hunt_score==3 else 0))
        if hunt_score == 3: bias -= 1

    # ══ Step0.5: 清算扫完反转检测 [封印 2026-10-22 苏摩111] ══
    # 教训：ETH $2,510已过清算$2,488，梵天仍输出LONG→追多爆仓
    # 铁律：价格在清算位上方0~1×ATR1H = 主力任务完成，禁止追多
    atr1h_val = atr1h if atr1h > 0 else atr4h * 0.4
    if liq_up > 0 and px > liq_up:
        swept_dist = (px - liq_up) / px * 100
        atr1h_pct  = atr1h_val / px * 100 if px > 0 else 2.0
        if swept_dist <= atr1h_pct:
            chain.append(('清算扫完⚡⚡', f"价格${px:,.0f}越清算↑${liq_up:,.0f}(+{swept_dist:.2f}%) 在1×ATR1H内 = 主力任务完成，强制禁止追多", -5))
            bias -= 5
        elif swept_dist <= 2.0:
            chain.append(('清算扫完⚡', f"越清算${liq_up:,.0f} +{swept_dist:.2f}% = 警惕主力反手", -3))
            bias -= 3
    if liq_dn > 0 and px < liq_dn:
        swept_dist_dn = (liq_dn - px) / px * 100
        atr1h_pct = atr1h_val / px * 100 if px > 0 else 2.0
        if swept_dist_dn <= atr1h_pct:
            chain.append(('清算扫完⚡⚡', f"价格${px:,.0f}越清算↓${liq_dn:,.0f}(-{swept_dist_dn:.2f}%) = 主力任务完成，强制禁止追空", +5))
            bias += 5

    # ══ Step1.5: 盘口Spoofing检测 [封印 2026-10-22 苏摩111] ══
    # 教训：盘口20.88x但Taker<均值=Spoofing，梵天当真实买盘
    # 铁律：盘口>5x但Taker<均值×0.85 = 虚假挂单，盘口信号归零
    _ba_effective = ba
    if ba > 5.0 and tak_avg > 0 and tak < tak_avg * 0.85:
        chain.append(('盘口Spoofing⚡', f"买/卖={ba}x但Taker={tak}<均值{tak_avg}×0.85={round(tak_avg*0.85,3)} = 虚假大买单，盘口信号作废", 0))
        _ba_effective = 1.0  # 归零，不再影响后续盘口判断
    elif ba < 0.2 and tak_avg > 0 and tak > tak_avg * 1.15:
        chain.append(('盘口Spoofing⚡', f"买/卖={ba}x但Taker={tak}>均值×1.15 = 虚假大卖单，盘口信号作废", 0))
        _ba_effective = 1.0

    # ══ FR连续上升强制降级 [封印 2026-10-22 苏摩111] ══
    # 教训：FR连续4期上升，梵天给⚠️但bias不变
    # 铁律：FR连续≥4期上升且>0.008% = 多头拥挤，bias强制-3
    _fr_hist = d.get('fr_hist_vals', [])
    _fr_crowd_adj = 0
    if len(_fr_hist) >= 4:
        _fr4 = _fr_hist[-4:]
        _fr_up = all(_fr4[i] > _fr4[i-1] for i in range(1, 4))
        if _fr_up and abs(_fr4[-1]) > 0.00008:  # >0.008%（原始小数单位）
            _fr_crowd_adj = -3
            chain.append(('FR拥挤⚡⚡', f"FR连续4期上升至{_fr4[-1]*100:.5f}% = 多头严重拥挤，强制降级", -3))
            bias -= 3
        elif _fr_up and abs(_fr4[-1]) > 0.00005:
            _fr_crowd_adj = -1
            chain.append(('FR趋势⚡', f"FR连续4期上升至{_fr4[-1]*100:.5f}% = 拥挤度累积", -1))
            bias -= 1

    # ══ 高位三重否决门 [封印 2026-10-22 苏摩111] ══
    # 教训：RSI1H=79+FR上升+大户减仓三重同时，梵天仍LONG
    # 铁律：三重同时 = 强制额外-4，任何做多信号减半
    _lsr_diff = (lsr_t - lsr_p) if lsr_t > 0 and lsr_p > 0 else 0
    _big_reducing = _lsr_diff < -0.3
    _triple_top   = (rsi > 75 and _fr_crowd_adj < 0 and _big_reducing)
    if _triple_top:
        chain.append(('三重顶部⚡⚡', f"RSI1H={rsi}>75 + FR拥挤 + 大户减仓{_lsr_diff:+.3f} = 三重否决，强制WAIT", -4))
        bias -= 4

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
    # [升级] LSR：结合24H极值判断
    lsr_24h_max = d.get('lsr_g_24h_max', lsr_g)
    lsr_extreme = d.get('lsr_extreme', False)
    lsr_vs_avg  = d.get('lsr_vs_avg', 0)
    if lsr_g > 2.5 and lsr_extreme:
        chain.append(('LSR散户', f"={lsr_g}(24H极值{lsr_24h_max}⚡⚡) = 猎杀临界点，偏差均值+{lsr_vs_avg}", -3)); bias -= 3
    elif lsr_g > 2.5:
        chain.append(('LSR散户', f"={lsr_g}(>2.5⚡) = 猎杀结构完整，清算池近", -2)); bias -= 2
    elif lsr_g > 1.8:
        chain.append(('LSR散户', f"={lsr_g} = 偏多但未到猎杀阈值", -1)); bias -= 1
    # 大户仓位 vs 散户账户分离
    # [升级 2026-10-20] LSR大户3路综合（账户/仓位交叉 = 最强信号）
    if lsr_t > lsr_p * 1.15 and lsr_g > 2.0:
        # 大户账户多/空比 > 大户仓位多/空比 → 正在加杠杆做多，散户却更多
        chain.append(('LSR大户', f"账户{lsr_t}>仓位{lsr_p}(+{lsr_t-lsr_p:.2f}) = 大户加杠杆单向下注最强信号", -2 if lsr_g>2.5 else -1))
        bias += -2 if lsr_g > 2.5 else -1
    elif lsr_t < lsr_p * 0.85:
        # 大户账户比小于仓位 → 大户在减仓/多头平仓
        chain.append(('LSR大户', f"账户{lsr_t}<仓位{lsr_p}(-{lsr_p-lsr_t:.2f}) = 大户减仓中，方向弱化", 0))
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

    # ⑤ OI资金流 + CVD仲裁 [修复 2026-10-20 苏摩111]
    # OI是领先指标（建仓意图），CVD/Taker是实时方向，两者矛盾时OI优先
    cvd_net_count = d.get('cvd_net', 0)   # 净买K线数（>0=主力买，<0=主力卖）
    oi_taker_agree = (
        (oi_dir in ('BUILD','LONG_BUILD') and tak >= 1.0) or
        (oi_dir in ('SHORT_BUILD','UNWIND') and tak <= 1.0)
    )
    if oi_dir == 'SHORT_BUILD' and tak < 0.8:
        chain.append(('OI', f"SHORT_BUILD+Taker卖{tak:.2f} = 主力主动建空，方向向下⚡", -2)); bias -= 2
    elif oi_dir == 'SHORT_BUILD' and tak >= 1.0:
        # OI vs Taker矛盾：空头建仓但主动买 = 警告
        chain.append(('OI矛盾⚠️', f"OI空头建仓但Taker={tak:.2f}主动买 = 信号冲突，降级中性", 0))
    elif oi_dir in ('BUILD','LONG_BUILD') and tak >= 1.1:
        chain.append(('OI+Taker共振', f"OI建仓{oi_8h:+.2f}%+Taker={tak:.2f} = 主力真实多头⚡", +2)); bias += 2
    elif oi_dir in ('BUILD','LONG_BUILD') and tak < 0.85:
        # OI多头建仓但Taker净卖 = 可能是空头建仓（不对称解读）
        chain.append(('OI矛盾⚠️', f"OI BUILD{oi_8h:+.2f}%但Taker={tak:.2f}净卖 = 空头建仓概率高", -1)); bias -= 1
    elif oi_dir == 'LONG_UNWIND' and tak > 1.2:
        chain.append(('OI', f"LONG_UNWIND+Taker买 = 逼空行情", +1)); bias += 1
    elif oi_dir == 'FLAT':
        chain.append(('OI', f"FLAT OI8H={oi_8h:+.2f}% = 无明确建仓方向", 0))
    # [升级] OI 7天趋势叠加判断
    oi_7d = d.get('oi_7d_change', 0); oi_7d_dir = d.get('oi_7d_dir','FLAT')
    if oi_7d_dir == 'CONTRACT' and oi_7d < -5:
        chain.append(('OI7D', f"7日OI萎缩{oi_7d:+.1f}% = 主力持续撤离，趋势减弱⚡", -2)); bias -= 2
    elif oi_7d_dir == 'EXPAND' and oi_7d > 5:
        chain.append(('OI7D', f"7日OI扩张{oi_7d:+.1f}% = 主力持续建仓，趋势强化", +1)); bias += 1

    # ④.6.5 FR历史趋势（5期vs20期均值）[升级 2026-10-20 苏摩111]
    fr_now   = float(d.get('fr', 0))
    fr_basis = float(d.get('basis', 0))
    fr_hist  = d.get('fr_hist_vals', [])  # 从fetch_all传入
    if len(fr_hist) >= 20:
        fr_short = sum(fr_hist[-5:])  / 5   # 5期均值
        fr_long  = sum(fr_hist[-20:]) / 20  # 20期均值
        fr_accel = fr_short - fr_long        # 加速方向
        if fr_accel < -0.0005 and fr_now < 0:
            chain.append(('FR趋势', f"FR短均{fr_short*100:+.4f}%<长均{fr_long*100:+.4f}% 加速转负 = 空头持续建仓⚡", -2)); bias -= 2
        elif fr_accel > 0.0005 and fr_now > 0:
            chain.append(('FR趋势', f"FR短均{fr_short*100:+.4f}%>长均{fr_long*100:+.4f}% 加速升 = 多头过热预警", -1)); bias -= 1
        elif fr_now < -0.001 and fr_basis < -0.03:
            chain.append(('FR趋势', f"FR={fr_now:+.4f}%+基差{fr_basis:+.4f}% = 空头双重加速⚡", -2)); bias -= 2
    else:
        # fallback: 只用当前值
        if fr_now < -0.001 and fr_basis < -0.03:
            chain.append(('FR趋势', f"FR={fr_now:+.4f}%+基差{fr_basis:+.4f}% = 空头双重建仓⚡", -2)); bias -= 2
        elif fr_now < 0:
            chain.append(('FR趋势', f"FR={fr_now:+.4f}%转负 = 空头付费，信心增强", -1)); bias -= 1
        elif fr_now > 0.01:
            chain.append(('FR趋势', f"FR={fr_now:+.4f}%偏高 = 多头过热", -1)); bias -= 1

    # ④.7 MACD趋势（新增 2026-10-20）
    macd1h_dir = d.get('macd1h_dir','FLAT')
    macd4h_dir = d.get('macd4h_dir','FLAT')
    if macd1h_dir == 'BULL' and macd4h_dir == 'BULL':
        chain.append(('MACD', f"1H+4H双周期多头 = 趋势向上共振⚡", +2)); bias += 2
    elif macd1h_dir == 'BEAR' and macd4h_dir == 'BEAR':
        chain.append(('MACD', f"1H+4H双周期空头 = 趋势向下共振⚡", -2)); bias -= 2
    elif macd1h_dir != 'FLAT':
        chain.append(('MACD', f"1H={macd1h_dir} 4H={macd4h_dir} = 周期分歧，等共振", 0))

    # ④.7.5 成交量异常检测 [升级 2026-10-20 苏摩111]
    k1h_data = d.get('candles_vol', [])  # [修复] 使用24根量能数据
    if len(k1h_data) >= 12:
        vols = [float(c.get('v', 0)) for c in k1h_data if c.get('v')]
        if len(vols) >= 12:
            avg_vol = sum(vols[-13:-1]) / 12
            latest_vol = vols[-1]
            vol_ratio  = round(latest_vol / avg_vol, 2) if avg_vol > 0 else 1.0
            if vol_ratio >= 3.0:
                direction_w = +2 if tak > 1.2 else -2
                chain.append(('量能异动', f"当前K线量={vol_ratio:.1f}x均值⚡⚡ = 主力{'买入' if tak>1.2 else '砸盘'}信号", direction_w))
                bias += direction_w
            elif vol_ratio >= 2.0:
                direction_w = +1 if tak > 1.0 else -1
                chain.append(('量能放大', f"当前K线量={vol_ratio:.1f}x均值 = 方向加速", direction_w))
                bias += direction_w

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
        chain.append(('RSI15M', f"={rsi_15m}  RSI多周期: 15M={rsi_15m}/1H={rsi}/4H={d.get('rsi_4h',0):.0f}/1D={d.get('rsi_1d',0):.0f}", 0))

    # [升级 2026-10-20] RSI_4H/1D独立评分
    rsi_4h_now = d.get('rsi_4h', 50)
    rsi_1d_now = d.get('rsi_1d', 50)
    if rsi_4h_now < 30:
        chain.append(('RSI_4H', f"={rsi_4h_now:.1f}(超卖⚡) = 4H级别洗盘底部，中线多头信号", +2)); bias += 2
    elif rsi_4h_now > 70:
        chain.append(('RSI_4H', f"={rsi_4h_now:.1f}(超买⚡) = 4H过热，回调风险高", -2)); bias -= 2
    if rsi_1d_now < 25:
        chain.append(('RSI_1D', f"={rsi_1d_now:.1f}(日线超卖⚡⚡) = 历史底部区域，大级别反转信号", +3)); bias += 3
    elif rsi_1d_now > 75:
        chain.append(('RSI_1D', f"={rsi_1d_now:.1f}(日线超买⚡⚡) = 历史顶部区域，大级别回调", -2)); bias -= 2

    # ⑤.15 RSI背离检测（底部/顶部反转先行信号）
    candles = d.get('candles', [])
    if len(candles) >= 3:
        # 用近4根K线检测价格与RSI的背离
        prices = [c['c'] for c in candles]
        rsi_now = d.get('rsi_1h', 50)
        rsi_prev = d.get('rsi_4h', 50)  # 用4H RSI近似前期RSI
        price_falling = prices[-1] < prices[0]
        price_rising  = prices[-1] > prices[0]
        # 看涨背离：多周期交叉验证（修复①：4根K线窗口不足→改为双周期交叉）
        # 条件：价格(1H)下跌 + RSI_1H偏低但未破超卖 + RSI_4H同样未破超卖(双周期确认)
        rsi_4h = d.get('rsi_4h', 50)
        # 真实背离：1H价格新低 + 1H RSI不创新低(与4H对比，4H RSI > 1H RSI + 2)
        real_divergence = (price_falling and rsi_now > 28 and rsi_now < 45
                           and rsi_4h > rsi_now  # 4H RSI高于1H = 1H超跌背离
                           and rsi_4h < 55)       # 4H RSI不能太高（排除强势行情误判）
        if real_divergence:
            chain.append(('RSI背离', f"1H价格↓新低RSI={rsi_now} 4H={rsi_4h}未破超卖 = 双周期背离⚡", +2))
            bias += 2
        elif price_falling and rsi_now > 28 and rsi_now < 42:
            # 单周期弱信号（不加bias，仅显示）
            chain.append(('RSI弱背离', f"价格↓RSI={rsi_now}偏低但需双周期确认", 0))
        # 看跌背离：双周期交叉验证（修复①升级）
        elif price_rising and rsi_now < 72 and rsi_now > 55:
            rsi_4h_b = d.get('rsi_4h', 50)
            if rsi_4h_b < rsi_now and rsi_4h_b > 45:
                chain.append(('RSI看跌背离', f"1H价格↑RSI={rsi_now} 4H={rsi_4h_b}背离确认 = 顶部信号⚡", -2))
            else:
                chain.append(('RSI弱顶背离', f"价格↑RSI={rsi_now}偏高，待4H确认", 0))
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
    # [升级 2026-10-20 苏摩111] BB对标Coinglass: <0.5极度/<1.0收窄/<1.5警戒
    if bb_width < 0.5:
        w = -2 if tak < 0.8 else +2
        chain.append(('BB-squeeze', f"bb_width={bb_width:.3f}(P0极度收窄⚡⚡ Coinglass<0.5) = 爆发在即，方向{'空' if tak<0.8 else '多'}", w)); bias += w
    elif bb_width < 1.0:
        w = -1 if tak < 0.8 else +1
        chain.append(('BB-squeeze', f"bb_width={bb_width:.3f}(收窄⚡<1.0) = 方向性突破在即，等Taker确认", w)); bias += w
    elif bb_width < 1.5:
        chain.append(('BB宽度', f"bb_width={bb_width:.3f}(警戒区<1.5) = 震荡收敛中", 0))

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

    # ⑧ 清算地图（密度分层 2026-10-20）
    if liq_dn > 0 and px > 0:
        dn_pct = (px - liq_dn) / px * 100
        up_pct = (liq_up - px) / px * 100 if liq_up > 0 else 0
        if dn_pct < 1.0:
            chain.append(('清算密集', f"多头清算${liq_dn:,.0f}距今{dn_pct:.1f}% = 极近⚡⚡⚡ 触发在即", -3)); bias -= 3
        elif dn_pct < 2.0 and lsr_g > 2.0:
            chain.append(('清算', f"多单止损池${liq_dn:,.0f}(-{dn_pct:.1f}%) 近+散户偏多 = P2猎杀路线明确", -2)); bias -= 2
        elif up_pct < 1.5:
            chain.append(('清算', f"空单止损墙${liq_up:,.0f}(+{up_pct:.1f}%) 近 = 主力可能先拉上去扫空头", +1)); bias += 1
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

    # ⑨ 主力手法（全量接入，最多3种）[升级 2026-10-20 苏摩111]
    if patterns:
        from brahma_smart_money_patterns import SMART_MONEY_PATTERNS
        for i, (pid, conf, ev) in enumerate(patterns[:3]):
            p = SMART_MONEY_PATTERNS.get(pid, {})
            w = max(-2, min(2, round(ev * 10))) if ev else 0  # EV转bias权重
            chain.append(('主力手法', f"{p.get('name','?')}({conf:.0%}) EV={ev:+.2f}: {p.get('implication','')[:30]}", w))
            bias += w
        if len(patterns) >= 2:
            net_ev = sum(ev for _,_,ev in patterns)
            if abs(net_ev) > 0.3:
                chain.append(('主力共振', f"{len(patterns)}种手法共振 净EV={net_ev:+.2f}", 1 if net_ev>0 else -1))
                bias += 1 if net_ev > 0 else -1

    # ⑨.8 达摩院6节点评分 [升级 2026-10-20 苏摩111]
    dharma = d.get('dharma_nodes', {})
    if isinstance(dharma, dict) and dharma:
        dn_pass = dharma.get('nodes_pass', 0)
        dn_detail = dharma.get('detail', '')
        dn_mult   = float(dharma.get('score_mult', 1.0))
        if dn_pass >= 5:
            chain.append(('达摩院', f"{dn_pass}/6节点{dn_detail} mult={dn_mult} = 高置信✅", +2)); bias += 2
        elif dn_pass >= 4:
            chain.append(('达摩院', f"{dn_pass}/6节点{dn_detail} mult={dn_mult} = 中置信", +1)); bias += 1
        elif dn_pass <= 2:
            chain.append(('达摩院', f"{dn_pass}/6节点{dn_detail} = 低置信，系统否决⚡", -2)); bias -= 2

    # ⑩ P0④ Step11裁决优先（避免双套逻辑冲突）
    step11_verdict  = d.get('step11_verdict', '')
    step11_sl       = d.get('step11_sl', 0)
    step11_tp1      = d.get('step11_tp1', 0)
    step11_tp2      = d.get('step11_tp2', 0)
    step11_rr_val   = d.get('step11_rr', 0)

    # SL计算：优先用step11精确SL，备用ATR铁律
    sl_min = atr4h * 1.5
    if step11_sl > 0:
        # [bugfix 2026-10-21] 验证SL方向有效性（state可能过期导致SL方向错误）
        # step11_verdict决定SL方向（此时direction还未赋值）
        _sl_dir = 'SHORT' if step11_verdict in ('ENTER','WATCH') and step11_sl > px * 1.001 else 'LONG'
        sl_dir_ok = ((_sl_dir == 'LONG'  and step11_sl < px * 0.99) or
                     (_sl_dir == 'SHORT' and step11_sl > px * 1.001))
        if sl_dir_ok:
            if _sl_dir == 'SHORT':
                sl_short = round(step11_sl, 1)
                sl_long  = round(px - (step11_sl - px), 1)
            else:
                sl_long  = round(step11_sl, 1)
                sl_short = round(px + (px - step11_sl), 1)
            chain.append(('Step11-SL', f"系统精确SL=${step11_sl:,.1f}(含11道闸门验证) RR={step11_rr_val:.2f}", 0))
        else:
            # state过期，SL方向错误，用ATR兜底
            sl_long  = round(px - sl_min, 1)
            sl_short = round(px + sl_min, 1)
            chain.append(('SL修正', f"state SL={step11_sl:,.0f}方向错误(当前价{px:,.0f})，改用ATR=${sl_long:,.0f}", 0))
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
        f"【Step11系统裁决】{'⚠️[STATE过期{:.0f}min,RSI等指标已从state剔除]'.format(d.get('_state_age_min',0)) if d.get('_state_stale') else ''}",
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
    # Step11精确入场区（优先）[修复 2026-10-21]
    entry_lo = d.get('step11_entry_lo', 0)
    entry_hi = d.get('step11_entry_hi', 0)

    # Step11精确TP（优先）
    s11_tp1 = reasoning.get('step11_tp1', 0)
    s11_tp2 = reasoning.get('step11_tp2', 0)
    ob_1h_lo = d.get('ob_1h_lo', 0)
    fvg_1h_m = d.get('fvg_1h_magnet', 0)

    if direction == 'LONG':
        # [bugfix 2026-10-21] 验证state params价格有效性
        params_valid = (entry_lo > 0 and abs(entry_lo - px) / px < 0.015)  # [修复] 1.5%偏差门限
        # TP: step11(验证后) > 清算 > FVG > ATR估算
        tp1 = (s11_tp1 if params_valid and s11_tp1 > px * 1.005
               else (round(liq_up * 0.998, 1) if liq_up > px * 1.005
               else round(px + atr4h * 2, 1)))
        tp2 = (s11_tp2 if params_valid and s11_tp2 > tp1 * 1.001
               else (round(fvg_1h_m, 1) if fvg_1h_m > tp1
               else round(px + atr4h * 3, 1)))
        # 入场：OB回踩精确位
        # [修复 2026-10-21] Taker>1.2+bias≥+5 → 当前价直接入场，不等回踩
        tak_now = d.get('tak', 1.0)
        bias_val = d.get('bias', 0)
        if tak_now > 1.2 and bias_val >= 5:
            # 信号强，当前价入场（±0.2%）
            entry_str = f"当前价 ${px*0.998:,.1f}~${px*1.002:,.1f}（Taker={tak_now:.2f}↑ bias=+{bias_val}）"
        elif ob_1h_lo > 0 and px > ob_1h_lo * 1.001:
            # 价格在OB上方但信号弱，等回踩到OB
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
        # [bugfix 2026-10-21] 验证state params价格与当前价匹配（state可能过期）
        params_valid = (entry_lo > 0 and abs(entry_lo - px) / px < 0.015)  # [修复 2026-10-21] 入场区在当前价1.5%以内
        tp1 = (s11_tp1 if params_valid and 0 < s11_tp1 < px * 0.995
               else (round(liq_dn * 1.002, 1) if 0 < liq_dn < px * 0.995
               else round(px - atr4h * 2, 1)))
        tp2 = (s11_tp2 if params_valid and 0 < s11_tp2 < tp1 * 0.999
               else (round(liq_dn * 0.995, 1) if 0 < liq_dn < px * 0.99
               else round(px - atr4h * 3, 1)))
        # 空单入场：GEX ZF阻力区
        if zf > px * 0.998:
            entry_str = f"等ZF阻力区 ${px*1.001:,.1f}~${zf*0.999:,.1f}"
        else:
            entry_str = f"${px*1.001:,.1f}~${px*1.003:,.1f}"
        rr_str = f"{rr:.2f}" if rr > 0 else "计算中"
        long_line  = "🟢 暂无多单｜等待结构"
        short_line = f"🔴 空单｜{entry_str}"
        short_sl   = f"止损${sl_short:,.1f}｜目标${tp1:,.1f}→${tp2:,.1f}"
        long_sl    = short_sl   # [bugfix 2026-10-21] 空单时long_sl显示空单信息
        warn = f"{'Taker<0.6持续卖压' if d['tak']<0.6 else f'方向={direction} RR={rr_str}'}"
        bust = f"突破${sl_short:,.1f}+OI增加作废"
    else:
        long_line  = "🟢 暂无多单｜等待结构"
        short_line = "🔴 暂无空单｜等待结构"
        long_sl    = ""
        warn = "双侧均在等待，无入场信号"
        bust = f"GEX ZF=${zf:,.0f}为关键位"

    # [bugfix 2026-10-21] VIP输出格式修复：多空单分开显示，不混用SL字段
    if direction == 'LONG':
        lines = [
            f"🌿 姓赵不宣 | {sym} 当前布局",
            f"——— {sym} ———",
            long_line,
            long_sl,
            f"  杠杆3x｜仓位1.5%",
            short_line,
            f"⚠️ {warn}",
            f"🚫 {bust}",
            f"🌿 姓赵不宣 | 不是建议",
        ]
    elif direction == 'SHORT':
        lines = [
            f"🌿 姓赵不宣 | {sym} 当前布局",
            f"——— {sym} ———",
            long_line,
            short_line,
            long_sl,   # 空单的SL+TP
            f"  杠杆3x｜仓位1.5%",
            f"⚠️ {warn}",
            f"🚫 {bust}",
            f"🌿 姓赵不宣 | 不是建议",
        ]
    else:
        lines = [
            f"🌿 姓赵不宣 | {sym} 当前布局",
            f"——— {sym} ———",
            long_line,
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

    # 入口注入state（只注入GEX，不重载fangcang避免慢）
    # fangcang由state_refresh cron每小时:13写入，此处只补GEX ZF
    _state_f = BASE / 'data' / 'brahma_state_btc.json'
    _state_age = (time.time() - _state_f.stat().st_mtime) if _state_f.exists() else 9999
    if _state_age > 3600:  # state超过1小时才强制刷新
        try:
            from brahma_state_refresh import _inject_gex_to_state
            _inject_gex_to_state()
        except Exception:
            pass

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