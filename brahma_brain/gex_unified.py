#!/usr/bin/env python3
# ponytail: gex_engine 352行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
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
  from brahma_brain.gex_unified import GEXEngine
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


# ══ 合并自 gex_scanner.py ══
#!/usr/bin/env python3
# ponytail: gex_scanner 516行，有意为之，重构前先 grep 所有调用方
"""
brahma_brain/gex_scanner.py — GEX自动计算引擎
设计院·苏摩111 2026-06-30

功能：
  每4H自动抓取 Deribit 期权链
  计算每个行权价的 GEX = Gamma × OI × Price² × 0.01 × 方向
  输出 MAX GEX（最大正值）/ MIN GEX（最小负值）/ ZeroFlip价位
  写入 data/gex_state.json，供 brahma_core s22因子和梵天360 D10使用

GEX计算原理：
  - Call GEX = +Gamma × OI × Spot²  (做市商持有Call → 需要卖空标的对冲)
  - Put  GEX = -Gamma × OI × Spot²  (做市商持有Put  → 需要买入标的对冲)
  - 净GEX > 0 → 做市商净多 → 价格被压制在区间（引力）
  - 净GEX < 0 → 做市商净空 → 价格波动被放大（排斥）
  - MAX GEX = 净GEX最大值对应的行权价（最强压制区）
  - MIN GEX = 净GEX最小值对应的行权价（极端下行支撑）
  - ZeroFlip = 净GEX从正变负的临界价位（方向转换点）

使用：
  python3 gex_scanner.py            # 扫描BTC+ETH
  python3 gex_scanner.py --symbol BTC
  python3 gex_scanner.py --symbol ETH
  from brahma_brain.gex_unified import get_gex_state, scan_gex
"""

import json, time, math, sys, os
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from data_cache import _SSL_CTX as _DC_SSL_CTX

# ── 路径 ─────────────────────────────────────────────────────
_DIR  = Path(__file__).parent
_ROOT = _DIR.parent
_DATA = _ROOT / 'data'
_GEX_STATE_FILE = _DATA / 'gex_state.json'

# ── 配置 ─────────────────────────────────────────────────────
DERIBIT_BASE  = "https://www.deribit.com/api/v2/public"
CACHE_TTL_SEC = 14400     # 4小时缓存
MAX_EXPIRY_DAYS = 45      # 只看45天内到期合约（流动性最好）
MIN_OI_FILTER  = 0.1      # 最小OI过滤（BTC单位）
GAMMA_FALLBACK_IV = 0.70  # 无法取到greeks时的备用隐含波动率


# ════════════════════════════════════════════════════════════════
# 数据获取层
# ════════════════════════════════════════════════════════════════

