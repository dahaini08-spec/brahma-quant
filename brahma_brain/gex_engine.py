#!/usr/bin/env python3
"""
梵天 GEX 引擎 (s22维度)
Gamma Exposure Sentiment — 基于 Deribit 公开期权数据

GEX = Σ (sign × Gamma × OI × S² × 0.01)
  Call → +1 (做市商正Gamma，压制波动)
  Put  → -1 (做市商负Gamma，放大波动)

s22评分: -10 ~ +8
  负GEX + 与信号方向一致 → 放大波动，加分
  正GEX → 压制波动，减分

用法:
  python3 brahma_brain/gex_engine.py
  from brahma_brain.gex_engine import GEXEngine
  result = GEXEngine.score('BTCUSDT', 'SHORT')
"""
import math
import json
import time
import urllib.request
import urllib.error
import re
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

ROOT    = Path(__file__).parent.parent
CACHE_F = ROOT / 'data/gex_cache.json'
CACHE_TTL = 1800  # 30分钟缓存


# ─────────────────────────────────────────────────────────────────
# Black-Scholes Gamma
# ─────────────────────────────────────────────────────────────────
def bs_gamma(S: float, K: float, T: float, sigma: float, r: float = 0.05) -> float:
    if T <= 1e-6 or sigma <= 0 or K <= 0 or S <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        phi = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
        return phi / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError):
        return 0.0


# ─────────────────────────────────────────────────────────────────
# Deribit API
# ─────────────────────────────────────────────────────────────────
def _deribit_get(path: str, timeout: int = 10) -> Optional[dict]:
    url = f'https://www.deribit.com/api/v2/public/{path}'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read()).get('result')
    except Exception:
        return None


def _parse_instrument(name: str) -> Optional[dict]:
    """BTC-10JUN26-61000-C → {strike, type, exp_ms}"""
    m = re.match(r'(\w+)-(\d+\w+\d+)-(\d+)-(C|P)', name)
    if not m:
        return None
    _, exp_str, strike, opt_type = m.groups()
    try:
        exp_dt = datetime.strptime(exp_str, '%d%b%y').replace(tzinfo=timezone.utc)
        return {
            'strike':   float(strike),
            'type':     opt_type,
            'exp_ms':   int(exp_dt.timestamp() * 1000),
        }
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────
# GEX 计算核心
# ─────────────────────────────────────────────────────────────────
def compute_gex(currency: str = 'BTC',
                atm_range: float = 0.25) -> Optional[Dict]:
    """
    从 Deribit 获取期权数据并计算 GEX 分布

    返回:
        total_gex     : 总净GEX (USD)
        call_gex      : Call侧GEX
        put_gex       : Put侧GEX
        gex_by_strike : {strike: gex} 字典
        spot          : 当前标的价格
        zero_flip     : Zero Gamma Flip位置 (None=未找到)
        gamma_magnet  : 最大绝对GEX的Strike（价格磁铁）
        regime        : 'POSITIVE'/'NEGATIVE'
        ts            : 计算时间
    """
    # ── 检查缓存 ─────────────────────────────────────────
    if CACHE_F.exists():
        try:
            cached = json.loads(CACHE_F.read_text())
            if (time.time() - cached.get('_ts', 0)) < CACHE_TTL and \
               cached.get('currency') == currency:
                return cached
        except Exception:
            pass

    # ── 获取期权汇总 ──────────────────────────────────────
    data = _deribit_get(
        f'get_book_summary_by_currency?currency={currency}&kind=option'
    )
    if not data:
        return None

    spot = data[0].get('underlying_price', 0) if data else 0
    if spot <= 0:
        return None

    now_ms = int(time.time() * 1000)
    gex_by_strike: Dict[float, float] = {}
    total_gex = call_gex = put_gex = 0.0

    for opt in data:
        oi = float(opt.get('open_interest', 0))
        if oi <= 0:
            continue

        p = _parse_instrument(opt['instrument_name'])
        if not p:
            continue

        K = p['strike']
        if abs(K / spot - 1) > atm_range:      # 只看ATM±25%
            continue

        T = (p['exp_ms'] - now_ms) / (1000 * 86400 * 365)
        if T <= 0:
            continue

        iv = float(opt.get('mark_iv', 70)) / 100
        if iv <= 0.01:
            iv = 0.7

        gamma = bs_gamma(spot, K, T, iv)
        sign  = 1.0 if p['type'] == 'C' else -1.0
        gex   = sign * gamma * oi * spot * spot * 0.01

        gex_by_strike[K] = gex_by_strike.get(K, 0.0) + gex
        total_gex += gex
        if p['type'] == 'C':
            call_gex += gex
        else:
            put_gex += gex

    # ── Zero Gamma Flip ─────────────────────────────────
    zero_flip = None
    if gex_by_strike:
        sorted_k = sorted(gex_by_strike.keys())
        cum = 0.0
        for K in sorted_k:
            prev = cum
            cum += gex_by_strike[K]
            if prev != 0 and prev * cum < 0:
                zero_flip = K
                break

    # ── Gamma Magnet ─────────────────────────────────────
    gamma_magnet = None
    if gex_by_strike:
        gamma_magnet = max(gex_by_strike, key=lambda k: abs(gex_by_strike[k]))

    result = {
        'currency':      currency,
        'spot':          round(spot, 2),
        'total_gex':     round(total_gex),
        'call_gex':      round(call_gex),
        'put_gex':       round(put_gex),
        'gex_by_strike': {str(k): round(v) for k, v in sorted(gex_by_strike.items())},
        'zero_flip':     zero_flip,
        'gamma_magnet':  gamma_magnet,
        'regime':        'POSITIVE' if total_gex >= 0 else 'NEGATIVE',
        'n_strikes':     len(gex_by_strike),
        'ts':            datetime.now(timezone.utc).isoformat(),
        '_ts':           time.time(),
    }

    # 缓存
    try:
        CACHE_F.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass

    return result


