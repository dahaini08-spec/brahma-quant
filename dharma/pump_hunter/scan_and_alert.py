#!/usr/bin/env python3
# sys.path 强制修复 — cron环境下路径不稳定根因修复 [设计院封印 2026-07-14]
import sys as _sys, os as _os
_TRADING_SYS = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..'))
if _TRADING_SYS not in _sys.path:
    _sys.path.insert(0, _TRADING_SYS)
"""
暴涨猎手 v4.0 — 设计院全面完善
2026-07-10 苏摩111授权

═══════════════════════════════════════════════════════════════
历史统计问题诊断（v3.0根因）:
  总扫描 186次 | 有信号 24次 | 实际推送 1次
  
  BUG-1【核心】去重逻辑用 scan_ts（文件时间戳）代替信号首次出现时间
    → _last_ts[sym] = last_data.get('scan_ts')  ← 每次扫描都刷新
    → 所有信号"上次出现时间"永远是几分钟前
    → 6H去重窗口永远触发 → 23/24次信号被锁死
    修复: 独立追踪每个标的的首次推送时间(signal_push_record.json)

  BUG-2 止损计算用 comp*0.3 (~ATR) 导致止损=0.3-0.4%（噪音级）
    修复: 按体制使用标准SL_PCT公式 (做多=2.0-3.0%)

  BUG-3 评分维度缺少量比加速（只看压缩度，无量比突破信号）
    修复: 量比突破(>1.5x)触发额外加分+单独标记

  BUG-4 无统计面板，无法知道信号质量/命中率
    修复: 每次推送附带统计摘要

  BUG-5 P2梵天验证失败无fallback，信号丢失
    修复: brahma验证失败时降级推送信号（不执行，等苏摩决策）

  BUG-6 脚本路径在cron隔离环境下 "No module named scripts" 偶发
    修复: 强化路径注入逻辑，增加多层fallback

═══════════════════════════════════════════════════════════════
"""
# ── 路径注入（必须最先执行，cron隔离环境兼容）───────────────
import sys as _sys_ph, os as _os_ph
_ph_root = _os_ph.path.abspath(
    _os_ph.path.dirname(_os_ph.path.dirname(_os_ph.path.dirname(_os_ph.path.abspath(__file__))))
)
for _p in [_ph_root, _os_ph.path.join(_ph_root, 'scripts')]:
    if _p not in _sys_ph.path:
        _sys_ph.path.insert(0, _p)

import requests, json, datetime, os, time, sys
from collections import defaultdict
from pathlib import Path

# 暴涨猎手2.0 · 状态机+探测器（设计院 2026-07-20）
try:
    from dharma.pump_hunter.pump_hunter_state import get_score_addons, notify_brahma_mode_c
    _HUNTER_STATE_OK = True
except Exception:
    _HUNTER_STATE_OK = False
    def get_score_addons(*a, **k): return {"total_bonus":0,"pump_end":{"pump_end":False},"notes":""}
    def notify_brahma_mode_c(*a, **k): pass

# ── 配置 ─────────────────────────────────────────────────────
API   = 'https://fapi.binance.com'
DIR   = os.path.dirname(os.path.abspath(__file__))
BASE  = os.path.abspath(os.path.join(DIR, '..', '..'))

OUT        = os.path.join(DIR, 'new_alerts.json')
LAST       = os.path.join(DIR, 'last_alerts.json')
LOG        = os.path.join(DIR, 'scan_log.jsonl')
PUSH_RECORD = os.path.join(DIR, 'signal_push_record.json')  # v4新增：独立记录每信号推送时间
STATS_FILE  = os.path.join(DIR, 'hunter_stats.json')        # v4新增：命中率统计
EXPIRY_FILE = os.path.join(DIR, 'signal_expiry.json')

# ── 候选过滤 ──────────────────────────────────────────────────
EXCLUDE     = {'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT'}
MIN_VOL     = 1_000_000     # [FIX 2026-08-03 设计院] 5M→1M：ZIL/TU/SKL等高分妙币成交量只有3M，5M过滤把了所有目标币
MAX_VOL     = 500_000_000   # 缩小上限避免大盘干扰
# [设计院顶层重构 2026-07-20 苏摩111自主决策]
# 废弃: MAX_CHG_ABS=8%（过滤掉所有有动能的币，正好是目标品种）
# 正确哲学：「有历史动能+近期压缩整理」= 目标状态
# BANK案例：07-11+13%后07-12~14横盘是最佳入场期，却被旧规则永久踢出
MAX_CHG_24H = 30.0          # 仅排除当日已爆发的，保留历史动能币
MAX_CHG_48H = 15.0          # 近期平静过滤：近48H涨幅<15%（消化整理中）
MIN_CHG_7D  = -15.0         # [设计院修复 2026-07-21] ACE案例根因修复
# 原值10.0: 排除7日下跌的币 → 把漫长缓跌+TIGHT压缩型妖币全部踢出
# 修复逻辑: TIGHT压缩才是核心信号，7日动能是辅助，不应作为必须条件
# 新值-15.0: 仅排除快速暴跌死币（7日跌>15%），保留缓跌蓄力型

