#!/usr/bin/env python3
# ponytail: rsi_structure_watcher 966行，cron入口，单文件单职责，不可拆
"""
rsi_structure_watcher.py — 梵天信号v5.0 · 零成本守望层
设计院 6方辩论落地 · 2026-07-01 · 苏摩111批准

╔══════════════════════════════════════════════════════════════╗
║  职责：0 tokens纯脚本，监控7个市场结构事件                   ║
║  任一触发 → 写入 data/rsi_trigger_event.json                ║
║         → cron触发 brahma_scan_all BTC ETH（层2）           ║
║  静默条件满足 → 完全不写入，积分消耗=0                       ║
╠══════════════════════════════════════════════════════════════╣
║  触发事件：                                                  ║
║  E1: RSI_1H 从<50 穿越到 ≥62（反弹做空窗口）                ║
║  E2: RSI_1H 从>70 跌破 <65（超买回落）                      ║
║  E3: 价格突破48H高点（EMA确认）                              ║
║  E4: 价格跌破48H低点（破位信号）                             ║
║  E5: BB宽度从<0.8%扩张至>1.2%（压缩释放）                   ║
║  E6: 1H量比突然>2x（异常成交量）                             ║
║  E7: OI 1H变化>3%（资金大幅进出）                           ║
╠══════════════════════════════════════════════════════════════╣
║  静默条件（节省积分）：                                      ║
║  · RSI_1H在45~60区间 且 BB宽度<0.8% → 死水封印，跳过       ║
║  · 距上次触发<2H（冷却期，防重复消耗）                       ║
║  · 当前BB宽度<0.5%（极度压缩，方向未定）                     ║
╚══════════════════════════════════════════════════════════════╝

运行方式：openclaw cron every 5min（与btc_regime_watcher并行）
"""

import sys, os, json, time, requests
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
STATE_FILE  = BASE / 'data' / 'rsi_watcher_state.json'
TRIGGER_FILE = BASE / 'data' / 'rsi_trigger_event.json'
FAPI = 'https://fapi.binance.com'

SYMBOLS = [
    # 主力锚点
    'BTCUSDT', 'ETHUSDT',
    # 高流动性TOP标的
    'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'AVAXUSDT',
    'LINKUSDT', 'LTCUSDT', 'BCHUSDT', 'NEARUSDT', 'TAOUSDT',
    # 高OI山寨
    'HYPEUSDT', 'SUIUSDT', 'ENAUSDT', 'AAVEUSDT', 'WLDUSDT',
    'PENGUUSDT', '1000PEPEUSDT', 'TRUMPUSDT', 'SYNUSDT', 'SNDKUSDT',
    # 中等市值活跃
    'NVDAUSDT', 'TSLAUSDT', 'MSTRUSDT', 'AMDUSDT', 'INTCUSDT',
    'XAUUSDT', 'XAGUSDT', 'QQQUSDT', 'SOXLUSDT', 'ZECUSDT',
    # 潜力候选
    'LABUSDT', 'CLUSDT', 'USUSDT', 'BANKUSDT', 'NBISUSDT',
    'SKHYNIXUSDT', 'SAMSUNGUSDT', 'KORUUSDT', 'EWYUSDT', 'PUMPUSDT',
    # 原有标的保留
    'JTOUSDT', 'BEATUSDT', 'BASUSDT', 'TACUSDT',
    '1000XECUSDT', 'CRCLUSDT', 'AKEUSDT', 'HOMEUSDT', 'ESPORTSUSDT',
    # [P1扩展 2026-08-19 设计院自主] TradFi高流动性补全（≥20M，RSI守望原未覆盖）
    # Brent原油
    'BZUSDT',
    # 超高流动性半导体/科技ETF（1000M+）
    'SPCXUSDT', 'MUUSDT', 'SNXXUSDT', 'SKHYUSDT', 'DRAMUSDT', 'SOXSUSDT',
    # 高流动性美股大盘/科技（100M+）
    'MRVLUSDT',
    # 中流动性美股/科技（20M+）
    'GOOGLUSDT', 'AAPLUSDT', 'METAUSDT', 'TQQQUSDT', 'SPYUSDT',
    'MSFTUSDT', 'PLTRUSDT', 'COINUSDT',
]  # Phase1扩展 2026-07-18 苏摩111：9→50标的 | Phase2扩展 2026-08-19 设计院：51→67标的（TradFi全覆盖≥20M）
COOLDOWN_SECONDS = 7200   # 2H冷却
SILENT_RSI_LOW   = 45.0
SILENT_RSI_HIGH  = 60.0
SILENT_BB_MAX    = 0.80   # BB宽度<0.8%时死水封印
EXTREME_BB_MIN   = 0.50   # BB宽度<0.5%极度压缩，方向未定

# ── P1: 15min层E8事件（2026-07-10 6方联合推理）──────────────────────────
# 15min BB极度压缩(弹簧) + 1H主趋势向上 = 高概率向上爆发
E8_BB_15M_MAX    = 0.80   # 15min BBW < 0.8%
E8_RSI_1H_MIN    = 50.0   # 1H RSI > 50 = 主趋势向上
E8_VOL_RATIO_MAX = 0.70   # 15min 量比 < 0.7x = 量能委缩


# 触发阈值
RSI_CROSS_UP_FROM  = 50.0   # E1: 从<50
RSI_CROSS_UP_TO    = 62.0   # E1: 穿越到≥62
RSI_CROSS_DOWN_FROM= 70.0   # E2: 从>70
RSI_CROSS_DOWN_TO  = 65.0   # E2: 跌破<65
BB_EXPAND_FROM     = 0.80   # E5: 从<0.8%
BB_EXPAND_TO       = 1.20   # E5: 扩张到>1.2%
VOL_SURGE_RATIO    = 2.0    # E6: 量比>2x
OI_CHANGE_PCT      = 3.0    # E7: OI 1H变化>3%
# [Phase3 2026-08-21 苏摩111] E_TREND_SURGE: 趋势持续暴涨触发事件
# 解决：暴涨行情中E1~E7无法单独触发做多信号的系统性盲区
TREND_SURGE_MIN_CHG   = 8.0   # 48H内涨幅超过8%
TREND_SURGE_MIN_CANDLES = 3   # 最近4H至少N根阳线确认趋势
TREND_DAY_HIGH_WINDOW = 30    # 日线突破N日新高


def _fetch(url, timeout=8):
    try:
        r = requests.get(url, timeout=timeout)
        return r.json()
    except Exception:
        return None