# ─────────────────────────────────────────────────────────────────
# s22 评分逻辑
# ─────────────────────────────────────────────────────────────────
def score_gex(symbol: str, direction: str,
              gex_data: Optional[Dict] = None) -> Dict:
    """
    s22 GEX 评分: -10 ~ +8

    规则:
      负GEX regime（做市商空Gamma，放大波动）:
        SHORT 方向 → 波动放大有利于做空 → +4~+8
        LONG  方向 → 波动放大不利于持多 → -2~-4

      正GEX regime（做市商多Gamma，压制波动）:
        SHORT 方向 → 波动被压制，趋势难延续 → -4~-6
        LONG  方向 → 震荡偏多，价格稳定 → +1~+2

      强度根据 |total_gex| 分级
    """
    # 确定货币
    currency = 'BTC' if 'BTC' in symbol.upper() else \
               'ETH' if 'ETH' in symbol.upper() else 'BTC'

    if gex_data is None:
        gex_data = compute_gex(currency)

    if not gex_data:
        return {'s22': 0, 'reason': 'GEX数据获取失败', 'regime': 'UNKNOWN'}

    total_gex    = gex_data['total_gex']
    regime       = gex_data['regime']
    spot         = gex_data['spot']
    zero_flip    = gex_data.get('zero_flip')
    gamma_magnet = gex_data.get('gamma_magnet')

    # GEX强度分级（USD）
    abs_gex = abs(total_gex)
    if abs_gex > 500_000_000:
        strength = 'EXTREME'
    elif abs_gex > 200_000_000:
        strength = 'STRONG'
    elif abs_gex > 50_000_000:
        strength = 'MODERATE'
    else:
        strength = 'WEAK'

    # 距 Zero Flip 距离
    flip_dist_pct = None
    if zero_flip and spot > 0:
        flip_dist_pct = (zero_flip / spot - 1) * 100

    # 评分矩阵
    score_map = {
        # (regime, direction, strength)
        ('NEGATIVE', 'SHORT', 'EXTREME'):  +8,
        ('NEGATIVE', 'SHORT', 'STRONG'):   +6,
        ('NEGATIVE', 'SHORT', 'MODERATE'): +4,
        ('NEGATIVE', 'SHORT', 'WEAK'):     +2,
        ('NEGATIVE', 'LONG',  'EXTREME'):  -8,
        ('NEGATIVE', 'LONG',  'STRONG'):   -6,
        ('NEGATIVE', 'LONG',  'MODERATE'): -4,
        ('NEGATIVE', 'LONG',  'WEAK'):     -2,
        ('POSITIVE', 'SHORT', 'EXTREME'):  -8,
        ('POSITIVE', 'SHORT', 'STRONG'):   -5,
        ('POSITIVE', 'SHORT', 'MODERATE'): -3,
        ('POSITIVE', 'SHORT', 'WEAK'):     -1,
        ('POSITIVE', 'LONG',  'EXTREME'):  +4,
        ('POSITIVE', 'LONG',  'STRONG'):   +3,
        ('POSITIVE', 'LONG',  'MODERATE'): +2,
        ('POSITIVE', 'LONG',  'WEAK'):     +1,
    }

    dir_key = direction.upper()
    s22 = score_map.get((regime, dir_key, strength), 0)

    # 额外加分：价格接近 Zero Flip（±1.5%），波动率即将爆发
    flip_bonus = 0
    if flip_dist_pct is not None and abs(flip_dist_pct) < 1.5:
        flip_bonus = +2 if dir_key == 'SHORT' else +1
        s22 += flip_bonus

    # 构建原因说明
    gex_bn = total_gex / 1_000_000
    reason_parts = [
        f'GEX={gex_bn:+.1f}M({regime})',
        f'强度={strength}',
        f's22={s22:+d}',
    ]
    if flip_dist_pct is not None:
        reason_parts.append(f'ZeroFlip距离{flip_dist_pct:+.1f}%')
    if gamma_magnet:
        magnet_dist = (gamma_magnet / spot - 1) * 100
        reason_parts.append(f'磁铁Strike=${gamma_magnet:,.0f}({magnet_dist:+.1f}%)')

    return {
        's22':           s22,
        'regime':        regime,
        'strength':      strength,
        'total_gex_m':   round(gex_bn, 2),
        'zero_flip':     zero_flip,
        'flip_dist_pct': round(flip_dist_pct, 2) if flip_dist_pct else None,
        'gamma_magnet':  gamma_magnet,
        'reason':        ' | '.join(reason_parts),
        'spot':          spot,
        'ts':            gex_data.get('ts', ''),
    }