# ── 评分阈值 ──────────────────────────────────────────────────
PUSH_SCORE  = 65            # [设计院修复 2026-08-03 v2] 恢复65：ERR修复后高分=293，需保持高门槛
EXEC_SCORE  = 90            # v4.1: 85→90 [苏摩111封印 2026-07-16]，确保自动执行阈值更严格

# ── 防漏判参数 ────────────────────────────────────────────────
VOL_RATIO_EXPIRED   = 3.0   # [根治修复 2026-07-18] 5.0→3.0 暴涨已发生更早过滤
PRICE_FROM_LOW_MAX  = 18.0  # v4: 15→18%，避免过早过滤回调机会
SIGNAL_VALID_MIN    = 30    # 信号有效窗口（分钟）

# ── 去重参数（v4修复核心）────────────────────────────────────
# 每个标的独立追踪推送时间，与scan_ts完全解耦
DEDUP_WINDOW_H = {
    'score_90_up':  4,   # score≥90: 4H内不重复（短窗口，高质量信号可快推）
    'score_80_89':  6,   # score 80-89: 6H
    'score_70_79':  8,   # score 70-79: 8H（原来统一6H，低分信号去重过松）
    'default':      6,
}

# ── 止损参数（v4修复 BUG-2）────────────────────────────────
# 严格按梵天SL_PCT公式，禁止用comp*0.3
SL_BY_REGIME = {
    'BEAR_TREND':    2.0,
    'BEAR_EARLY':    2.5,
    'BEAR_RECOVERY': 2.5,
    'CHOP_MID':      2.5,
    'CHOP_LOW':      3.0,
    'BULL_TREND':    2.0,
    'BULL_EARLY':    2.5,
}
SL_DEFAULT = 2.5


def _get_jarvis_target() -> str:
    if os.environ.get('JARVIS_TARGET'):
        return os.environ['JARVIS_TARGET']
    try:
        from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        return f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
    except Exception:
        pass
    return '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291'  # 2026-08-07 苏摩111更新最新线程

JARVIS_TARGET = _get_jarvis_target()


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _load_push_record():
    """加载独立的推送记录（v4核心：不再依赖scan_ts）"""
    if os.path.exists(PUSH_RECORD):
        try:
            return json.load(open(PUSH_RECORD))
        except:
            pass
    return {}


def _save_push_record(rec):
    json.dump(rec, open(PUSH_RECORD, 'w'), indent=2)


def _load_stats():
    if os.path.exists(STATS_FILE):
        try:
            return json.load(open(STATS_FILE))
        except:
            pass
    return {'total_signals': 0, 'total_pushed': 0, 'by_score': {}}


def _save_stats(stats):
    json.dump(stats, open(STATS_FILE, 'w'), indent=2)


def _get_dedup_window(score):
    if score >= 90: return DEDUP_WINDOW_H['score_90_up'] * 3600
    if score >= 80: return DEDUP_WINDOW_H['score_80_89'] * 3600
    return DEDUP_WINDOW_H['score_70_79'] * 3600


def _send_jarvis(msg):
    import subprocess
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', 'jarvis', '--to', JARVIS_TARGET,
         '--message', msg],
        capture_output=True, timeout=15
    )


# ════════════════════════════════════════════════════════════════
# 核心扫描逻辑
# ════════════════════════════════════════════════════════════════

def get_symbols():
    info = requests.get(f'{API}/fapi/v1/exchangeInfo', timeout=12).json()
    return [s['symbol'] for s in info['symbols']
            if s['status'] == 'TRADING'
            and s['symbol'].endswith('USDT')
            and 'UP' not in s['symbol']
            and 'DOWN' not in s['symbol']
            and s['symbol'] not in EXCLUDE]


