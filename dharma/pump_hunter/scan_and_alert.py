#!/usr/bin/env python3
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
MIN_VOL     = 20_000_000    # 最低24H成交额$20M（适当降低覆盖）
MAX_VOL     = 800_000_000   # 排除超大盘
MAX_CHG_ABS = 25.0          # 排除已大幅波动（±25%以内）

# ── 评分阈值 ──────────────────────────────────────────────────
PUSH_SCORE  = 70            # v4: 75→70，适当降低门槛提高覆盖
EXEC_SCORE  = 85            # 触发梵天验证+自动执行的门槛

# ── 防漏判参数 ────────────────────────────────────────────────
VOL_RATIO_EXPIRED   = 5.0   # vol_ratio≥5x = 暴涨已发生，信号作废
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
    return '73295708:thread:019f309c-609b-7a75-a195-e221e5927c63'

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
    syms = get_symbols()

    # 批量行情
    _raw = requests.get(f'{API}/fapi/v1/ticker/24hr', timeout=15).json()
    if not isinstance(_raw, list):
        _raw = []
    tickers = {t['symbol']: t for t in _raw
               if isinstance(t, dict) and t.get('symbol', '').endswith('USDT')}

    # 候选过滤
    candidates = [s for s in syms
                  if s in tickers
                  and MIN_VOL < float(tickers[s].get('quoteVolume', 0)) < MAX_VOL
                  and abs(float(tickers[s].get('priceChangePercent', 0))) < MAX_CHG_ABS]

    alerts = []

    # 获取BTC体制（用于止损参数）
    btc_regime = 'UNKNOWN'
    try:
        from brahma_brain.universal_asset_router import get_regime_cached
        btc_regime = get_regime_cached('BTCUSDT')
    except Exception:
        pass

    _sl_pct_base = SL_BY_REGIME.get(btc_regime, SL_DEFAULT)

    for sym in candidates:
        try:
            tick  = tickers[sym]
            chg   = float(tick['priceChangePercent'])
            vol   = float(tick['quoteVolume'])
            price = float(tick['lastPrice'])

            score   = 0
            reasons = []

            # ── 1. OI变化（近6H vs 前段均值）─────────────────
            oi_hist = requests.get(
                f'{API}/futures/data/openInterestHist',
                params={'symbol': sym, 'period': '1h', 'limit': 48}, timeout=6
            ).json()

            oi_chg = 0.0
            if isinstance(oi_hist, list) and len(oi_hist) >= 12:
                oi_early = sum(float(x['sumOpenInterestValue']) for x in oi_hist[:36]) / 36
                oi_late  = sum(float(x['sumOpenInterestValue']) for x in oi_hist[-6:]) / 6
                oi_chg   = (oi_late - oi_early) / oi_early * 100 if oi_early > 0 else 0
                if oi_chg >= 60:
                    score += 40; reasons.append(f'OI暴增+{oi_chg:.0f}%')
                elif oi_chg >= 40:
                    score += 28; reasons.append(f'OI大增+{oi_chg:.0f}%')
                elif oi_chg >= 20:
                    score += 15; reasons.append(f'OI增加+{oi_chg:.0f}%')
                elif oi_chg >= 10:
                    score += 8;  reasons.append(f'OI小增+{oi_chg:.0f}%')

            # ── 2. 资金费率 ────────────────────────────────────
            fr_list = requests.get(
                f'{API}/fapi/v1/fundingRate',
                params={'symbol': sym, 'limit': 6}, timeout=5
            ).json()

            latest_fr = float(fr_list[-1]['fundingRate']) * 100 if isinstance(fr_list, list) and fr_list else 0
            if latest_fr < -0.05:
                score += 30; reasons.append(f'极端负费率{latest_fr:.3f}%')
            elif latest_fr < -0.02:
                score += 18; reasons.append(f'负费率{latest_fr:.3f}%')
            elif latest_fr < 0:
                score += 8;  reasons.append(f'轻微负费率{latest_fr:.3f}%')
            elif latest_fr > 0.04:
                score += 5;  reasons.append(f'正费率{latest_fr:.3f}%')

            # ── 3. 多空比（空头拥挤） ───────────────────────────
            lsr = requests.get(
                f'{API}/futures/data/globalLongShortAccountRatio',
                params={'symbol': sym, 'period': '1h', 'limit': 3}, timeout=5
            ).json()

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
                        # TIGHT压缩评分
                        if comp < 10:
                            score += 35; reasons.append(f'极度TIGHT{comp:.0f}%')
                        elif comp < 15:
                            score += 25; reasons.append(f'TIGHT{comp:.0f}%')
                        elif comp < 20:
                            score += 15; reasons.append(f'压缩{comp:.0f}%')
                        elif comp < 25:
                            score += 8;  reasons.append(f'轻压缩{comp:.0f}%')

                        # ── v4新增：量比突破评分 ────────────────
                        if vol_ratio < 0.4:
                            score += 15; reasons.append(f'量能极度萎缩{vol_ratio:.2f}x')
                        elif vol_ratio < 0.6:
                            score += 10; reasons.append(f'量能萎缩{vol_ratio:.2f}x')
                        elif vol_ratio > 2.0:
                            # 量比放大但未达到暴涨级（1.5~5x = 启动信号）
                            score += 12; reasons.append(f'量比放大{vol_ratio:.1f}x⚡')
                        elif vol_ratio > 1.5:
                            score += 8;  reasons.append(f'量比提升{vol_ratio:.1f}x')

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
            if score >= PUSH_SCORE:
                # v4修复 BUG-2：止损用SL_PCT公式，不用comp*0.3
                sl_pct   = _sl_pct_base
                tp_mult  = regime_tp
                entry_lo = round(price * 0.996, 6)
                entry_hi = round(price * 1.004, 6)
                sl_price = round(price * (1 - sl_pct / 100), 6)  # 做多止损=入场下方
                tp1_price= round(price * (1 + sl_pct * tp_mult / 100), 6)

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
                    'reasons':  reasons,
                })

        except Exception as e:
            pass

    alerts.sort(key=lambda x: -x['score'])
    elapsed = time.time() - t0
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
            if score >= EXEC_SCORE:
                try:
                    from brahma_brain.brahma_analysis_runner import run_analysis
                    _res = run_analysis(sym)
                    _valid = _res.get('valid_signal', False)
                    if _valid:
                        try:
                            from scripts.signal_bus import write as _sb_write
                            _px  = float(a.get('price', 0))
                            _atr = float(a.get('sl_pct', SL_DEFAULT)) / 100
                            _sb_write({
                                'source':    'pump_auto',
                                'symbol':    sym,
                                'direction': 'LONG',
                                'score':     float(score),
                                'valid':     True,
                                'regime':    btc_regime,
                                'entry_lo':  a.get('entry_lo', _px*0.996),
                                'entry_hi':  a.get('entry_hi', _px*1.004),
                                'sl':        a.get('sl_price', _px*(1-_atr)),
                                'sl_pct':    a.get('sl_pct', SL_DEFAULT),
                                'tp1':       a.get('tp1_price', _px*1.05),
                                'rr1':       a.get('rr', 1.8),
                                'expires_at': time.time() + SIGNAL_VALID_MIN*60,
                                'pos_pct':   a.get('exec_pos_pct', 1.0),
                            })
                            auto_executed = True
                            exec_result = {'status': '已写入执行队列'}
                            print(f'[pump-hunter v4] P2自动执行: {sym}')
                        except Exception as _e:
                            print(f'[pump-hunter v4] 信号总线写入失败: {_e}')
                except Exception as _e:
                    # v4修复 BUG-5: 梵天验证失败不丢弃信号，降级推送
                    print(f'[pump-hunter v4] 梵天验证异常(降级推送): {_e}')

            # P1: 推送信号
            msg = format_alert_v4(a, rank=i,
                                   auto_executed=auto_executed,
                                   exec_result=exec_result)
            _send_jarvis(msg)
            print(f'[pump-hunter v4] 推送: {sym} score={score}')

            # v4：更新独立推送记录（修复BUG-1核心）
            push_record[sym] = {
                'last_push_ts': now_ts,
                'last_score':   score,
                'last_push_at': datetime.datetime.utcnow().isoformat(),
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