def _fetch(url: str, timeout: int = 12) -> dict:
    """Deribit公开API请求"""
    req = urllib.request.Request(url, headers={'User-Agent': 'BrahmaGEXScanner/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_DC_SSL_CTX) as r:
            return json.loads(r.read())
    except Exception as e:
        pass  # [静默]
        return {}


def get_spot_price(currency: str) -> float:
    """获取Deribit指数价格"""
    idx_name = f"{currency.lower()}_usd"
    r = _fetch(f"{DERIBIT_BASE}/get_index_price?index_name={idx_name}")
    return r.get('result', {}).get('index_price', 0.0)


def get_option_instruments(currency: str) -> list:
    """获取未到期期权合约列表"""
    url = f"{DERIBIT_BASE}/get_instruments?currency={currency}&kind=option&expired=false"
    r = _fetch(url)
    instruments = r.get('result', [])

    # 过滤：只保留45天内到期
    now_ms = time.time() * 1000
    cutoff = now_ms + MAX_EXPIRY_DAYS * 86400 * 1000
    near = [i for i in instruments if i.get('expiration_timestamp', 0) <= cutoff]
    return near


def get_book_summary(currency: str) -> list:
    """批量获取期权OI和价格数据（一次API调用）"""
    url = f"{DERIBIT_BASE}/get_book_summary_by_currency?currency={currency}&kind=option"
    r = _fetch(url)
    return r.get('result', [])


def get_ticker_greeks(instrument_name: str) -> dict:
    """获取单个合约的greeks（含gamma）— 仅在需要精确值时调用"""
    url = f"{DERIBIT_BASE}/ticker?instrument_name={instrument_name}"
    r = _fetch(url)
    result = r.get('result', {})
    return result.get('greeks', {})


# ════════════════════════════════════════════════════════════════
# GEX计算引擎
# ════════════════════════════════════════════════════════════════

def black_scholes_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes Gamma计算
    S: 现货价格  K: 行权价  T: 到期时间(年)
    r: 无风险利率  sigma: 隐含波动率
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        phi_d1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
        gamma = phi_d1 / (S * sigma * math.sqrt(T))
        return gamma
    except Exception:
        return 0.0


def compute_gex_profile(books: list, instruments: list, spot: float, currency: str) -> dict:
    """
    计算完整GEX分布图谱
    
    返回：
      strike_gex: {行权价: 净GEX值}
      max_gex_strike: MAX GEX价位
      min_gex_strike: MIN GEX价位
      zero_flip: GEX从正变负的临界价位
      total_positive_gex: 总正GEX
      total_negative_gex: 总负GEX
      net_gex: 当前净GEX（spot价位）
    """
    if not spot or not books:
        return {}

    # 构建 instrument_name → book数据 映射
    book_map = {b['instrument_name']: b for b in books}

    # 构建 instrument_name → 合约元数据 映射
    ins_map = {i['instrument_name']: i for i in instruments}

    now_sec = time.time()
    r_rate  = 0.05   # 无风险利率5%（近似）

    strike_gex = {}   # {strike: net_gex}
    strike_call_gex = {}
    strike_put_gex  = {}

    processed = 0
    for name, book in book_map.items():
        oi = book.get('open_interest', 0)
        if oi < MIN_OI_FILTER:
            continue

        # 从合约名称解析行权价和Call/Put
        # 格式: BTC-30JUN26-60000-C
        parts = name.split('-')
        if len(parts) != 4:
            continue
        option_type = parts[3]   # 'C' or 'P'
        try:
            strike = float(parts[2])
        except ValueError:
            continue

        # 过滤太远离现价的合约（现价±60%）
        if strike < spot * 0.40 or strike > spot * 1.60:
            continue

        # 获取到期时间
        ins_data = ins_map.get(name, {})
        exp_ms   = ins_data.get('expiration_timestamp', 0)
        if exp_ms <= 0:
            continue
        T = (exp_ms / 1000 - now_sec) / (365.25 * 86400)  # 转换为年
        if T <= 0:
            continue

        # 获取IV（用mid_iv或mark_iv）
        sigma = book.get('mid_iv', book.get('mark_iv', 0)) / 100.0
        if sigma <= 0.01:
            sigma = GAMMA_FALLBACK_IV

        # 计算Gamma（BS公式）
        gamma = black_scholes_gamma(spot, strike, T, r_rate, sigma)
        if gamma <= 0:
            continue

        # GEX贡献 = Gamma × OI × Spot² × 0.01
        # 单位：美元/点（反映价格移动1%时做市商对冲规模）
        gex_contrib = gamma * oi * spot * spot * 0.01

        # Call = 正GEX（做市商持有call → 卖出现货对冲）
        # Put  = 负GEX（做市商持有put  → 买入现货对冲）
        if option_type == 'C':
            strike_call_gex[strike] = strike_call_gex.get(strike, 0) + gex_contrib
        else:
            strike_put_gex[strike]  = strike_put_gex.get(strike,  0) + gex_contrib

        processed += 1

    # 合并计算净GEX
    all_strikes = sorted(set(list(strike_call_gex.keys()) + list(strike_put_gex.keys())))
    for s in all_strikes:
        call_gex = strike_call_gex.get(s, 0)
        put_gex  = strike_put_gex.get(s,  0)
        strike_gex[s] = call_gex - put_gex

    if not strike_gex:
        return {}

    # MAX GEX（净GEX最大值 → 最强压制区）
    max_gex_strike = max(strike_gex, key=lambda s: strike_gex[s])
    # MIN GEX（净GEX最小值 → 极端下行支撑/加速区）
    min_gex_strike = min(strike_gex, key=lambda s: strike_gex[s])

    # ZeroFlip（GEX从正变负的临界价位）
    zero_flip = None
    sorted_strikes = sorted(strike_gex.keys())
    for i in range(len(sorted_strikes) - 1):
        s1, s2 = sorted_strikes[i], sorted_strikes[i+1]
        g1, g2 = strike_gex[s1], strike_gex[s2]
        if g1 > 0 and g2 < 0:
            # 线性插值
            zero_flip = round(s1 + (s2 - s1) * g1 / (g1 - g2), 0)
            break
        elif g1 < 0 and g2 > 0:
            zero_flip = round(s1 + (s2 - s1) * abs(g1) / (abs(g1) + abs(g2)), 0)
            break

    # 当前spot价位的净GEX（插值）
    net_gex_at_spot = 0.0
    for i, s in enumerate(sorted_strikes):
        if s >= spot:
            if i > 0:
                s0 = sorted_strikes[i-1]
                t = (spot - s0) / (s - s0)
                net_gex_at_spot = strike_gex[s0] * (1-t) + strike_gex[s] * t
            else:
                net_gex_at_spot = strike_gex[s]
            break

    total_pos = sum(v for v in strike_gex.values() if v > 0)
    total_neg = sum(v for v in strike_gex.values() if v < 0)

    # 计算GEX区间位置百分位
    min_s = min_gex_strike
    max_s = max_gex_strike
    pos_pct = round((spot - min_s) / (max_s - min_s) * 100, 1) if max_s != min_s else 50.0

    return {
        'currency':          currency,
        'spot':              round(spot, 2),
        'max_gex_strike':    round(max_gex_strike, 0),
        'min_gex_strike':    round(min_gex_strike, 0),
        'zero_flip':         round(zero_flip, 0) if zero_flip else None,
        'net_gex_at_spot':   round(net_gex_at_spot, 2),
        'total_positive_gex': round(total_pos, 2),
        'total_negative_gex': round(total_neg, 2),
        'gex_direction':     'POSITIVE' if net_gex_at_spot > 0 else 'NEGATIVE',
        'spot_pos_pct':      pos_pct,   # spot在MAX/MIN GEX区间的位置
        'dist_to_max_pct':   round((max_gex_strike - spot) / spot * 100, 2),
        'dist_to_min_pct':   round((spot - min_gex_strike) / spot * 100, 2),
        'contracts_processed': processed,
        # Fib levels
        'fib_786':  round(min_s + (max_s - min_s) * 0.786, 0),
        'fib_618':  round(min_s + (max_s - min_s) * 0.618, 0),
        'fib_500':  round((max_s + min_s) / 2, 0),
        'fib_382':  round(min_s + (max_s - min_s) * 0.382, 0),
        'fib_236':  round(min_s + (max_s - min_s) * 0.236, 0),
        # 原始分布（TOP20行权价）
        'top_strikes': {
            str(int(k)): round(v, 4)
            for k, v in sorted(strike_gex.items(), key=lambda x: abs(x[1]), reverse=True)[:20]
        },
    }


# ════════════════════════════════════════════════════════════════
# 主扫描入口
# ════════════════════════════════════════════════════════════════

def scan_gex(currency: str = 'BTC', force: bool = False) -> dict:
    """
    扫描指定币种的GEX分布
    force=False: 读缓存（TTL 4H）
    force=True:  强制重新计算
    """
    _DATA.mkdir(exist_ok=True)
    currency = currency.upper()

    # 读缓存
    if not force and _GEX_STATE_FILE.exists():
        try:
            state = json.loads(_GEX_STATE_FILE.read_text())
            cached = state.get(currency, {})
            age = time.time() - cached.get('scan_ts', 0)
            if age < CACHE_TTL_SEC:
                pass  # [静默]
                return cached
        except Exception:
            pass

    pass  # [静默]
    t0 = time.time()

    # 获取数据
    spot        = get_spot_price(currency)
    instruments = get_option_instruments(currency)
    books       = get_book_summary(currency)

    if not spot or not books:
        pass  # [静默]
        return {}

    pass  # [静默]

    # 计算GEX
    profile = compute_gex_profile(books, instruments, spot, currency)
    if not profile:
        pass  # [静默]
        return {}

    # 写入状态文件
    profile['scan_ts']       = time.time()
    profile['scan_datetime'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    profile['elapsed_sec']   = round(time.time() - t0, 2)

    state = {}
    if _GEX_STATE_FILE.exists():
        try:
            state = json.loads(_GEX_STATE_FILE.read_text())
        except Exception:
            pass
    state[currency] = profile
    _GEX_STATE_FILE.write_text(json.dumps(state, indent=2))

    # ── [设计院 2026-08-13] GEX历史追加写入，供Historical GEX图表使用 ──
    _GEX_HISTORY_FILE = _DATA / 'gex_history.jsonl'
    try:
        hist_record = {
            'ts': profile['scan_ts'],
            'dt': profile['scan_datetime'],
            'currency': currency,
            'spot': profile['spot'],
            'max_gex_strike': profile['max_gex_strike'],
            'min_gex_strike': profile['min_gex_strike'],
            'zero_flip': profile.get('zero_flip'),
            'net_gex_at_spot': profile['net_gex_at_spot'],
            'total_positive_gex': profile['total_positive_gex'],
            'total_negative_gex': profile['total_negative_gex'],
            'gex_direction': profile['gex_direction'],
            'spot_pos_pct': profile['spot_pos_pct'],
        }
        with open(_GEX_HISTORY_FILE, 'a') as _hf:
            _hf.write(json.dumps(hist_record) + '\n')
    except Exception:
        pass  # [静默] 历史写入失败不阻断主流程

    pass  # [静默]
    print(f'  MAX GEX: ${profile["max_gex_strike"]:,.0f}  '
          f'MIN GEX: ${profile["min_gex_strike"]:,.0f}  '
          f'ZeroFlip: ${profile.get("zero_flip","?"):,.0f}' if profile.get("zero_flip") else
          f'  MAX GEX: ${profile["max_gex_strike"]:,.0f}  MIN GEX: ${profile["min_gex_strike"]:,.0f}')
    print(f'  当前位置: {profile["spot_pos_pct"]:.1f}%分位  '
          f'距MAX: {profile["dist_to_max_pct"]:+.1f}%  '
          f'GEX方向: {profile["gex_direction"]}')
    return profile


def scan_all(force: bool = False) -> dict:
    """扫描BTC+ETH，返回完整状态"""
    results = {}
    for currency in ['BTC', 'ETH']:
        results[currency] = scan_gex(currency, force=force)
    return results


def get_gex_state(currency: str = 'BTC') -> dict:
    """
    读取缓存的GEX状态（供brahma_core s22因子调用）
    返回空dict表示数据不可用
    """
    if not _GEX_STATE_FILE.exists():
        return {}
    try:
        state = json.loads(_GEX_STATE_FILE.read_text())
        return state.get(currency.upper(), {})
    except Exception:
        return {}


def get_gex_score_for_signal(currency: str, direction: str, spot: float = None) -> tuple:
    """
    供 brahma_core confluence_score 调用
    返回 (score_adj, description)
    
    逻辑：
      做空 + 当前在GEX区间80%+分位 + GEX负值 → +10分（顺势）
      做空 + 接近MAX GEX（<3%）           → +8分（压力确认）
      做多 + 接近MIN GEX（<5%）           → +8分（支撑确认）
      做空 + GEX正值（被压制区间）         → -5分（逆势）
    """
    gex = get_gex_state(currency)
    if not gex or not gex.get('max_gex_strike'):
        return 0, '无GEX数据'

    score = 0
    notes = []
    pos_pct       = gex.get('spot_pos_pct', 50)
    gex_direction = gex.get('gex_direction', 'POSITIVE')
    dist_max      = gex.get('dist_to_max_pct', 10)
    dist_min      = gex.get('dist_to_min_pct', 50)
    max_s         = gex.get('max_gex_strike', 0)
    min_s         = gex.get('min_gex_strike', 0)
    zero_flip     = gex.get('zero_flip')

    if direction == 'SHORT':
        if pos_pct >= 80 and gex_direction == 'NEGATIVE':
            score += 10
            notes.append(f'GEX{pos_pct:.0f}%分位+负值顺势空')
        elif pos_pct >= 80:
            score += 6
            notes.append(f'GEX{pos_pct:.0f}%分位接近MAX压力')
        if abs(dist_max) <= 3.0:
            score += 8
            notes.append(f'距MAX GEX ${max_s:,.0f}仅{dist_max:.1f}%')
        if gex_direction == 'POSITIVE' and pos_pct < 60:
            score -= 5
            notes.append('GEX正值区间中部，做空逆势')

    elif direction == 'LONG':
        if pos_pct <= 20 and gex_direction == 'POSITIVE':
            score += 10
            notes.append(f'GEX{pos_pct:.0f}%分位+正值顺势多')
        if abs(dist_min) <= 5.0:
            score += 8
            notes.append(f'距MIN GEX ${min_s:,.0f}仅{dist_min:.1f}%')
        if gex_direction == 'NEGATIVE' and pos_pct > 40:
            score -= 5
            notes.append('GEX负值区间，做多逆势')

    desc = ' | '.join(notes) if notes else f'GEX中性(pos={pos_pct:.0f}%)'
    return score, desc


# ════════════════════════════════════════════════════════════════
# 格式化报告输出
# ════════════════════════════════════════════════════════════════

def format_gex_report(gex: dict) -> str:
    """格式化GEX分析报告（供Jarvis推送）"""
    if not gex:
        return '❌ GEX数据不可用'

    cur   = gex.get('currency', '?')
    spot  = gex.get('spot', 0)
    max_s = gex.get('max_gex_strike', 0)
    min_s = gex.get('min_gex_strike', 0)
    zero  = gex.get('zero_flip')
    pos   = gex.get('spot_pos_pct', 0)
    direc = gex.get('gex_direction', '?')
    d_max = gex.get('dist_to_max_pct', 0)
    d_min = gex.get('dist_to_min_pct', 0)
    f786  = gex.get('fib_786', 0)
    f618  = gex.get('fib_618', 0)
    f500  = gex.get('fib_500', 0)
    dt    = gex.get('scan_datetime', '?')

    icon = '🔴' if direc == 'NEGATIVE' else '🟢'
    lines = [
        f"📊 {cur}/USD GEX分析 | {dt}",
        f"",
        f"现价:    ${spot:,.2f}  ({pos:.1f}%分位)",
        f"MAX GEX: ${max_s:,.0f}  (距现价 {d_max:+.1f}%)",
        f"MIN GEX: ${min_s:,.0f}  (距现价 -{d_min:.1f}%)",
        f"ZeroFlip: ${zero:,.0f}" if zero else "ZeroFlip: 未检测到",
        f"",
        f"GEX方向: {icon} {direc}",
        f"  → {'做市商净空头，波动放大，利于趋势行情' if direc == 'NEGATIVE' else '做市商净多头，价格被压制，区间震荡'}",
        f"",
        f"Fib支撑阻力:",
        f"  Fib 78.6%: ${f786:,.0f}",
        f"  Fib 61.8%: ${f618:,.0f}",
        f"  Fib 50%:   ${f500:,.0f} (GEX中点)",
    ]
    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天GEX扫描器')
    parser.add_argument('--symbol', default='ALL',  help='BTC/ETH/ALL')
    parser.add_argument('--force',  action='store_true', help='强制重新计算')
    parser.add_argument('--report', action='store_true', help='输出格式化报告')
    args = parser.parse_args()

    if args.symbol.upper() == 'ALL':
        results = scan_all(force=args.force)
    else:
        results = {args.symbol.upper(): scan_gex(args.symbol.upper(), force=args.force)}

    if args.report:
        for cur, gex in results.items():
            print('\n' + format_gex_report(gex))
    else:
        for cur, gex in results.items():
            if gex:
                print(f"\n{cur}: MAX=${gex['max_gex_strike']:,.0f} "
                      f"MIN=${gex['min_gex_strike']:,.0f} "
                      f"ZeroFlip=${gex.get('zero_flip','?')} "
                      f"方向={gex['gex_direction']} "
                      f"位置={gex['spot_pos_pct']:.1f}%")