# ─────────────────────────────────────────────────────────────────
# 主程序 / CLI
# ─────────────────────────────────────────────────────────────────
def main():
    print(f'\n🏯 梵天 GEX 引擎 (s22维度)')
    print(f'   {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'   数据源: Deribit 公开期权 API\n')

    for currency, symbol in [('BTC', 'BTCUSDT'), ('ETH', 'ETHUSDT')]:
        print(f'{"─"*52}')
        print(f'  计算 {currency} GEX...')
        gex = compute_gex(currency)
        if not gex:
            print(f'  ❌ 数据获取失败')
            continue

        print(f'  现价:         ${gex["spot"]:,.2f}')
        print(f'  总GEX:        {gex["total_gex"]/1e6:+.1f}M USD')
        print(f'  Call GEX:     {gex["call_gex"]/1e6:+.1f}M')
        print(f'  Put  GEX:     {gex["put_gex"]/1e6:+.1f}M')
        print(f'  体制:         {gex["regime"]}')
        print(f'  Strike覆盖:   {gex["n_strikes"]}个')
        print(f'  Zero Flip:    ${gex["zero_flip"]:,.0f}' if gex['zero_flip'] else '  Zero Flip:    未找到')
        print(f'  Gamma磁铁:    ${gex["gamma_magnet"]:,.0f}' if gex['gamma_magnet'] else '')

        # GEX柱状图（文字版，TOP 8 Strike）
        items = sorted(gex['gex_by_strike'].items(),
                       key=lambda x: abs(float(x[1])), reverse=True)[:8]
        print(f'\n  GEX分布 TOP8 Strike:')
        max_abs = max(abs(float(v)) for _, v in items) if items else 1
        for k, v in sorted(items, key=lambda x: float(x[0])):
            v = float(v)
            bar_len = int(abs(v) / max_abs * 20)
            bar = ('█' * bar_len) if v > 0 else ('░' * bar_len)
            dist = (float(k) / gex['spot'] - 1) * 100
            flag = ' ← 现价' if abs(dist) < 0.3 else ''
            print(f'    ${float(k):>8,.0f} ({dist:+5.1f}%)  {v/1e6:+7.1f}M  {"+" if v>0 else "-"}{bar}{flag}')

        # s22评分
        for direction in ['SHORT', 'LONG']:
            s = score_gex(symbol, direction, gex)
            print(f'\n  s22 {direction:5}: {s["s22"]:+3d}  {s["reason"]}')

    print()


if __name__ == '__main__':
    main()