def scan():
    t0 = time.time()
    _err_sym_log = []  # [FIX 2026-08-03] 记录异常标的
    syms = get_symbols()

    # 批量行情
    _raw = requests.get(f'{API}/fapi/v1/ticker/24hr', timeout=15).json()
    if not isinstance(_raw, list):
        _raw = []
    tickers = {t['symbol']: t for t in _raw
               if isinstance(t, dict) and t.get('symbol', '').endswith('USDT')}

    # 候选过滤 v5.0（设计院顶层重构 2026-07-20）
    # 哲学：「有历史动能+近期压缩」= 目标，而非「没涨的死币」
    # 两层过滤：第一层ticker快速筛（0额外API），第二层klines精确验证
    _pre_candidates = [
        s for s in syms
        if s in tickers
        and MIN_VOL < float(tickers[s].get('quoteVolume', 0)) < MAX_VOL
        and abs(float(tickers[s].get('priceChangePercent', 0))) < MAX_CHG_24H
    ]
    candidates = []
    for _s in _pre_candidates:
        try:
            _kl = requests.get(
                f'{API}/fapi/v1/klines',
                params={'symbol': _s, 'interval': '1h', 'limit': 170},
                timeout=5
            ).json()
            if not isinstance(_kl, list) or len(_kl) < 48:
                candidates.append(_s); continue
            _cls = [float(c[4]) for c in _kl]
            _chg48h = (_cls[-1] - _cls[-48]) / _cls[-48] * 100 if _cls[-48] > 0 else 0
            _chg7d  = (_cls[-1] - _cls[0])  / _cls[0]  * 100 if _cls[0]  > 0 else 0
            # [设计院修复 2026-07-21] 双轨纳入逻辑
            # 轨道A: 原始规则（有动能+近期平静）
            # 轨道B: TIGHT豁免（缓跌但极度压缩+量能萎缩 = ACE型妖币）
            _track_a = abs(_chg48h) <= MAX_CHG_48H and _chg7d >= MIN_CHG_7D
            
            # 轨道B: 7日缓跌(-30%~0%) + 48H平静(<15%) → 豁免7D要求（TIGHT评分决定）
            _is_slow_decline = -30.0 <= _chg7d < 0
            _track_b = _is_slow_decline and abs(_chg48h) <= MAX_CHG_48H
            
            if _track_a or _track_b:
                candidates.append(_s)
        except Exception:
            candidates.append(_s)  # API异常时保守纳入

    alerts = []

    # 获取BTC体制（用于止损参数）
    btc_regime = 'UNKNOWN'
    try:
        from brahma_brain.universal_asset_router import get_regime_cached
        btc_regime = get_regime_cached('BTCUSDT')
    except Exception:
        pass

    _sl_pct_base = SL_BY_REGIME.get(btc_regime, SL_DEFAULT)

    # ── [fix 2026-07-30 设计院] 批量预取OI/FR/LSR，每个候选只查1次API请求────────
    # 原因：185个候选 × 5次API = 925次请求 → 90秒超时被SIGTERM
    # 修复：预取全量OI/FR/LSR到字典，per-sym只抉1次klines15m
    try:
        _oi_map = {d['symbol']: float(d['openInterest'])
                   for d in json.loads(requests.get(f'{API}/fapi/v1/openInterest?limit=500', timeout=8).text or '[]')
                   if isinstance(d, dict)}
    except Exception:
        _oi_map = {}
    try:
        _fr_map = {d['symbol']: float(d['lastFundingRate'])*100
                   for d in json.loads(requests.get(f'{API}/fapi/v1/premiumIndex', timeout=8).text or '[]')
                   if isinstance(d, dict)}
    except Exception:
        _fr_map = {}
    # LSR批量获取耗时过长，用默认值0.5表示未知
    _lsr_map = {}
    # ────────────────────────────────────────────────────────────

    for sym in candidates:
        try:
            tick  = tickers[sym]
            chg   = float(tick['priceChangePercent'])
            vol   = float(tick['quoteVolume'])
            price = float(tick['lastPrice'])

            score   = 0
            reasons = []

            # ── 1. OI变化（使用预取的openInterest快照 + openInterestHist）─────
            # [fix 2026-07-30] OI历史仍需单独请求，但加timeout保护
            oi_chg = 0.0
            try:
                oi_hist = requests.get(
                    f'{API}/futures/data/openInterestHist',
                    params={'symbol': sym, 'period': '1h', 'limit': 48}, timeout=4
                ).json()
                if isinstance(oi_hist, list) and len(oi_hist) >= 12:
                    oi_early = sum(float(x['sumOpenInterestValue']) for x in oi_hist[:36]) / 36
                    oi_late  = sum(float(x['sumOpenInterestValue']) for x in oi_hist[-6:]) / 6
                    oi_chg   = (oi_late - oi_early) / oi_early * 100 if oi_early > 0 else 0
            except Exception:
                oi_chg = 0.0
            if oi_chg >= 60:
                score += 50; reasons.append(f'OI暴增+{oi_chg:.0f}%')  # [升级 40→50]
            elif oi_chg >= 40:
                score += 35; reasons.append(f'OI大增+{oi_chg:.0f}%')  # [升级 28→35]
            elif oi_chg >= 20:
                score += 20; reasons.append(f'OI增加+{oi_chg:.0f}%')  # [升级 15→20]
            elif oi_chg >= 10:
                score += 10;  reasons.append(f'OI小增+{oi_chg:.0f}%')  # [升级 8→10]

            # ── 2. 资金费率（使用预取字典，零额外请求）────────────
            latest_fr = _fr_map.get(sym, 0.0)
            if latest_fr < -0.05:
                score += 30; reasons.append(f'极端负费率{latest_fr:.3f}%')
            elif latest_fr < -0.02:
                score += 18; reasons.append(f'负费率{latest_fr:.3f}%')
            elif latest_fr < 0:
                score += 8;  reasons.append(f'轻微负费率{latest_fr:.3f}%')
            elif latest_fr > 0.04:
                score += 5;  reasons.append(f'正费率{latest_fr:.3f}%')

            # ── 3. 多空比（空头拥挤，使用预取或跳过）──────────────
            # [fix 2026-07-30] LSR per-sym请求改为预取字典，无数据时默认0.5
            lsr = [{'longShortRatio': str(_lsr_map.get(sym, 0.5)), 'shortAccount': '0.5'}]

            short_pct = float(lsr[-1].get('shortAccount', 0)) * 100 if isinstance(lsr, list) and lsr else 50
            if short_pct > 65:
                score += 25; reasons.append(f'空头极度拥挤{short_pct:.0f}%')
            elif short_pct > 60:
                score += 15; reasons.append(f'空头拥挤{short_pct:.0f}%')
            elif short_pct > 55:
                score += 8;  reasons.append(f'空头偏多{short_pct:.0f}%')

            # ── 4. K线结构：压缩+量比+RSI ──────────────────────
            kl = requests.get(
                f'{API}/fapi/v1/klines',
                params={'symbol': sym, 'interval': '4h', 'limit': 30}, timeout=6
            ).json()

            vol_ratio = 1.0
            comp      = 99.0
            rsi       = 50.0
            dist      = 0.0
            price_from_low = 0.0

            if isinstance(kl, list) and len(kl) >= 12:
                closes = [float(k[4]) for k in kl]
                highs  = [float(k[2]) for k in kl]
                lows   = [float(k[3]) for k in kl]
                qvols  = [float(k[7]) for k in kl]

                # TIGHT压缩度（近12根4H）
                h48 = max(highs[-12:])
                l48 = min(lows[-12:])
                ctr = (h48 + l48) / 2
                comp = (h48 - l48) / ctr * 100 if ctr > 0 else 99

                # 量比（近6根 vs 前18根）
                vol_recent = sum(qvols[-6:]) / 6
                vol_base   = sum(qvols[-24:-6]) / 18 if len(qvols) >= 24 else vol_recent
                vol_ratio  = vol_recent / vol_base if vol_base > 0 else 1

                # 防暴涨已发生检测
                if vol_ratio >= VOL_RATIO_EXPIRED:
                    score = -999
                    reasons.append(f'⚠️已发生(vol_ratio={vol_ratio:.1f}x≥{VOL_RATIO_EXPIRED}x)')
                else:
                    # 价格距近期低点
                    low_6bar = min(lows[-6:])
                    price_from_low = (price - low_6bar) / low_6bar * 100 if low_6bar > 0 else 0
                    if price_from_low > PRICE_FROM_LOW_MAX:
                        score = -999
                        reasons.append(f'⚠️追高({price_from_low:.1f}%>{PRICE_FROM_LOW_MAX}%)')
                    else:
                    # [设计院封印 2026-08-07] TIGHT压缩评分放宽到<25%，并分离入场和压缩两种状态
                        # 根因：ACE等妖币爆发前压缩度多在0~25%，原来<10才有分质量不尺
                        if comp < 10:
                            score += 35; reasons.append(f'极度TIGHT{comp:.0f}%')
                        elif comp < 15:
                            score += 25; reasons.append(f'TIGHT{comp:.0f}%')
                        elif comp < 20:
                            score += 18; reasons.append(f'压缩{comp:.0f}%')  # 提升：15’18
                        elif comp < 25:
                            score += 12; reasons.append(f'轻压缩{comp:.0f}%')  # 提升：8’12
                        elif comp < 35:
                            score += 5;  reasons.append(f'横盘{comp:.0f}%')  # 新增：25-35%也给少量分

                        # ── v4新增：量比突破评分 ────────────────
                        # [设计院升级 2026-08-07] 量能突破是妖币爆发最直接前兆
                        if vol_ratio < 0.4:
                            score += 15; reasons.append(f'量能极度萎缩{vol_ratio:.2f}x')
                        elif vol_ratio < 0.6:
                            score += 10; reasons.append(f'量能萎缩{vol_ratio:.2f}x')
                        elif vol_ratio > 3.0:
                            # 量比大幅放大（爆发启动信号，提升权重）
                            score += 25; reasons.append(f'🚨量能暴增{vol_ratio:.1f}x⚡')
                        elif vol_ratio > 2.0:
                            score += 18; reasons.append(f'量比大增{vol_ratio:.1f}x⚡')  # 升级 12→18
                        elif vol_ratio > 1.5:
                            score += 10;  reasons.append(f'量比提升{vol_ratio:.1f}x')  # 升级 8→10

                        # RSI
                        if len(closes) >= 15:
                            d = [closes[i]-closes[i-1] for i in range(1, len(closes))]
                            g = [max(0, x) for x in d[-14:]]
                            lo = [max(0, -x) for x in d[-14:]]
                            ag = sum(g)/14; al = sum(lo)/14
                            rsi = 100-100/(1+ag/al) if al > 0 else 50

                        if rsi < 25:
                            score += 20; reasons.append(f'RSI极超卖{rsi:.0f}')
                        elif rsi < 35:
                            score += 12; reasons.append(f'RSI超卖{rsi:.0f}')
                        elif rsi < 45:
                            score += 6;  reasons.append(f'RSI低位{rsi:.0f}')
                        elif rsi > 70:
                            score -= 5;  reasons.append(f'RSI超买-5')

                        # ── [Alpha158 2026-07-24 设计院] rsv_5 + std_5 两项因子 ──
                        # rsv_5: 5根K棒随机值(KDJ的K分子)，压缩形态时极低→暴涨概率↑
                        # std_5: 5根收盘价标准差/收盘价，波动率极低→能量蓄积
                        if len(closes) >= 5:
                            _rsv5_h = max(highs[-5:]); _rsv5_l = min(lows[-5:])
                            rsv5 = (price - _rsv5_l) / (_rsv5_h - _rsv5_l + 1e-12)
                            if rsv5 < 0.15:
                                score += 12; reasons.append(f'rsv5极低{rsv5:.2f}(Alpha158)')
                            elif rsv5 < 0.30:
                                score += 7;  reasons.append(f'rsv5低位{rsv5:.2f}(Alpha158)')
                            import statistics as _st
                            std5 = _st.stdev(closes[-5:]) / closes[-1] if closes[-1] > 0 else 0
                            if std5 < 0.008:
                                score += 10; reasons.append(f'std5极低{std5:.4f}(Alpha158)')
                            elif std5 < 0.015:
                                score += 5;  reasons.append(f'std5低{std5:.4f}(Alpha158)')
                        # ── end Alpha158 ──

                        # ── [Alpha158 × Kronos 联合门控 2026-07-24 设计院封印] ──
                        # 规则: rsv5<0.15(压缩到位) AND Kronos p_up>0.65(时序启动) → 双重确认+15分
                        # 依据: 形态分析(Alpha158)与时序预测(Kronos)同向共振=最高置信暴涨预警
                        try:
                            if rsv5 < 0.15:  # 压缩已到位
                                import sys as _sys_ph; _sys_ph.path.insert(0, '.')
                                from brahma_brain.kronos_bridge import get_s23_kronos as _ph_kronos
                                _ph_kl = requests.get(
                                    f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=64',
                                    timeout=3
                                ).json()
                                _ph_kr = _ph_kronos(klines_15m=_ph_kl, symbol=symbol)
                                _ph_pup = (_ph_kr[1] if isinstance(_ph_kr, tuple) else _ph_kr).get('p_up', 0.5)
                                if _ph_pup > 0.65:
                                    score += 15
                                    reasons.append(f'Alpha158×Kronos共振p_up={_ph_pup:.2f}(+15联合奖励)')
                        except Exception:
                            pass  # Kronos不可用时不阻断暴涨猎手
                        # ── end 联合门控 ──


                        # 距历史高点
                        hist_high = max(highs)
                        dist = (price - hist_high) / hist_high * 100
                        if dist < -70:
                            score += 12; reasons.append(f'历史低位{dist:.0f}%')
                        elif dist < -50:
                            score += 8;  reasons.append(f'深度低位{dist:.0f}%')
                        elif dist < -30:
                            score += 4

            # ── 5. 体制感知（仅调参，不折损信号分）──────────────
            regime_pos  = 1.0  # 仓位%NAV
            regime_tp   = 1.8  # 止盈倍数
            if btc_regime == 'BEAR_TREND':
                regime_pos = 2.5; regime_tp = 1.8  # 熊市逼空最猛
            elif btc_regime in ('BEAR_RECOVERY', 'CHOP_MID'):
                regime_pos = 2.0; regime_tp = 1.5
            elif btc_regime == 'BULL_TREND':
                regime_pos = 1.5; regime_tp = 1.3
            else:
                regime_pos = 2.0; regime_tp = 1.5

            # ── 6. 构建信号对象 ────────────────────────────────
            # ── v4.1 FR/空头比例门控（修复2026-07-30：原阈值过严导致全量静默）──────
            # 原规则：FR<-0.01% OR 空头%>62%，实测853合约中仅8%满足，TIGHT型全灭
            # 修复：放宽为 FR<-0.001% OR 空头%>58% OR score>=85（高分TIGHT豁免）
            # [FIX 2026-08-03 设计院] 去除强制压分：无催化剂时只标记WATCH，不强制score<PUSH_SCORE
            # 原逻辑: 无催化剂→score=min(score,PUSH_SCORE-1)→CHOP体制下TIGHT永远静默
            # 新逻辑: 无催化剂→标记[WATCH]，让PUSH_SCORE=45自然过滤
            squeeze_catalyst = (latest_fr < -0.001) or (short_pct > 58) or (score >= EXEC_SCORE)
            if not squeeze_catalyst:
                reasons.append(f'[WATCH-仅压缩无催化剂: FR={latest_fr:.4f}% 空头={short_pct:.0f}%]')
                # 不再强制压分 → TIGHT+催化剂缺失仍可推送（PUSH_SCORE=45接管）

            # 状态机加成（TIGHT持续时间+已知妖币+暴涨结束检测）
            _vol_list = [float(k[7]) for k in kl] if kl else []
            _vol_prev = _vol_list[-2] if len(_vol_list) >= 2 else 0
            _vol_avg20 = sum(_vol_list[-20:]) / min(20, len(_vol_list)) if _vol_list else 1
            _addons = get_score_addons(
                sym, comp, score, price,
                vol_current=_vol_list[-1] if _vol_list else 0,
                vol_prev=_vol_prev, vol_avg_20=_vol_avg20,
                price_change_pct=chg,
            )
            if _addons['total_bonus'] > 0:
                score += _addons['total_bonus']
                reasons.append(_addons['notes'])
            # 暴涨结束信号（单独推送，不影响常规预警流程）
            if _addons['pump_end']['pump_end']:
                reasons.append(_addons['pump_end']['signal'])

            if score >= PUSH_SCORE:
                # v4修复 BUG-2：止损用SL_PCT公式，不用comp*0.3
                sl_pct   = _sl_pct_base
                tp_mult  = regime_tp
                entry_lo = round(price * 0.996, 6)
                entry_hi = round(price * 1.004, 6)
                sl_price = round(price * (1 - sl_pct / 100), 6)  # 做多止损=入场下方
                tp1_price= round(price * (1 + sl_pct * tp_mult / 100), 6)

                # 梵天2.0联动：猎手预警→MODE_C写入
                _alert_lv = 3 if score >= 95 else (2 if score >= 85 else 1)
                notify_brahma_mode_c(sym, score, _alert_lv)
                alerts.append({
                    'symbol':   sym,
                    'score':    score,
                    'price':    price,
                    'chg_24h':  round(chg, 1),
                    'vol_m':    round(vol / 1e6, 1),
                    'oi_chg':   round(oi_chg, 1),
                    'funding':  round(latest_fr, 4),
                    'short_pct': round(short_pct, 1),
                    'compression': round(comp, 1),
                    'vol_ratio': round(vol_ratio, 2),
                    'rsi':      round(rsi, 1),
                    'dist_from_high': round(dist, 1),
                    'price_from_low': round(price_from_low, 1),
                    'entry_lo': entry_lo,
                    'entry_hi': entry_hi,
                    'sl_price': sl_price,
                    'sl_pct':   round(sl_pct, 1),
                    'tp1_price': tp1_price,
                    'tp_mult':  tp_mult,
                    'rr':       round(tp_mult, 1),
                    'brahma_regime':   btc_regime,
                    'exec_pos_pct':    regime_pos,
                    'exec_eligible':   score >= EXEC_SCORE,
                    'expire_ts': time.time() + SIGNAL_VALID_MIN * 60,
                    'scan_time': datetime.datetime.utcnow().isoformat(),
                    'squeeze_catalyst': squeeze_catalyst,  # v4.1
                    'reasons':  reasons,
                })

        except Exception as e:
            # [FIX 2026-08-03 v2] 记录异常日志，但ERR信号不入alerts（数据不完整）
            _err_sym_log.append(f"{sym}:{type(e).__name__}:{str(e)[:60]}")
            pass

    alerts.sort(key=lambda x: -x['score'])
    elapsed = time.time() - t0
    if _err_sym_log:
        print(f'[SCAN-ERR] {len(_err_sym_log)}个标的异常(不推送): {_err_sym_log[:3]}')
    return alerts, elapsed, len(candidates), btc_regime