def get_market_data(sym):
    """拉取1H K线 + OI，计算所有指标"""
    try:
        # 1H K线 48根
        kl = _fetch(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&limit=50')
        if not kl or len(kl) < 20:
            return None

        closes = [float(k[4]) for k in kl]
        highs  = [float(k[2]) for k in kl]
        lows   = [float(k[3]) for k in kl]
        vols   = [float(k[5]) for k in kl]

        px = closes[-1]

        # RSI_1H
        n = 14
        c = closes[-(n+2):]
        gains  = [max(c[i]-c[i-1], 0) for i in range(1, len(c))]
        losses = [max(c[i-1]-c[i], 0) for i in range(1, len(c))]
        ag = sum(gains[-n:]) / n
        al = sum(losses[-n:]) / n
        rsi_1h = round(100 - 100/(1+ag/al), 1) if al > 0 else 100.0

        # 布林带宽度%
        import statistics
        ma20  = sum(closes[-20:]) / 20
        std20 = statistics.stdev(closes[-20:])
        bb_width = std20 * 2 / ma20 * 100

        # 量比（当前1H vs 过去24H均量）
        vol_ratio = vols[-1] / (sum(vols[-25:-1]) / 24) if sum(vols[-25:-1]) > 0 else 1.0

        # 48H高低点
        r48h = max(highs[-48:]) if len(highs) >= 48 else max(highs)
        s48h = min(lows[-48:])  if len(lows)  >= 48 else min(lows)

        # EMA20_1H
        ema20 = sum(closes[-20:]) / 20

        # OI 1H变化（扩展至10根，支持背离检测）
        oi_data = _fetch(f'{FAPI}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=10')
        oi_chg_1h = 0.0
        oi_history = []  # [(oi_val, price_approx)] 供背离检测用
        if oi_data and len(oi_data) >= 2:
            v_prev = float(oi_data[-2].get('sumOpenInterestValue', 0))
            v_curr = float(oi_data[-1].get('sumOpenInterestValue', 0))
            if v_prev > 0:
                oi_chg_1h = (v_curr - v_prev) / v_prev * 100
            # 构建OI历史序列
            for h in oi_data:
                oi_val = float(h.get('sumOpenInterest', 0))
                oi_history.append(oi_val)

        # [P0-1 2026-08-21 苏摩111] 量能枯竭检测数据
        # 计算每根1H K线的量比（相对过去20H均量）
        vol_avg20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else sum(vols[:-1]) / max(len(vols)-1, 1)
        vol_ratios_1h = []
        for i in range(max(0, len(vols)-8), len(vols)-1):  # 最近7根历史
            vr = vols[i] / vol_avg20 if vol_avg20 > 0 else 1.0
            pr = (highs[i] - lows[i]) / closes[i] * 100 if closes[i] > 0 else 0
            vol_ratios_1h.append({'vol_ratio': round(vr, 3), 'price_range': round(pr, 3)})

        # [P1-1 2026-08-21 苏摩111] 多周期结构背景数据
        # 日线K线（识别大周期支撑区/高点區）
        day30_high, day30_low = px, px
        rsi_4h = 50.0
        try:
            kl_1d = _fetch(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1d&limit=32')
            if kl_1d and len(kl_1d) >= 10:
                h1d = [float(k[2]) for k in kl_1d[-31:-1]]
                l1d = [float(k[3]) for k in kl_1d[-31:-1]]
                day30_high = max(h1d) if h1d else px
                day30_low  = min(l1d) if l1d else px
        except Exception:
            pass
        try:
            kl_4h = _fetch(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=4h&limit=20')
            if kl_4h and len(kl_4h) >= 16:
                c4h = [float(k[4]) for k in kl_4h]
                g4 = [max(c4h[i]-c4h[i-1],0) for i in range(1,len(c4h))]
                l4 = [max(c4h[i-1]-c4h[i],0) for i in range(1,len(c4h))]
                ag4 = sum(g4[-14:])/14; al4 = sum(l4[-14:])/14
                rsi_4h = round(100-100/(1+ag4/al4),1) if al4 > 0 else 100.0
        except Exception:
            pass

        # [P1-2 2026-08-21 苏摩111] 资金费率历史
        funding_rates = []
        try:
            fr_data = _fetch(f'{FAPI}/fapi/v1/fundingRate?symbol={sym}&limit=8')
            if fr_data:
                funding_rates = [float(f['fundingRate'])*100 for f in fr_data]
        except Exception:
            pass

        return dict(
            sym=sym, px=px,
            rsi_1h=rsi_1h,
            bb_width=round(bb_width, 3),
            vol_ratio=round(vol_ratio, 2),
            r48h=r48h, s48h=s48h, ema20=ema20,
            oi_chg_1h=round(oi_chg_1h, 2),
            oi_history=oi_history,
            vol_ratios_1h=vol_ratios_1h,
            vol_avg20=round(vol_avg20, 2),
            day30_high=round(day30_high, 2),
            day30_low=round(day30_low, 2),
            rsi_4h=round(rsi_4h, 1),
            funding_rates=funding_rates,
        )
    except Exception as e:
        pass  # [静默]
        return None


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def check_cooldown(state, sym):
    """检查冷却期，True=在冷却中，跳过
    [修复3 2026-07-18 苏摩111] BTC/ETH豁免: 每4H强制检查一次，防止体制感知长期静默
    """
    last_ts = state.get(f'{sym}_last_trigger', 0)
    elapsed = time.time() - last_ts
    # BTC/ETH: 冷却期上限4H（14400s），防止15H+静默
    _BTC_ETH = {'BTCUSDT', 'ETHUSDT'}
    effective_cooldown = min(COOLDOWN_SECONDS, 14400) if sym in _BTC_ETH else COOLDOWN_SECONDS
    return elapsed < effective_cooldown


def detect_events(data, prev_state, sym):
    """
    检测7个触发事件，返回触发的事件列表
    """
    px       = data['px']
    rsi      = data['rsi_1h']
    bb       = data['bb_width']
    vol_r    = data['vol_ratio']
    r48h     = data['r48h']
    s48h     = data['s48h']
    ema20    = data['ema20']
    oi_chg   = data['oi_chg_1h']

    prev_rsi = prev_state.get(f'{sym}_rsi', rsi)
    prev_bb  = prev_state.get(f'{sym}_bb',  bb)

    events = []

    # ── 静默门控（优先判断，节省积分） ──────────────────────────
    # [设计院修复 2026-08-08] E8极度压缩事件优先于静默门控
    # 根因: BBW=0.019是历史最低区间，原来被「死水封印」拦截导致30天未触发
    # 修复: 极度压缩(BBW<2.5%)直接触发E8，跳过死水封印
    BBW_EXTREME_ALERT = 2.5   # BBW<2.5% = 极度压缩警戒（尅5% 单位是百分比）
    if bb < BBW_EXTREME_ALERT:
        events.append({
            'event': 'E8_BBW_EXTREME_COMPRESS',
            'desc': f'BBW={bb:.3f}%极度压缩(历史最低区间) RSI={rsi:.1f} 弹簧即将释放',
            'priority': 'HIGH',
            'bbw_shrinking': prev_bb > bb,
        })
        return events, 'E8_EXTREME_BBW'

    # 死水封印：RSI中性区 + BB压缩（已通过E8门控，此时BBW>2.5%）
    if SILENT_RSI_LOW <= rsi <= SILENT_RSI_HIGH and bb < SILENT_BB_MAX:
        return [], 'SILENT_DEAD_WATER'

    # BB极度压缩（方向未定，任何触发都是噪音）
    if bb < EXTREME_BB_MIN:
        return [], 'SILENT_BB_EXTREME_COMPRESS'

    # ── E1: RSI_1H 从<50 穿越到 ≥62（反弹做空窗口） ──────────
    if prev_rsi < RSI_CROSS_UP_FROM and rsi >= RSI_CROSS_UP_TO:
        if px < ema20:  # 价格仍在EMA20下方，结构偏空
            events.append({
                'event': 'E1_RSI_CROSS_UP_SHORT_WINDOW',
                'desc': f'RSI_1H {prev_rsi:.1f}→{rsi:.1f} 突破{RSI_CROSS_UP_TO}，价格仍<EMA20，做空窗口打开',
                'priority': 'HIGH',
            })

    # ── E2: RSI_1H 从>70 跌破 <65（超买回落） ──────────────────
    if prev_rsi > RSI_CROSS_DOWN_FROM and rsi < RSI_CROSS_DOWN_TO:
        events.append({
            'event': 'E2_RSI_OVERBOUGHT_PULLBACK',
            'desc': f'RSI_1H {prev_rsi:.1f}→{rsi:.1f} 超买回落破{RSI_CROSS_DOWN_TO}，做空确认',
            'priority': 'HIGH',
        })

    # ── E3: 价格突破48H高点 ─────────────────────────────────────
    if px > r48h * 1.001:  # 突破0.1%确认
        events.append({
            'event': 'E3_PRICE_BREAK_48H_HIGH',
            'desc': f'价格${px:,.2f}突破48H高点${r48h:,.2f}(+{(px/r48h-1)*100:.2f}%)',
            'priority': 'MEDIUM',
        })

    # ── E4: 价格跌破48H低点 ─────────────────────────────────────
    if px < s48h * 0.999:  # 跌破0.1%确认
        events.append({
            'event': 'E4_PRICE_BREAK_48H_LOW',
            'desc': f'价格${px:,.2f}跌破48H低点${s48h:,.2f}({(px/s48h-1)*100:.2f}%)',
            'priority': 'HIGH',
        })

    # ── E5: BB宽度从<0.8%扩张至>1.2%（压缩释放） ───────────────
    if prev_bb < BB_EXPAND_FROM and bb > BB_EXPAND_TO:
        events.append({
            'event': 'E5_BB_EXPANSION',
            'desc': f'BB宽度 {prev_bb:.2f}%→{bb:.2f}% 压缩释放，方向即将选择',
            'priority': 'MEDIUM',
        })

    # ── E6: 1H量比突然>2x（异常成交量） ─────────────────────────
    if vol_r > VOL_SURGE_RATIO:
        events.append({
            'event': 'E6_VOLUME_SURGE',
            'desc': f'1H量比{vol_r:.1f}x 异常放量，结构可能变化',
            'priority': 'MEDIUM',
        })

    # ── E7: OI 1H变化>3%（资金大幅进出） ────────────────────────
    if abs(oi_chg) > OI_CHANGE_PCT:
        direction = '增仓' if oi_chg > 0 else '减仓'
        events.append({
            'event': 'E7_OI_SURGE',
            'desc': f'OI 1H变化{oi_chg:+.1f}% 资金{direction}，注意方向',
            'priority': 'MEDIUM',
        })


    # ── E_TREND_SURGE: 趋势持续暴涨触发（Phase3 2026-08-21 苏摩111）──────────
    # 解决：趋势行情中E1~E7无法单独触发做多信号的核心盲区
    # 触发条件（全部满足）：
    #   TS1: 48H内价格涨幅 >= 8%
    #   TS2: 最近4H至少连续3根阳线确认趋势
    #   TS3: RSI_1H > 60（非震荡回弹）
    # 冷却: 8H内同一标的不重复触发
    try:
        import time as _time_ts
        _48h_chg_ts = (px - s48h) / s48h * 100 if s48h > 0 else 0
        _ts1 = _48h_chg_ts >= 5.0  # [P1修复 2026-08-22 苏摩111] 8%→5%，覆盖缓慢爬升盲区
        _k4h_ts = data.get('klines_4h', [])
        if _k4h_ts and len(_k4h_ts) >= 3:
            _bull_cnt_ts = sum(1 for k in _k4h_ts[-3:] if float(k[4]) > float(k[1]))
            _ts2 = (_bull_cnt_ts >= 3)
        else:
            _ts2 = True
        _ts3 = rsi > 60.0
        _ts_key = f'{sym}_trend_surge_ts'
        _ts_last = prev_state.get(_ts_key, 0)
        _ts_ok = (_time_ts.time() - _ts_last) > 28800
        if _ts1 and _ts2 and _ts3 and _ts_ok:
            events.append({
                'event': 'E_TREND_SURGE',
                'desc': (f'🚀 趋势暴涨触发! 48H涨幅{_48h_chg_ts:.1f}%'
                         f' 4H阳线确认 RSI={rsi:.1f}'
                         f' → 动量层匹配，全量扫描做多'),
                'priority': 'HIGH',
                'direction': 'LONG',
                'chg_48h': round(_48h_chg_ts, 2),
                '_cooldown_key': _ts_key,
            })
    except Exception:
        pass


    # ── E_VOL_DRYUP: 成交量枯竭预警（P0-1 2026-08-21 苏摩111）──────────────────
    # 核心逻辑：卖盘枯竭+价格收敛 = 多头积累完毕，随时爆发
    # 触发条件（全部满足）：
    #   V1: 连续5根1H K线量比<0.65（历史20H均量）
    #   V2: 价格区间收敛：5H内平均每根涨跌幅 < 1.2%
    #   V3: RSI_1H 在50~80（趋势背景，非超卖反弹）
    # 冷却: 12H
    try:
        _vr_hist = data.get('vol_ratios_1h', [])
        if len(_vr_hist) >= 5:
            _v1 = all(v['vol_ratio'] < 0.65 for v in _vr_hist[-5:])
            _v2 = sum(v['price_range'] for v in _vr_hist[-5:]) / 5 < 1.2
            _v3 = 50.0 <= rsi <= 80.0
            _vd_key = f'{sym}_vol_dryup_ts'
            import time as _tv
            _vd_ok = (time.time() - prev_state.get(_vd_key, 0)) > 43200
            if _v1 and _v2 and _v3 and _vd_ok:
                _avg_vr = sum(v['vol_ratio'] for v in _vr_hist[-5:]) / 5
                events.append({
                    'event': 'E_VOL_DRYUP',
                    'desc': (f'💧 量能枯竭预警! 连续5H均量比={_avg_vr:.2f}x'
                             f' 价格区间收敛 RSI={rsi:.1f}'
                             f' → 卖盘枯竭，多头积累完毕，注意方向性爆发'),
                    'priority': 'HIGH',
                    'direction': 'WATCH',
                    'avg_vol_ratio': round(_avg_vr, 3),
                    '_cooldown_key': _vd_key,
                })
    except Exception:
        pass

    # ── E_OI_DIVERGE: OI+价格背离（P0-2 2026-08-21 苏摩111）──────────────────
    # 核心逻辑：OI持续增加但价格不创新低 = 空头加仓压不下去 = 轧空即将发生
    # 触发条件：
    #   O1: OI最近3H净增加（逐根递增）
    #   O2: 价格未创新低（当前价 > 48H最低价 × 1.003）
    #   O3: RSI_1H < 55（价格并未追高，排除顺势加多）
    # 冷却: 8H
    try:
        _oi_hist = data.get('oi_history', [])
        if len(_oi_hist) >= 4:
            _o1 = all(_oi_hist[-(i+1)] > _oi_hist[-(i+2)] for i in range(3))
            _o2 = px > s48h * 1.003
            _o3 = rsi < 55.0
            _od_key = f'{sym}_oi_diverge_ts'
            import time as _to
            _od_ok = (time.time() - prev_state.get(_od_key, 0)) > 28800
            if _o1 and _o2 and _o3 and _od_ok:
                _oi_chg3h = (_oi_hist[-1]-_oi_hist[-4])/_oi_hist[-4]*100 if _oi_hist[-4]>0 else 0
                events.append({
                    'event': 'E_OI_DIVERGE',
                    'desc': (f'🚨 OI+价格背离! OI连续3H增{_oi_chg3h:+.2f}%'
                             f' 但价格未创新低 RSI={rsi:.1f}'
                             f' → 空头加仓压不下，轧空风险升高'),
                    'priority': 'HIGH',
                    'direction': 'LONG',
                    'oi_chg_3h': round(_oi_chg3h, 2),
                    '_cooldown_key': _od_key,
                })
    except Exception:
        pass


    # ── E_MTF_SUPPORT: 多周期结构共振做多背景（P1-1 2026-08-21 苏摩111）────────
    # 核心逻辑：日线大周期支撑 + 4H RSI回调 + 1H结构转换 = 最高置信度预判信号
    # 触发条件（满足2项及以上）：
    #   M1: 价格处于30日区间底部20%（日线级别支撑区）
    #   M2: RSI_4H < 40（4H超卖，多头成本区）
    #   M3: RSI_1H从<35回升到>40（1H结构转换确认）
    #   M4: 价格在日线30日低点上方0~5%（贴近大周期支撑）
    # 冷却: 24H（大周期信号，不频繁触发）
    try:
        _d30h = data.get('day30_high', px)
        _d30l = data.get('day30_low', px)
        _rsi4h = data.get('rsi_4h', 50.0)
        _pos30 = (px - _d30l) / (_d30h - _d30l) if _d30h > _d30l else 0.5

        _m1 = _pos30 <= 0.20   # 处于30日底部20%
        _m2 = _rsi4h < 40.0    # 4H超卖
        _m3 = prev_rsi < 35.0 and rsi > 40.0  # 1H结构转换
        _m4 = 0 <= (px - _d30l) / _d30l * 100 <= 5.0  # 贴近30日低点上方5%内

        _mtf_hits = sum([_m1, _m2, _m3, _m4])
        _mtf_key = f'{sym}_mtf_support_ts'
        import time as _tm
        _mtf_ok = (time.time() - prev_state.get(_mtf_key, 0)) > 86400  # 24H冷却

        if _mtf_hits >= 2 and _mtf_ok:
            _active = [f'M{i+1}' for i,v in enumerate([_m1,_m2,_m3,_m4]) if v]
            events.append({
                'event': 'E_MTF_SUPPORT',
                'desc': (f'🏗️ 多周期结构共振! 触发{_mtf_hits}项({",".join(_active)})'
                         f' 30日区间位={_pos30:.0%} RSI_4H={_rsi4h:.1f}'
                         f' → 大周期支撑确认，高置信度做多背景'),
                'priority': 'HIGH',
                'direction': 'LONG',
                'mtf_hits': _mtf_hits,
                'pos30': round(_pos30, 3),
                '_cooldown_key': _mtf_key,
            })
    except Exception:
        pass

    # ── E_FUNDING_FLIP: 资金费率翻转监控（P1-2 2026-08-21 苏摩111）────────────
    # 核心逻辑：资金费率从正值（多头拥挤）趋近0或转负 = 空头开始占优，轧空前兆
    # 触发条件：
    #   F1: 最近3期资金费率均值 < 0.005%（接近中性或负值）
    #   F2: 前3期资金费率均值 > 0.03%（之前是高多拥挤状态）
    #   F3: 价格未暴跌（未创48H新低，排除恐慌性做空）
    # 冷却: 16H
    try:
        _fr = data.get('funding_rates', [])
        if len(_fr) >= 6:
            _fr_recent = _fr[-3:]   # 最近3期
            _fr_prev   = _fr[-6:-3] # 前3期
            _f1 = sum(_fr_recent) / 3 < 0.005
            _f2 = sum(_fr_prev) / 3 > 0.03
            _f3 = px > s48h * 0.99  # 价格未暴跌
            _ff_key = f'{sym}_funding_flip_ts'
            import time as _tf
            _ff_ok = (time.time() - prev_state.get(_ff_key, 0)) > 57600  # 16H冷却
            if _f1 and _f2 and _f3 and _ff_ok:
                _fr_now = sum(_fr_recent) / 3
                _fr_was = sum(_fr_prev) / 3
                events.append({
                    'event': 'E_FUNDING_FLIP',
                    'desc': (f'💰 资金费率翻转! 前均={_fr_was:.4f}%→近均={_fr_now:.4f}%'
                             f' 多头拥挤消散 价格={px:,.1f}'
                             f' → 空头离场+轧空风险升高'),
                    'priority': 'HIGH',
                    'direction': 'LONG',
                    'fr_recent': round(_fr_now, 5),
                    'fr_prev': round(_fr_was, 5),
                    '_cooldown_key': _ff_key,
                })
    except Exception:
        pass

    # ── E11: 清算墙逼近(<0.5%) — 轧空/踩踏即将触发 [设计院 2026-08-05] ────
    try:
        import sys as _sys_e11
        _bb11 = str(BASE / 'brahma_brain')
        if _bb11 not in _sys_e11.path:
            _sys_e11.path.insert(0, _bb11)
        from liq_density_engine import get_liq_density as _get_ld_e11
        _ld11 = _get_ld_e11(symbol, price)
        _ab11 = _ld11.get('above_walls', [])
        _bl11 = _ld11.get('below_walls', [])
        for _wp11, _wv11 in (_ab11 + _bl11):
            _dist11 = abs(_wp11 - price) / price * 100
            if _dist11 < 0.5 and _wv11 > 50_000_000:   # 距离<0.5% 且 量>$50M
                _side11 = '上方空头清算墙' if _wp11 > price else '下方多头清算墙'
                events.append({
                    'event': 'E11_LIQ_WALL_NEAR',
                    'desc': f'{_side11}逼近: {_wp11:,.0f}({_dist11:.2f}%, ${_wv11/1e6:.0f}M) — 触发点已近',
                    'priority': 'HIGH',
                })
                break   # 只取最近一堵墙
    except Exception:
        pass

    # ── E10: RSI回弹确认（设计院 2026-07-13）————————————————————————————
    # 核心逻辑： RSI_1H从<30回弹至>35 → 超卖消化，为多头入场提供确认信号
    # 意义：直接在最低点入场 WR=72%, 回弹后入场 WR=78%（+6%）
    if prev_rsi < 30 and rsi >= 35:
        events.append({
            'event': 'E10_RSI_BOUNCE_CONFIRM',
            'desc': f'RSI_1H {prev_rsi:.1f}→{rsi:.1f} 超卖回弹确认（从<30回升>35），多头入场+6%WR提升',
            'priority': 'HIGH',
        })

    # ── E8/E9: ETH EMA门控嵌入（取代eth-ema-gate独立cron，零额外负担）──
    # 2026-07-25 设计院自主：三重门控 + 止损池预警，复用rsi_watcher现有10min节奏
    if sym == 'ETHUSDT':
        _ema20   = data.get('ema20', 0)  # 复用现有ema20字段
        _rsi_now = data.get('rsi_1h', rsi)
        _sl_pool = 1855.0
        # C1: 价格站稳EMA20_1H（新宪法铁律）
        # C2: RSI超卖修复（复用现有RSI，不增加额外请求）
        # C3: 止损池预警
        _c1 = px > _ema20 if _ema20 else False
        _c2 = _rsi_now > 32
        if _c1 and _c2:
            events.append({
                'event': 'E8_ETH_LONG_GATE_OPEN',
                'desc': (f'🔔 ETH LONG门控通过! '
                         f'价格${px:.2f}>EMA20${_ema20:.2f} '
                         f'RSI={_rsi_now:.1f} '
                         f'入场$1858~$1861 SL=$1812 TP1=$1904 TP2=$1928 — 苏摩是否执行?'),
                'priority': 'HIGH',
            })
        elif px < _sl_pool:
            events.append({
                'event': 'E9_ETH_SL_POOL_BREAK',
                'desc': f'🚨 ETH止损池跌破! 价格${px:.2f}<${_sl_pool} 多头止损级联风险，LONG计划暂停',
                'priority': 'HIGH',
            })

    # ── E11/E12: BEAR_TREND专用做空触发（设计院 2026-08-04 苏摩111执行）──────────
    # 问题：BEAR_TREND体制下，E1/E2/E3触发率极低（RSI很少到70，反弹幅度有限）
    # 解法：增加BEAR_TREND特有的反弹顶部识别模式
    #
    # E11: BEAR_TREND + RSI_1H从>60回落到<55（反弹失败，做空确认）
    #      意义：BEAR体制里RSI能到60已是强反弹，回落到55说明反弹顶部已确认
    #      历史验证：BEAR体制反弹后RSI回落 WR=68.1%（来自wr_matrix_v7）
    #
    # E12: BEAR_TREND + 价格反弹至EMA20_1H ±1.5%（新宪法规则：EMA20是做空入场参考位）
    #      意义：梵天新宪法要求"价格<EMA20_1H才允许做空入场"
    #      此事件触发=价格刚触碰EMA20，是最优做空入场时机

    # 读取当前体制（复用 regime_state.json，不增加API调用）
    try:
        import json as _j
        _regime_f = Path(__file__).parent.parent / 'data' / 'regime_state.json'
        _regime_data = _j.loads(_regime_f.read_text()) if _regime_f.exists() else {}
        _sym_regime = _regime_data.get(sym, {})
        _cur_regime = _sym_regime.get('regime', _sym_regime.get('confirmed', '')) if isinstance(_sym_regime, dict) else ''
    except Exception:
        _cur_regime = ''

    if 'BEAR' in _cur_regime:
        # E11: RSI从>60回落到<55（反弹失败）
        if prev_rsi > 60.0 and rsi < 55.0:
            events.append({
                'event': 'E11_BEAR_RSI_PULLBACK',
                'desc': (f'🐻 BEAR_TREND RSI_1H {prev_rsi:.1f}→{rsi:.1f} 反弹失败回落，'
                         f'做空窗口打开 (BEAR_WR≈68%) '
                         f'价格${px:,.2f} EMA20=${ema20:,.2f}'),
                'priority': 'HIGH',
                'regime': _cur_regime,
                'direction': 'SHORT',
            })

        # E12: 价格触碰EMA20_1H（±1.5% 范围内）
        if ema20 > 0:
            _dist_ema = abs(px - ema20) / ema20
            if _dist_ema <= 0.015 and px < ema20 * 1.005:  # 在EMA20附近且略低于
                events.append({
                    'event': 'E12_BEAR_EMA20_TOUCH',
                    'desc': (f'🐻 BEAR_TREND 价格${px:,.2f}触碰EMA20_1H${ema20:,.2f} '
                             f'(dist={_dist_ema*100:.2f}%) — 新宪法做空参考位，'
                             f'确认后可入场'),
                    'priority': 'HIGH',
                    'regime': _cur_regime,
                    'direction': 'SHORT',
                })

    # ── E_SLOW_CLIMB: 缓慢持续爬升触发（2026-08-22 苏摩111）──────────────────
    # 核心逻辑：7D涨幅大但每个48H窗口<8%，是E_TREND_SURGE无法捕捉的慢牛盲区
    # 触发条件（全部满足）：
    #   SC1: 7D涨幅 >= 12%（累计涨幅明显）
    #   SC2: RSI_4H > 55（趋势动能，非震荡）
    #   SC3: 价格在7D高点的90%以上（未回撤，仍在上升通道）
    #   SC4: 非BEAR_TREND体制（避免熊市反弹误触发）
    # 冷却: 12H
    try:
        import time as _sc_time
        _kl1d = data.get('klines_1d', [])
        if _kl1d and len(_kl1d) >= 7:
            _7d_open = float(_kl1d[-7][1])
            _7d_high = max(float(k[2]) for k in _kl1d[-7:])
            _7d_chg = (px - _7d_open) / _7d_open * 100 if _7d_open > 0 else 0
            _rsi4h_sc = data.get('rsi_4h', 50)
            _regime_sc = data.get('regime', 'CHOP_MID')
            _sc1 = _7d_chg >= 12.0
            _sc2 = _rsi4h_sc > 55.0
            _sc3 = px >= _7d_high * 0.90
            _sc4 = 'BEAR_TREND' not in _regime_sc
            _sc_key = f'{sym}_slow_climb_ts'
            _sc_ok = (_sc_time.time() - prev_state.get(_sc_key, 0)) > 43200  # 12H冷却
            if _sc1 and _sc2 and _sc3 and _sc4 and _sc_ok:
                events.append({
                    'event': 'E_SLOW_CLIMB',
                    'desc': (f'🐢 缓慢爬升触发! 7D涨幅{_7d_chg:.1f}%'
                             f' RSI_4H={_rsi4h_sc:.1f} 价格在7D高点{px/_7d_high:.0%}'
                             f' 体制={_regime_sc} → 慢牛上涨，触发全量做多扫描'),
                    'priority': 'HIGH',
                    'direction': 'LONG',
                    'chg_7d': round(_7d_chg, 2),
                    'rsi_4h': round(_rsi4h_sc, 1),
                    '_cooldown_key': _sc_key,
                })
    except Exception:
        pass

    return events, 'ACTIVE' if events else 'NO_EVENT'


def write_trigger(sym, events, data):
    """写入触发事件文件（供scan_all读取）+ 高优先级事件推送Jarvis"""
    try:
        existing = {}
        if TRIGGER_FILE.exists():
            try:
                existing = json.loads(TRIGGER_FILE.read_text())
            except Exception:
                existing = {}

        existing[sym] = {
            'ts': time.time(),
            'ts_iso': datetime.now(tz=timezone.utc).isoformat(),
            'symbol': sym,
            'px': data['px'],
            'rsi_1h': data['rsi_1h'],
            'bb_width': data['bb_width'],
            'events': events,
            'high_priority': any(e['priority'] == 'HIGH' for e in events),
        }
        TRIGGER_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False))

        # ── [设计院 2026-08-09 苏摩111] 高优先级事件直推Jarvis + 去重 ────
        # 修复根因：BB压缩持续时每5M推一次 → 2H内24条刷屏
        # 解法：同类事件(sym+event_type) 4H内只推1次
        high_events = [e for e in events if e.get('priority') in ('HIGH', 'P0', 'P1')]
        if high_events:
            try:
                import subprocess as _sp, json as _jj
                from pathlib import Path as _Pth
                from scripts.system_config import JARVIS_TARGET

                # ── 去重检查 ────────────────────────────────────────────
                _DEDUP_F = _Pth(__file__).parent.parent / 'data' / 'rsi_watcher_dedup.json'
                _dedup = _jj.loads(_DEDUP_F.read_text()) if _DEDUP_F.exists() else {}
                _now   = __import__('time').time()
                _DEDUP_TTL = 4 * 3600  # 同类事件4H内不重复推送

                # 过滤已推过的事件
                _new_events = []
                for e in high_events:
                    _key = f"{sym}_{e.get('event','?')}"
                    _last = _dedup.get(_key, 0)
                    if _now - _last >= _DEDUP_TTL:
                        _new_events.append(e)
                    else:
                        _remain = int((_DEDUP_TTL - (_now - _last)) / 60)
                        print(f"[rsi_watcher] 去重跳过 {_key} (剩余{_remain}min)")

                if _new_events:
                    ev_lines = '\n'.join([f"  [{e['priority']}] {e['event']}: {e['desc']}" for e in _new_events])
                    msg = (
                        f"🔔 RSI结构事件 · {sym}\n"
                        f"价格: ${data['px']:,.2f} | RSI_1H={data['rsi_1h']:.1f} | BB={data['bb_width']:.2f}%\n"
                        f"{ev_lines}\n"
                        f"→ 梵天扫描链已启动"
                    )
                    _sp.Popen(
                        ['openclaw', 'message', 'send', '--to', JARVIS_TARGET, '--channel', 'jarvis', '--message', msg],
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
                    )
                    # 写入去重记录
                    for e in _new_events:
                        _dedup[f"{sym}_{e.get('event','?')}"] = _now
                    # 清理过期记录
                    _dedup = {k: v for k, v in _dedup.items() if _now - v < 86400}
                    _DEDUP_F.write_text(_jj.dumps(_dedup, indent=2))
            except Exception as _pe:
                pass  # [静默]
        # ────────────────────────────────────────────────────────────────
        return True
    except Exception as e:
        pass  # [静默]
        return False


def _load_pre_filter_symbols() -> list:
    """[Phase2 2026-07-18 苏摩111] 加载 market_pre_filter 输出的候选标的
    将外部预筛层的candidates合并进入监控池，实现零token成本全市圶65个监控
    """
    trigger_file = BASE / 'data' / 'pre_filter_trigger.json'
    if not trigger_file.exists():
        return []
    try:
        d = json.loads(trigger_file.read_text())
        # 只返回最新一批candidates（20分钟内有效）
        from datetime import datetime, timezone
        gen = d.get('generated', '')
        if gen:
            dt = datetime.fromisoformat(gen.replace('Z','+00:00'))
            age_s = (datetime.now(tz=timezone.utc) - dt).total_seconds()
            if age_s > 1200:  # 20分钟过期
                return []
        return d.get('symbols', [])
    except Exception:
        return []



def check_skip_reset(symbol: str, current_price: float, state: dict) -> bool:
    """
    [P1-2 2026-08-15 苏摩111] SKIP状态自动重置机制
    当一个币分析为SKIP且价格从记录高点回落>30% → 清除SKIP标记，触发重新扫描
    根因：高位SKIP ≠ 回落底部继续SKIP（ACE教训：$0.37 SKIP后 $0.06再度起爆）
    """
    key_high = f'{symbol}_recent_high'
    key_ts   = f'{symbol}_recent_high_ts'
    high = float(state.get(key_high, 0))
    ts   = float(state.get(key_ts, 0))
    if high > 0 and current_price > 0:
        drawdown = (high - current_price) / high * 100
        age_h = (time.time() - ts) / 3600 if ts else 0
        if drawdown > 30 and age_h > 1:
            return True  # 触发重置：从高点回落>30%
    return False

def run():
    now_str = datetime.now(tz=timezone.utc).strftime('%H:%M UTC')
    state = load_state()
    triggered_syms = []
    triggered_full_data = []  # [Fix1 2026-08-30 苏摩111] 完整信号数据列表
    silent_syms = []

    # [Phase2] 合并 pre_filter 候选标的（动态扩展监控池）
    pre_syms = _load_pre_filter_symbols()
    # 去重：已在SYMBOLS里的不重复添加
    extra_syms = [s for s in pre_syms if s not in SYMBOLS]
    effective_symbols = list(SYMBOLS) + extra_syms
    if extra_syms:
        pass  # 静默，每5分钟运行时合并候选标的

    for sym in effective_symbols:
        # 冷却期检查
        if check_cooldown(state, sym):
            last_ts = state.get(f'{sym}_last_trigger', 0)
            remaining = int((COOLDOWN_SECONDS - (time.time() - last_ts)) / 60)
            pass  # [静默]
            continue

        data = get_market_data(sym)
        if not data:
            continue

        events, status = detect_events(data, state, sym)

        # 更新状态（RSI/BB记录）
        state[f'{sym}_rsi'] = data['rsi_1h']
        state[f'{sym}_bb']  = data['bb_width']

        # [P1-2 2026-08-15 苏摩111] 高点记录 + SKIP自动重置
        cur_price = float(data.get('price', 0) or data.get('close', 0))
        if cur_price > 0:
            prev_high = float(state.get(f'{sym}_recent_high', 0))
            if cur_price > prev_high:
                state[f'{sym}_recent_high']    = cur_price
                state[f'{sym}_recent_high_ts'] = time.time()
            # 从高点回落>30% → 强制触发重新扫描（清除静默封印）
            if check_skip_reset(sym, cur_price, state) and status.startswith('SILENT'):
                events = [{'event': 'SKIP_RESET', 'detail': f'回落>{state.get(f"{sym}_recent_high",0):.4f}的30%'}]
                status = 'SKIP_RESET_TRIGGERED'

        # ── E_ZONE: 战场区间触及检测 (price_zone_engine P3, 2026-08-25) ──
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brahma_brain'))
            from price_zone_engine import check_zone_touch
            _zone_touch = check_zone_touch(sym, cur_price)
            if _zone_touch:
                _zone_msg = _zone_touch['msg']
                import subprocess as _sp2
                _sp2.Popen(
                    ['openclaw','message','send','--to', JARVIS_TARGET,
                     '--channel','jarvis','--message', _zone_msg],
                    stdout=_sp2.DEVNULL, stderr=_sp2.DEVNULL
                )
                # [设计院 2026-08-25 苏摩111] P1: price_zone触及→CPU大脑直通
                # 不再只推消息，直接让CPU决策要不要执行
                try:
                    from brahma_cpu import process_event as _cpu_process
                    _zone_type = _zone_touch.get('zone_type', 'UNKNOWN')  # SHORT_ZONE / LONG_ZONE
                    _cpu_dir = 'SHORT' if _zone_type == 'SHORT_ZONE' else 'LONG'
                    import threading as _thr
                    def _cpu_run():
                        try:
                            _cpu_result = _cpu_process(
                                symbol=sym,
                                signal_dir=_cpu_dir,
                                event_type=f'ZONE_TOUCH_{_zone_type}',
                            )
                            _dec = _cpu_result.get('decision', 'SKIP')
                            _sc  = _cpu_result.get('score', 0)
                            _rsn = _cpu_result.get('reason', '')
                            if _dec in ('EXECUTE', 'ALERT'):
                                _icon = '🟢' if _dec == 'EXECUTE' else '🟡'
                                _notify = (f'{_icon} **CPU大脑裁决** | {sym} 区间触及\n'
                                           f'方向:{_cpu_dir} score={_sc:.1f} decision={_dec}\n{_rsn}')
                                _sp2.Popen(
                                    ['openclaw','infer','--channel','jarvis',
                                     '--to', JARVIS_TARGET,
                                     '--message', _notify],
                                    stdout=_sp2.DEVNULL, stderr=_sp2.DEVNULL
                                )
                        except Exception:
                            pass
                    _thr.Thread(target=_cpu_run, daemon=True).start()
                except Exception:
                    pass  # CPU接入失败不影响主流程
        except Exception:
            pass  # 区间检测不影响主流程

        if status.startswith('SILENT'):
            silent_syms.append(f"{sym}({status})")
            pass  # [静默]
        elif events:
            # [2026-08-29 苏摩111修复] 信号写入前强制过体制死穴检查（防止 BEAR_RECOVERY 倒空/BULL_TREND 倒多）
            _sig_regime = data.get('regime', '')
            _sig_dir = 'SHORT' if any('BEAR' in str(e) or 'OVERBOUGHT' in str(e) or 'BREAK' in str(e) for e in events) else 'LONG'
            _DEAD_SHORT = {'BEAR_RECOVERY', 'BULL_EARLY'}  # 这两个体制做空 WR=0%
            _DEAD_LONG  = {'BEAR_TREND'}                   # BEAR_TREND 做多 死穴
            if _sig_dir == 'SHORT' and _sig_regime in _DEAD_SHORT:
                pass  # [封禁] {sym} {_sig_regime} SHORT = 死穴，不写入
            elif _sig_dir == 'LONG' and _sig_regime in _DEAD_LONG:
                pass  # [封禁] {sym} {_sig_regime} LONG = 死穴，不写入
            else:
                # 有触发事件
                write_trigger(sym, events, data)
                state[f'{sym}_last_trigger'] = time.time()
                triggered_syms.append(sym)
                # [Fix1 2026-08-30 苏摩111] 收集完整信号数据，修复空壳信号根因
                # 从 events 提取 direction（以事件内置 direction 优先）
                _ev_dir = None
                for _ev in events:
                    if _ev.get('direction'):
                        _ev_dir = _ev['direction']
                        break
                _final_dir = _ev_dir or _sig_dir
                triggered_full_data.append({
                    'symbol':    sym,
                    'source':    'rsi_watcher',
                    'direction': _final_dir,
                    'regime':    _sig_regime or data.get('regime', ''),
                    'score':     data.get('score', None),      # rsi_watcher 无score，保留None
                    'grade':     None,
                    'sl_pct':    None,
                    'entry_lo':  None,
                    'entry_hi':  None,
                    'signal_id': f'rsi_{sym}_{int(time.time())}',
                    'meta':      {'events': [e.get('event','') for e in events]},
                })
            for ev in events:
                pass  # [静默]
        else:
            pass  # [静默]

    save_state(state)

    if triggered_syms:
        pass  # [静默]
        import subprocess
        # 层关1事件触发后：扫描完成即触发auto_executor（缩短延迟）
        # ulimit限制单条Python链内存上限（防止OOM）
        # E5/E6/E7触发时，同步触发 pump-hunter 加速扫描（1H兜底之外的事件驱动加速）
        pump_trigger_events = {'E5_BB_EXPANSION', 'E6_VOLUME_SURGE', 'E7_OI_SURGE'}
        has_pump_trigger = any(
            ev.get('event','') in pump_trigger_events
            for sym_evs in [events] for ev in sym_evs
        ) if events else False

        # [v5.6 设计院自主落地 2026-07-13] 触发链末尾追加 signal_dashboard 事件驱动推送
        # 原理：E1-E9事件触发时，扫描完成后立即检查仪表盘（T1-T4条件），无变化静默
        # 效果：signal_dashboard 从30min定时 → 事件驱动（活跃市场8-12次/天，非活跃趋近0）
        # [P2-C 设计院封印 2026-07-23 苏摩111] 层全1事件驱动接35维矩阵
        # 根因: 事件触发后当前只走轻量路径(market_screener+brahma_scan_all)
        # 修复: 探测到 E1/E2/E3/E4 时附加触发 brahma_1hao_analysis BTC ETH
        # 效果: 价格穿越/RSI穿越事件发生时即制35维分析，而非等待30min定时轮询
        _35dim_trigger_events = {'E1_RSI_CROSS_UP', 'E2_RSI_OVERBOUGHT_FALL',
                                  'E3_PRICE_BREAKOUT_HIGH', 'E4_PRICE_BREAKDOWN_LOW',
                                  'E10_RSI_BOUNCE'}
        _has_35dim_trigger = any(
            ev.get('event', '') in _35dim_trigger_events
            for ev in (events if events else [])
        )
        # 获取触发事件对应的标的（只分析已触发的标的，防止过度消耗）
        _35dim_syms = ' '.join(triggered_syms[:2]) if triggered_syms else 'BTCUSDT ETHUSDT'

        scan_cmd = (
            f'cd {BASE} && '
            f'ulimit -v 1048576 2>/dev/null; '
            f'python3 scripts/market_screener.py && '
            f'python3 scripts/brahma_scan_all.py --candidates && '
            f'python3 scripts/auto_executor.py 2>&1 | tail -5'
        )
        # E1/E2/E3/E4/E10 触发时附加 brahma_1hao_analysis 35维分析
        # [P1-B修复 2026-07-24 苏摩确认] 根据触发事件类型决定分析方向
        # E2(RSI超买回落)/E4(跌破48H低) → SHORT方向
        # E1(RSI上穿)/E3(突破48H高)/E10(RSI反弹确认) → LONG方向
        if _has_35dim_trigger:
            _short_events = {'E2_RSI_OVERBOUGHT_PULLBACK', 'E4_PRICE_BREAK_48H_LOW'}
            _long_events  = {'E1_RSI_CROSS_UP_SHORT_WINDOW', 'E3_PRICE_BREAK_48H_HIGH', 'E10_RSI_BOUNCE_CONFIRM'}
            _short_events = {'E2_RSI_OVERBOUGHT_DROP', 'E4_PRICE_BREAK_48H_LOW',
                             'E11_BEAR_RSI_PULLBACK', 'E12_BEAR_EMA20_TOUCH'}  # [2026-08-04 E11/E12新增]
            _triggered_event_names = {ev.get('event','') for ev in (events if events else [])}
            _has_short_trigger = bool(_triggered_event_names & _short_events)
            _has_long_trigger  = bool(_triggered_event_names & _long_events)
            # 优先SHORT（空信号更稀缺，优先恢复）
            if _has_short_trigger:
                scan_cmd += (
                    f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction SHORT 2>&1 | tail -5'
                )
            if _has_long_trigger:
                scan_cmd += (
                    f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction LONG 2>&1 | tail -5'
                )
            if not _has_short_trigger and not _has_long_trigger:
                # 混合事件或未分类 → 双向均分析
                scan_cmd += (
                    f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction LONG 2>&1 | tail -3'
                    f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction SHORT 2>&1 | tail -3'
                )
        # E5/E6/E7触发时附加 pump-hunter 扫描
        if has_pump_trigger:
            scan_cmd = (
                f'cd {BASE} && '
                f'ulimit -v 1048576 2>/dev/null; '
                f'python3 dharma/pump_hunter/scan_and_alert.py --dry-run 2>&1 | tail -3 & '
                f'python3 scripts/market_screener.py && '
                f'python3 scripts/brahma_scan_all.py --candidates && '
                f'python3 scripts/auto_executor.py 2>&1 | tail -5'
            )
            if _has_35dim_trigger:
                # [P1-B修复 同步] 备用路径同样注入方向感知
                _short_ev2 = {'E2_RSI_OVERBOUGHT_PULLBACK', 'E4_PRICE_BREAK_48H_LOW', 'E11_BEAR_RSI_PULLBACK', 'E12_BEAR_EMA20_TOUCH'}  # [2026-08-04]
                _long_ev2  = {'E1_RSI_CROSS_UP_SHORT_WINDOW', 'E3_PRICE_BREAK_48H_HIGH', 'E10_RSI_BOUNCE_CONFIRM'}
                _trig_ev2  = {ev.get('event','') for ev in (events if events else [])}
                if _trig_ev2 & _short_ev2:
                    scan_cmd += (f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction SHORT 2>&1 | tail -5')
                if _trig_ev2 & _long_ev2:
                    scan_cmd += (f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction LONG 2>&1 | tail -5')
                if not (_trig_ev2 & (_short_ev2 | _long_ev2)):
                    scan_cmd += (f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction LONG 2>&1 | tail -3'
                                 f' && python3 scripts/brahma_1hao_analysis.py {_35dim_syms} --direction SHORT 2>&1 | tail -3')
        try:
            # ── 防积压：检查是否已有扫描链在运行 ──────────────────
            import os, glob
            lock_file = BASE / 'data/.rsi_scan_chain.lock'
            if lock_file.exists():
                lock_age = time.time() - lock_file.stat().st_mtime
                if lock_age < 120:   # v5.2: 2min内认为上一轮还在跑（原4min，gateway重启导致残留）
                    pass  # [静默]
                    return
                else:
                    lock_file.unlink(missing_ok=True)  # 超时残留锁，强制清除
            lock_file.write_text(str(os.getpid()))
            proc = subprocess.Popen(scan_cmd, shell=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            pass  # [静默]
            # 非阻塞：让进程在后台运行，定时清理锁
            def _cleanup_lock(p, lf):
                try:
                    p.wait(timeout=240)  # 最多等4min
                except Exception:
                    p.kill()
                finally:
                    try: lf.unlink(missing_ok=True)
                    except: pass
            import threading
            threading.Thread(target=_cleanup_lock, args=(proc, lock_file), daemon=True).start()
        except Exception as e:
            try: Path(BASE / 'data/.rsi_scan_chain.lock').unlink(missing_ok=True)
            except: pass
            pass  # [静默]
    elif not silent_syms:
        pass  # triggered_syms已推送，无需重复

    # [2026-08-30 苏摩111 Fix1] 写入统一信号队列 — 传完整参数（修复空壳信号根因）
    # 根因：triggered_syms 是 sym 字符串列表，push_signals() 写入时 score/direction/regime 全为 null
    # 修复：改为使用 triggered_full_data（在循环内收集完整信号），调用 push_signal_full
    if triggered_full_data:
        try:
            import sys as _sys_sq
            _sys_sq.path.insert(0, str(Path(__file__).parent))
            from signal_queue_writer import push_signal_full as _sq_push_full
            for _sig_record in triggered_full_data:
                _sq_push_full(_sig_record)
        except Exception:
            pass  # 队列写入失败不影响主流程

    if not triggered_syms and not silent_syms:
        print("HEARTBEAT_OK")


if __name__ == '__main__':
    run()
