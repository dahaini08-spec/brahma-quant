#!/usr/bin/env python3
"""
ep_score.py — 执行优先级综合评分（EP Score）
设计院 · 2026-06-06  终极方案P0~P2核心模块

EP Score = 梵天基础分
  + gap调整     (gap=0%→+10, gap=0.5%→+5, gap=2%→-10, gap>5%→-30)
  + FR调整      (FR>0.005%→+5, FR<-0.01%→-15, FR<-0.005%→-8)
  + 体制强度    (BEAR_TREND STRONG→+8, BEAR_TREND→+5, BEAR_EARLY→-3, CHOP→-20)
  + 信号新鲜度  (<1H→0, 1~4H→-3, 4~8H→-8, 8~24H→-15, >24H→-25)
  + 流动性      (vol<$100M→-20, $100~500M→-5, >$500M→0)
  + 特殊惩罚    (LAB/CHZ等小市值→-50, FR<-0.01%且空单→额外-8)
"""
import json, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent

# 小市值/低质量品种黑名单
NOISY_SYMS = {'LABUSDT', 'CHZUSDT', 'ZKCUSDT', 'HEMIUSDT', 'PORTALUSDT', 'WLDUSDT'}

# 已知流动性分级（24H vol USD，按量级）
VOL_TIER = {
    'BTCUSDT':  'HIGH',    # >10B
    'ETHUSDT':  'HIGH',
    'SOLUSDT':  'HIGH',
    'XRPUSDT':  'HIGH',
    'BNBUSDT':  'HIGH',
    'DOGEUSDT': 'MED',     # 500M~2B
    'ADAUSDT':  'MED',
    'LTCUSDT':  'MED',
    'LINKUSDT': 'MED',
    'AVAXUSDT': 'MED',
}

_price_cache = {}
_fr_cache    = {}
_cache_ts    = {}
CACHE_TTL    = 90  # 秒

def _get_price(sym: str) -> float:
    now = time.time()
    if sym in _price_cache and now - _cache_ts.get(f'p_{sym}', 0) < CACHE_TTL:
        return _price_cache[sym]
    try:
        r = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=4)
        p = float(json.loads(r.read())['price'])
        _price_cache[sym] = p
        _cache_ts[f'p_{sym}'] = now
        return p
    except:
        return 0.0

def _get_fr(sym: str) -> float:
    """资金费率，已转为%"""
    now = time.time()
    if sym in _fr_cache and now - _cache_ts.get(f'fr_{sym}', 0) < CACHE_TTL:
        return _fr_cache[sym]
    try:
        r = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}', timeout=4)
        fr = float(json.loads(r.read()).get('lastFundingRate', 0)) * 100
        _fr_cache[sym] = fr
        _cache_ts[f'fr_{sym}'] = now
        return fr
    except:
        return 0.0

