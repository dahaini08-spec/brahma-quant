#!/usr/bin/env python3
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
]  # Phase1扩展 2026-07-18 苏摩111：9→50标的（TOP50 OI+成交量）
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

        # OI 1H变化
        oi_data = _fetch(f'{FAPI}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=3')
        oi_chg_1h = 0.0
        if oi_data and len(oi_data) >= 2:
            v_prev = float(oi_data[-2].get('sumOpenInterestValue', 0))
            v_curr = float(oi_data[-1].get('sumOpenInterestValue', 0))
            if v_prev > 0:
                oi_chg_1h = (v_curr - v_prev) / v_prev * 100

        return dict(
            sym=sym, px=px,
            rsi_1h=rsi_1h,
            bb_width=round(bb_width, 3),
            vol_ratio=round(vol_ratio, 2),
            r48h=r48h, s48h=s48h, ema20=ema20,
            oi_chg_1h=round(oi_chg_1h, 2),
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

        if status.startswith('SILENT'):
            silent_syms.append(f"{sym}({status})")
            pass  # [静默]
        elif events:
            # 有触发事件
            write_trigger(sym, events, data)
            state[f'{sym}_last_trigger'] = time.time()
            triggered_syms.append(sym)
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

    if not triggered_syms and not silent_syms:
        print("HEARTBEAT_OK")


if __name__ == '__main__':
    run()