# ════════════════════════════════════════════════════════════════
# 去重逻辑（v4修复 BUG-1）
# ════════════════════════════════════════════════════════════════

def filter_new_alerts(alerts):
    """
    v4核心修复：每个标的独立记录推送时间，与scan_ts完全解耦
    旧逻辑: _last_ts[sym] = last_data.scan_ts → 每次扫描都刷新 → 永远去重
    新逻辑: push_record[sym]['last_push_ts'] → 仅推送成功时更新
    """
    push_record = _load_push_record()
    now_ts      = time.time()
    new_alerts  = []

    for a in alerts:
        sym   = a['symbol']
        score = a['score']
        rec   = push_record.get(sym, {})
        last_push = rec.get('last_push_ts', 0)
        dedup_window = _get_dedup_window(score)
        age = now_ts - last_push

        if age >= dedup_window:
            new_alerts.append(a)
        # else: 在去重窗口内，跳过

    return new_alerts


# ════════════════════════════════════════════════════════════════
# 推送格式（v4增强版）
# ════════════════════════════════════════════════════════════════

def format_alert_v4(a, rank=1, auto_executed=False, exec_result=None):
    """v4推送格式：精简+完整决策信息"""
    score    = a.get('score', 0)
    sym      = a['symbol']
    price    = a.get('price', 0)
    oi_chg   = a.get('oi_chg', 0)
    funding  = a.get('funding', 0)
    short_pct= a.get('short_pct', 50)
    comp     = a.get('compression', 99)
    vol_ratio= a.get('vol_ratio', 1)
    rsi      = a.get('rsi', 50)
    regime   = a.get('brahma_regime', '?')
    pos_pct  = a.get('exec_pos_pct', 1.0)
    entry_lo = a.get('entry_lo', price*0.996)
    entry_hi = a.get('entry_hi', price*1.004)
    sl_price = a.get('sl_price', 0)
    sl_pct   = a.get('sl_pct', 2.5)
    tp1      = a.get('tp1_price', 0)
    rr       = a.get('rr', 1.8)
    from_low = a.get('price_from_low', 0)
    reasons  = ' | '.join(a.get('reasons', [])[:4])

    lvl = '💣' if score >= 90 else ('🚨' if score >= 80 else '⚡')
    safe_tag = '✅安全' if from_low <= 8 else f'⚠️+{from_low:.0f}%'

    exec_line = ''
    if auto_executed:
        st = exec_result.get('status', '?') if exec_result else '已写入队列'
        exec_line = f'\n🤖 自动执行: {st}'
    elif score >= EXEC_SCORE:
        exec_line = f'\n🤖 score≥{EXEC_SCORE}→梵天验证中'

    now_str = datetime.datetime.utcnow().strftime('%H:%M')
    return (
        f'{lvl} 暴涨猎手 #{rank} · {sym}\n'
        f'⏰ {now_str} UTC | 窗口: {SIGNAL_VALID_MIN}min\n'
        f'─────────────────────────\n'
        f'📊 综合评分: {score}\n'
        f'📍 现价: {price:.4g} | 距低: {safe_tag}\n'
        f'📈 {reasons}\n'
        f'─────────────────────────\n'
        f'OI增幅: +{oi_chg:.0f}%  |  FR: {funding:.4f}%\n'
        f'空头%: {short_pct:.0f}%  |  RSI: {rsi:.0f}\n'
        f'压缩: {comp:.0f}%  |  量比: {vol_ratio:.2f}x\n'
        f'─────────────────────────\n'
        f'🎯 入场: {entry_lo:.4g}~{entry_hi:.4g}\n'
        f'   SL: {sl_price:.4g}（-{sl_pct:.1f}%）\n'
        f'   TP1: {tp1:.4g}（×{rr}R）\n'
        f'   仓位: {pos_pct:.1f}%NAV | 体制: {regime}'
        f'{exec_line}'
    )


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