def calc_ep(sig: dict, price: float = 0, fr: float = None) -> dict:
    """
    计算单个信号的EP Score及分项明细。
    sig: 信号字典（含symbol/score/ts/regime/entry_lo等）
    price: 实时价格（传0则自动拉取）
    fr: 资金费率%（传None则自动拉取）
    返回: {ep: int, base: float, adj_gap: int, adj_fr: int, adj_regime: int,
           adj_fresh: int, adj_liquidity: int, adj_noisy: int,
           gap_pct: float, fr_pct: float, age_h: float, breakdown: str}
    """
    sym        = sig.get('symbol', '')
    base_score = float(sig.get('score', 0) or 0)
    regime     = str(sig.get('regime', '') or '')
    direction  = str(sig.get('signal_dir') or sig.get('direction', '') or 'SHORT').upper()
    ts_str     = str(sig.get('ts', '') or '')
    entry_lo   = float(sig.get('entry_lo') or 0)

    # ── 自动拉取实时数据 ──────────────────────────────────
    if not price:
        price = _get_price(sym)
    if fr is None:
        fr = _get_fr(sym)

    # ── gap调整 ───────────────────────────────────────────
    gap_pct = 0.0
    adj_gap = 0
    if price > 0 and entry_lo > 0:
        gap_pct = (entry_lo - price) / price * 100
        if gap_pct < -0.5:      adj_gap = -15   # 已跌穿入场区较深
        elif gap_pct < 0:       adj_gap = -5    # 已跌穿入场区轻微
        elif gap_pct <= 0.3:    adj_gap = +10   # ⚡在区内
        elif gap_pct <= 0.8:    adj_gap = +5    # 📍极近
        elif gap_pct <= 1.5:    adj_gap = 0     # 待反弹
        elif gap_pct <= 3.0:    adj_gap = -10   # 较远
        elif gap_pct <= 5.0:    adj_gap = -20   # 很远
        else:                   adj_gap = -30   # 过期入场区

    # ── FR调整 ────────────────────────────────────────────
    adj_fr = 0
    is_short = 'SHORT' in direction or '空' in direction
    if fr > 0.008:              adj_fr = +8    # 多头大量付费 → 空单收益最好
    elif fr > 0.004:            adj_fr = +5
    elif fr > 0.001:            adj_fr = +2
    elif fr >= -0.001:          adj_fr = 0     # 中性
    elif fr >= -0.005:
        adj_fr = -5 if is_short else 0         # 空头轻微付费
    elif fr >= -0.010:
        adj_fr = -10 if is_short else 0        # 空头拥挤
    else:
        adj_fr = -18 if is_short else 0        # 空头严重拥挤

    # ── 体制强度调整 ──────────────────────────────────────
    adj_regime = 0
    reg_upper  = regime.upper()
    if 'BEAR_TREND' in reg_upper:
        adj_regime = +8 if is_short else -20
    elif 'BEAR_EARLY' in reg_upper:
        adj_regime = +3 if is_short else -10
    elif 'BEAR_RECOVERY' in reg_upper:
        adj_regime = -10 if is_short else +5
    elif 'BEAR_CRASH' in reg_upper:
        adj_regime = +15 if is_short else -30
    elif 'BULL_TREND' in reg_upper:
        adj_regime = -30 if is_short else +8
    elif 'BULL_EARLY' in reg_upper:
        adj_regime = -20 if is_short else +5
    elif 'CHOP' in reg_upper:
        adj_regime = -15
    elif reg_upper in ('', 'UNKNOWN', 'DEFAULT'):
        adj_regime = -5

    # ── 信号新鲜度调整 ────────────────────────────────────
    adj_fresh = 0
    age_h     = 0.0
    if ts_str:
        try:
            ts_epoch = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).timestamp()
            age_h    = (time.time() - ts_epoch) / 3600
            if age_h <= 1:      adj_fresh = 0
            elif age_h <= 4:    adj_fresh = -3
            elif age_h <= 8:    adj_fresh = -8
            elif age_h <= 24:   adj_fresh = -15
            elif age_h <= 48:   adj_fresh = -22
            else:               adj_fresh = -30   # 超48H，重度惩罚
        except:
            adj_fresh = -10

    # ── 流动性调整 ────────────────────────────────────────
    tier = VOL_TIER.get(sym, 'LOW')
    adj_liquidity = {'HIGH': 0, 'MED': -3, 'LOW': -15}[tier]

    # ── 噪音品种惩罚 ──────────────────────────────────────
    adj_noisy = -50 if sym in NOISY_SYMS else 0

    # ── 汇总 ─────────────────────────────────────────────
    ep = int(base_score + adj_gap + adj_fr + adj_regime + adj_fresh + adj_liquidity + adj_noisy)

    parts = []
    if adj_gap:    parts.append(f'gap{adj_gap:+d}')
    if adj_fr:     parts.append(f'FR{adj_fr:+d}')
    if adj_regime: parts.append(f'体制{adj_regime:+d}')
    if adj_fresh:  parts.append(f'鲜度{adj_fresh:+d}')
    if adj_liquidity: parts.append(f'流动{adj_liquidity:+d}')
    if adj_noisy:  parts.append(f'噪音{adj_noisy:+d}')
    breakdown = ' '.join(parts) if parts else '无调整'

    return {
        'ep':             ep,
        'base':           base_score,
        'adj_gap':        adj_gap,
        'adj_fr':         adj_fr,
        'adj_regime':     adj_regime,
        'adj_fresh':      adj_fresh,
        'adj_liquidity':  adj_liquidity,
        'adj_noisy':      adj_noisy,
        'gap_pct':        round(gap_pct, 3),
        'fr_pct':         round(fr, 4),
        'age_h':          round(age_h, 1),
        'price':          price,
        'breakdown':      breakdown,
    }


def rank_signals(signals: list, fetch_live: bool = True) -> list:
    """
    对信号列表计算EP Score并排序（降序）。
    每个信号追加 _ep 字段（calc_ep返回的dict）。
    """
    results = []
    for sig in signals:
        sym   = sig.get('symbol', '')
        price = _get_price(sym) if fetch_live else 0
        fr    = _get_fr(sym) if fetch_live else None
        ep    = calc_ep(sig, price=price, fr=fr)
        sig   = dict(sig)
        sig['_ep'] = ep
        sig['_price'] = ep['price']
        results.append(sig)
    results.sort(key=lambda x: -x['_ep']['ep'])
    return results


if __name__ == '__main__':
    # 快速自测
    test_sig = {
        'symbol': 'DOGEUSDT', 'score': 180, 'regime': 'BEAR_TREND',
        'signal_dir': 'SHORT', 'ts': '2026-06-06T07:00:00Z',
        'entry_lo': 0.0817,
    }
    r = calc_ep(test_sig)
    print(f"DOGE EP={r['ep']}  (base={r['base']} {r['breakdown']})")
    print(f"  gap={r['gap_pct']:+.2f}%  FR={r['fr_pct']:+.4f}%  age={r['age_h']:.1f}H")
