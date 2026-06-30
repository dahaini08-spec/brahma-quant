#!/usr/bin/env python3
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
  from gex_scanner import get_gex_state, scan_gex
"""

import json, time, math, sys, os
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

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
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'[GEX] API请求失败: {url[:80]} → {e}')
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
                print(f'[GEX] {currency} 使用缓存 (更新于{age/60:.0f}分钟前)')
                return cached
        except Exception:
            pass

    print(f'[GEX] {currency} 开始扫描 Deribit 期权链...')
    t0 = time.time()

    # 获取数据
    spot        = get_spot_price(currency)
    instruments = get_option_instruments(currency)
    books       = get_book_summary(currency)

    if not spot or not books:
        print(f'[GEX] {currency} 数据获取失败')
        return {}

    print(f'[GEX] {currency} spot=${spot:,.2f} 合约数={len(instruments)} 有OI={sum(1 for b in books if b.get("open_interest",0)>0)}')

    # 计算GEX
    profile = compute_gex_profile(books, instruments, spot, currency)
    if not profile:
        print(f'[GEX] {currency} GEX计算失败（数据不足）')
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

    print(f'[GEX] {currency} 扫描完成 ({profile["elapsed_sec"]}s)')
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