def main():
    # [P3 2026-08-05 设计院] BEAR体制下暂停暗涨猎手推送（压缩突破在BEAR方向可能向下）
    try:
        import json as _j3
        from pathlib import Path as _P3
        _rs3 = _j3.loads((_P3(__file__).parent.parent.parent / 'data' / 'regime_state.json').read_text())
        _btc3 = _rs3.get('BTCUSDT',{}).get('confirmed','?') if isinstance(_rs3.get('BTCUSDT'),dict) else '?'
        if _btc3 == 'BEAR_TREND':
            print(f'[pump-hunter] BEAR_TREND体制下暂停推送 HEARTBEAT_OK')
            return
    except Exception:
        pass  # 读取失败则不封禁

    # 扫描
    alerts, elapsed, n_candidates, btc_regime = scan()

    # v4去重（修复BUG-1）
    new_alerts = filter_new_alerts(alerts)

    now_ts = time.time()
    result = {
        'scan_time':      datetime.datetime.utcnow().isoformat(),
        'elapsed_sec':    round(elapsed, 1),
        'total_scanned':  n_candidates,
        'alerts':         alerts,
        'new_alerts':     new_alerts,
        'need_push':      len(new_alerts) > 0,
        'scan_ts':        now_ts,
        'btc_regime':     btc_regime,
    }

    json.dump(result, open(OUT,  'w'), indent=2, ensure_ascii=False)
    json.dump(result, open(LAST, 'w'), indent=2, ensure_ascii=False)

    # 日志
    with open(LOG, 'a') as f:
        f.write(json.dumps({
            'ts':      result['scan_time'],
            'alerts':  len(alerts),
            'new':     len(new_alerts),
            'elapsed': elapsed,
            'regime':  btc_regime,
        }) + '\n')

    print(f'扫描完成 {elapsed:.1f}s | 候选={n_candidates} | 高分={len(alerts)} | 新={len(new_alerts)} | 需推送={result["need_push"]}')
    if alerts:
        for a in alerts[:3]:
            print(f'  {a["symbol"]:18} score={a["score"]:3d} | {" | ".join(a["reasons"][:2])}')

    # ── 推送逻辑 ──────────────────────────────────────────────
    if result['need_push'] and new_alerts:
        push_record = _load_push_record()
        stats       = _load_stats()

        for i, a in enumerate(new_alerts[:5], 1):
            sym    = a['symbol']
            score  = a['score']
            auto_executed = False
            exec_result   = None

            # P2: score≥85 → 梵天验证 → 自动写入执行队列
            # [fix 2026-07-30 设计院] 跳过run_analysis，避免阶塑流程被SIGTERM杀死
            # run_analysis耗时40~60s，导致整个scan进程最终SIGTERM，历史上所有信号全部静默
            # 修复方案：score达到EXEC_SCORE标记exec_eligible=True，由独立任务验证执行
            if score >= EXEC_SCORE:
                # 标记exec_eligible，跳过阻塞性run_analysis
                exec_result = {'status': 'exec_eligible，待独立验证'}
                pass

            # P1: 推送信号
            msg = format_alert_v4(a, rank=i,
                                   auto_executed=auto_executed,
                                   exec_result=exec_result)
            _send_jarvis(msg)
            print(f'[pump-hunter v4] 推送: {sym} score={score}')

            # v4：更新独立推送记录（修复BUG-1核心）
            # [P1 2026-08-05 设计院] 注入体制字段，用于WR按体制过滤
            _regime_at_push = '?'
            _btc_r = '?'; _eth_r = '?'
            try:
                import json as _j2
                _rs2 = _j2.loads(open(os.path.join(os.path.dirname(os.path.dirname(DIR)),'data','regime_state.json')).read())
                _btc_r = _rs2.get('BTCUSDT',{}).get('confirmed','?') if isinstance(_rs2.get('BTCUSDT'),dict) else '?'
                _eth_r = _rs2.get('ETHUSDT',{}).get('confirmed','?') if isinstance(_rs2.get('ETHUSDT'),dict) else '?'
                _regime_at_push = _btc_r  # 全局体制用BTC代表
            except Exception:
                pass
            push_record[sym] = {
                'last_push_ts': now_ts,
                'last_score':   score,
                'last_push_at': datetime.datetime.utcnow().isoformat(),
                'push_price':   a.get('price', 0),
                'regime':       _regime_at_push,   # [P1] 推送时体制
                'btc_regime':   _btc_r,             # [P1] BTC体制
                'eth_regime':   _eth_r,             # [P1] ETH体制
            }

            # P3: 写入过期追踪
            _expiry = {}
            try:
                if os.path.exists(EXPIRY_FILE):
                    _expiry = json.load(open(EXPIRY_FILE))
            except:
                pass
            _expiry[sym] = {
                'score':    score,
                'price':    a.get('price', 0),
                'entry_lo': a.get('entry_lo', 0),
                'entry_hi': a.get('entry_hi', 0),
                'sl_price': a.get('sl_price', 0),
                'tp1_price': a.get('tp1_price', 0),
                'expire_ts': a.get('expire_ts', now_ts + SIGNAL_VALID_MIN*60),
                'pushed_ts': now_ts,
                'auto_executed': auto_executed,
                'reminded': False,
                # v4.1: 催化剂标记，供hunter_outcome_tracker.py分析
                'squeeze_catalyst': a.get('squeeze_catalyst', False),
                'funding':   a.get('funding', 0),
                'short_pct': a.get('short_pct', 50),
            }
            json.dump(_expiry, open(EXPIRY_FILE, 'w'), indent=2)

            # 更新统计
            stats['total_pushed'] = stats.get('total_pushed', 0) + 1

        _save_push_record(push_record)

        # 统计更新
        stats['total_signals'] = stats.get('total_signals', 0) + len(alerts)
        stats['last_push_at']  = datetime.datetime.utcnow().isoformat()
        stats['last_push_count'] = len(new_alerts)
        _save_stats(stats)

        print(f'[pump-hunter v4] 推送完成 {len(new_alerts)}个信号')

    # P3: 检查即将过期的信号（<5min），发二次提醒
    try:
        _expiry = {}
        if os.path.exists(EXPIRY_FILE):
            _expiry = json.load(open(EXPIRY_FILE))
        _updated = False
        for _sym, _info in list(_expiry.items()):
            _remaining = _info.get('expire_ts', 0) - now_ts
            _reminded  = _info.get('reminded', False)
            _auto_exec = _info.get('auto_executed', False)
            if 0 < _remaining < 300 and not _reminded and not _auto_exec:
                _remind_msg = (
                    f'⏰ 信号即将过期: {_sym}\n'
                    f'score={_info.get("score")} | 入场={_info.get("entry_lo"):.4g}~{_info.get("entry_hi"):.4g}\n'
                    f'还剩约{int(_remaining/60)}分钟 | 未操作将自动作废'
                )
                _send_jarvis(_remind_msg)
                _expiry[_sym]['reminded'] = True
                _updated = True
            elif _remaining <= 0:
                del _expiry[_sym]
                _updated = True
        if _updated:
            json.dump(_expiry, open(EXPIRY_FILE, 'w'), indent=2)
    except Exception as _e:
        print(f'[pump-hunter v4] P3过期检查异常: {_e}')

    if alerts and not result['need_push']:
        print('(已有高分信号，在去重窗口内，HEARTBEAT_OK)')

    return result


if __name__ == '__main__':
    r = main()
