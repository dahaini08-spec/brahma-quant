#!/usr/bin/env python3
"""
pump_short_scanner.py · 暴涨标的做空时机扫描器 v1.0
设计院 · 2026-06-21 | 审核缺陷D1-D6全部落地

核心修复：
  D1: 体制门控（第一约束，非技术指标）
  D2: 评分模型引入体制乘数
  D3: FR<0 AND LSR<1 联合门控
  D4: 流动性门控（<$50M降权，<$20M排除）
  D5: Fib基准改为14天日线最低点
  D6: RSI顶部背离检测

用法：
  python3 scripts/pump_short_scanner.py                    # 扫描当前信号池 + 自动筛选
  python3 scripts/pump_short_scanner.py --symbols BICO ALICE  # 指定标的
  python3 scripts/pump_short_scanner.py --min-chg 20        # 仅分析24h涨幅>20%的标的
  python3 scripts/pump_short_scanner.py --dry               # 不写信号，仅打印
"""

import sys, os, json, argparse, time
import requests
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent

# ══════════════════════════════════════════════════
# 体制乘数矩阵（铁证，来自dharma离线回放6.5年）
# ══════════════════════════════════════════════════
REGIME_MULT = {
    'BEAR_TREND':     {'SHORT': 1.5, 'LONG': 0.0},
    'BEAR_EARLY':     {'SHORT': 1.5, 'LONG': 0.5},
    'BEAR_RECOVERY':  {'SHORT': 0.4, 'LONG': 0.95},
    'BEAR_CORRECTION':{'SHORT': 1.0, 'LONG': 1.0},
    'BULL_TREND':     {'SHORT': 0.5, 'LONG': 1.5},
    'BULL_EARLY':     {'SHORT': 0.5, 'LONG': 1.5},
    'BULL_CORRECTION':{'SHORT': 1.0, 'LONG': 1.0},
    'CHOP_HIGH':      {'SHORT': 0.5, 'LONG': 0.5},
    'CHOP_MID':       {'SHORT': 0.5, 'LONG': 0.5},
    'CHOP_LOW':       {'SHORT': 0.5, 'LONG': 0.5},
}

# 铁证WR（BEAR_RECOVERY_SHORT = 次铁证死穴）
REGIME_SHORT_WR = {
    'BEAR_TREND':    ('WR=71.8%', '✅S级'),
    'BEAR_EARLY':    ('WR=66.5%', '✅S级'),
    'BEAR_RECOVERY': ('WR=47.9%', '❌死穴'),
    'BULL_TREND':    ('WR=47.7%', '❌最大死穴'),
    'BULL_EARLY':    ('WR=51.9%', '❌负期望'),
    'CHOP_MID':      ('WR≈57%',   '❌负EV'),
}

# 流动性门控
VOL_EXCLUDE_M  = 20    # 日成交<$20M → 排除
VOL_PENALTY_M  = 50    # 日成交<$50M → 评分×0.5

# ══════════════════════════════════════════════════
# 数据获取
# ══════════════════════════════════════════════════
def _get(url, params=None, timeout=6):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except:
        return None

def get_ticker(sym):
    return _get(f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={sym}') or {}

def get_klines(sym, interval, limit):
    return _get('https://fapi.binance.com/fapi/v1/klines',
                {'symbol': sym, 'interval': interval, 'limit': limit}) or []

def get_funding(sym):
    """获取实时资金费率"""
    d = _get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}')
    return float(d.get('lastFundingRate', 0)) if d else 0

def get_lsr(sym):
    """多空比（全局账户）"""
    d = _get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
             {'symbol': sym, 'period': '1h', 'limit': 3})
    return float(d[-1]['longShortRatio']) if d else None

def get_oi_delta(sym):
    """OI变化（12小时）"""
    d = _get('https://fapi.binance.com/futures/data/openInterestHist',
             {'symbol': sym, 'period': '1h', 'limit': 12})
    if not d or len(d) < 2: return 0
    oi_old = float(d[0].get('sumOpenInterest', 0))
    oi_new = float(d[-1].get('sumOpenInterest', 0))
    return (oi_new - oi_old) / oi_old * 100 if oi_old > 0 else 0

# ══════════════════════════════════════════════════
# 技术指标
# ══════════════════════════════════════════════════
def ema_last(data, n):
    if len(data) < 2: return data[-1]
    k = 2/(n+1); e = data[0]
    for d in data[1:]: e = d*k + e*(1-k)
    return e

def calc_rsi(closes, n=14):
    if len(closes) < n+2: return 50.0
    diffs = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    gs = [max(d, 0) for d in diffs[-n:]]
    ls = [max(-d, 0) for d in diffs[-n:]]
    ag = sum(gs)/n; al = sum(ls)/n or 1e-9
    return 100 - 100/(1+ag/al)

def detect_rsi_divergence(closes, highs, n=14):
    """
    D6修复：RSI顶部背离检测
    价格创新高 + RSI不创新高 → True
    检测最近3根K线
    """
    if len(closes) < 20: return False
    # 计算每根K线的RSI
    rsi_series = []
    for i in range(len(closes)):
        if i < n+1:
            rsi_series.append(50.0)
        else:
            rsi_series.append(calc_rsi(closes[max(0,i-n-5):i+1]))

    # 检测最近3对高点
    for i in range(-1, -4, -1):
        try:
            if highs[i] > highs[i-3] and rsi_series[i] < rsi_series[i-3]:
                return True  # 价格新高但RSI不创新高
        except IndexError:
            continue
    return False

def get_symbol_regime(sym):
    """
    D1修复：优先从 regime_state.json 获取 per-symbol 体制
    """
    try:
        rs_path = BASE / 'data' / 'regime_state.json'
        rs = json.loads(rs_path.read_text())
        sym_data = rs.get(sym, {})
        confirmed = sym_data.get('confirmed', '')
        if confirmed:
            return confirmed
    except:
        pass
    # fallback：系统全局体制
    try:
        bs_path = BASE / 'data' / 'brahma_state.json'
        bs = json.loads(bs_path.read_text())
        return bs.get('regime_label') or bs.get('regime', 'UNKNOWN')
    except:
        return 'UNKNOWN'

# ══════════════════════════════════════════════════
# 核心评分（含体制乘数）
# ══════════════════════════════════════════════════
def score_short(sym, data):
    """
    返回：(final_score, raw_score, regime_mult, reasons, warnings, detail)
    """
    cur     = data['cur']
    rsi15   = data['rsi15']
    rsi1h   = data['rsi1h']
    rsi4h   = data['rsi4h']
    ema20   = data['ema20_1h']
    pump_h  = data['pump_high']
    pump_l  = data['pump_low_14d']   # D5修复：14天日线基准
    from_h  = data['from_high']
    vol_m   = data['vol_m']
    fr      = data['fr']
    lsr     = data['lsr']
    oi_d    = data['oi_delta']
    diverge = data['rsi_divergence']
    regime  = data['regime']

    reasons  = []
    warnings = []
    raw = 0

    # ── D4：流动性门控（最先检查）──────────────────
    if vol_m < VOL_EXCLUDE_M:
        return 0, 0, 0, [], [f'日成交${vol_m:.0f}M < ${VOL_EXCLUDE_M}M 流动性不足，强制排除'], {}
    liq_mult = 0.5 if vol_m < VOL_PENALTY_M else 1.0
    if liq_mult < 1.0:
        warnings.append(f'日成交${vol_m:.0f}M 较低，评分×0.5')

    # ── D3：FR + LSR 联合门控 ───────────────────────
    # FR<0（空方付息）AND LSR<1.0（空头拥挤）→ 直接返回0
    if fr < -0.0003 and lsr is not None and lsr < 1.0:
        return 0, 0, 0, [], [
            f'FR={fr*100:.4f}%（空方付息）+ LSR={lsr:.2f}（空头拥挤）→ 轧空风险，拒绝做空'
        ], {}

    # ── 技术评分（原始分）──────────────────────────

    # 1. RSI超买（30分）
    rsi_pts = 0
    if rsi15 > 85:  rsi_pts += 12; reasons.append(f'15M RSI={rsi15:.0f} 极度超买')
    elif rsi15 > 70: rsi_pts += 7;  reasons.append(f'15M RSI={rsi15:.0f} 超买')
    if rsi1h > 80:  rsi_pts += 10; reasons.append(f'1H RSI={rsi1h:.0f} 严重超买')
    elif rsi1h > 70: rsi_pts += 6;  reasons.append(f'1H RSI={rsi1h:.0f} 超买')
    if rsi4h > 80:  rsi_pts += 8;  reasons.append(f'4H RSI={rsi4h:.0f} 极度超买')
    elif rsi4h > 70: rsi_pts += 5;  reasons.append(f'4H RSI={rsi4h:.0f} 超买')
    raw += min(rsi_pts, 30)

    # D6：RSI背离（+15分加成）
    if diverge:
        raw += 15; reasons.append('🔴 RSI顶部背离（价格新高+RSI不创新高）')

    # 2. 从高点回调位置（15分）
    fh = abs(from_h)
    if 3 < fh < 12:   raw += 15; reasons.append(f'距高点回调{fh:.1f}%（顶部压力位）')
    elif fh <= 3:      raw += 8;  warnings.append(f'距高点仅{fh:.1f}%，可能继续拉升')
    elif fh < 20:      raw += 10; reasons.append(f'从高点回落{fh:.1f}%')
    else:              raw += 3;  warnings.append(f'已回调{fh:.1f}%，追空风险大')

    # 3. EMA偏离（均值回归压力）（20分）
    ema_gap = (cur - ema20) / ema20 * 100 if ema20 > 0 else 0
    if ema_gap > 20:   raw += 20; reasons.append(f'偏离1H EMA20 +{ema_gap:.0f}% 均值回归压力极大')
    elif ema_gap > 12: raw += 14; reasons.append(f'偏离1H EMA20 +{ema_gap:.0f}%')
    elif ema_gap > 6:  raw += 8;  reasons.append(f'偏离1H EMA20 +{ema_gap:.0f}%')
    elif ema_gap < -5: raw -= 5;  warnings.append(f'价格已跌破EMA20，不追空')

    # 4. 资金费率（15分）
    if fr > 0.0008:    raw += 15; reasons.append(f'FR={fr*100:.4f}% 多方重度付息 ✅做空有利')
    elif fr > 0.0003:  raw += 10; reasons.append(f'FR={fr*100:.4f}% 多方付息')
    elif fr > 0.0001:  raw += 5;  reasons.append(f'FR={fr*100:.4f}% 轻微多方付息')
    elif fr < -0.0003: raw -= 3;  warnings.append(f'FR={fr*100:.4f}% 空方付息（轻度拥挤）')

    # 5. LSR（10分）
    if lsr is not None:
        if lsr > 2.5:   raw += 10; reasons.append(f'多空比={lsr:.2f} 多头极度拥挤')
        elif lsr > 1.8: raw += 7;  reasons.append(f'多空比={lsr:.2f} 散户偏多过热')
        elif lsr > 1.3: raw += 4;  reasons.append(f'多空比={lsr:.2f} 偏多')
        elif lsr < 0.8: raw -= 5;  warnings.append(f'多空比={lsr:.2f} 空头已拥挤')

    # 6. OI变化（10分）
    if oi_d > 30:     raw -= 3;  warnings.append(f'OI+{oi_d:.0f}% 新多头大量入场')
    elif oi_d < -10:  raw += 8;  reasons.append(f'OI{oi_d:.0f}% 多头离场')

    # 7. 成交量衰竭（10分）
    vol_r = data.get('vol_ratio', 1)
    if vol_r < 0.3:   raw += 8;  reasons.append(f'量比={vol_r:.2f} 严重缩量（顶部特征）')
    elif vol_r < 0.6: raw += 5;  reasons.append(f'量比={vol_r:.2f} 缩量')
    elif vol_r > 2.0: raw -= 3;  warnings.append(f'量比={vol_r:.2f} 仍放量')

    raw = max(0, min(100, raw))

    # ── D2：体制乘数 ───────────────────────────────
    mult = REGIME_MULT.get(regime, {}).get('SHORT', 0.5)
    raw_adj = raw * liq_mult  # 先乘流动性
    final = raw_adj * mult    # 再乘体制

    detail = {
        'raw': raw, 'liq_mult': liq_mult,
        'raw_adj': raw_adj, 'regime_mult': mult,
        'final': final, 'regime': regime,
        'ema_gap': ema_gap, 'from_high': from_h,
    }
    return final, raw, mult, reasons, warnings, detail


# ══════════════════════════════════════════════════
# Fibonacci（D5修复：14天日线基准）
# ══════════════════════════════════════════════════
def calc_fib(pump_low_14d, pump_high):
    span = pump_high - pump_low_14d
    return {
        '0.236': pump_high - span * 0.236,
        '0.382': pump_high - span * 0.382,
        '0.500': pump_high - span * 0.500,
        '0.618': pump_high - span * 0.618,
        '0.786': pump_high - span * 0.786,
    }


# ══════════════════════════════════════════════════
# 数据采集
# ══════════════════════════════════════════════════
def collect(sym):
    tk   = get_ticker(sym)
    k15  = get_klines(sym, '15m', 96)
    k1h  = get_klines(sym, '1h',  72)
    k4h  = get_klines(sym, '4h',  30)
    k1d  = get_klines(sym, '1d',  14)   # D5：14天日线

    if not k1h: return None

    def p(klines):
        return {
            'c': [float(k[4]) for k in klines],
            'h': [float(k[2]) for k in klines],
            'l': [float(k[3]) for k in klines],
            'v': [float(k[5]) for k in klines],
        }

    d15 = p(k15); d1h = p(k1h); d4h = p(k4h)
    d1d_c = [float(k[4]) for k in k1d]
    d1d_l = [float(k[3]) for k in k1d]

    cur       = d1h['c'][-1]
    pump_high = max(d1h['h'][-24:])
    pump_low_48h = min(d1h['l'][-48:])                      # 旧基准（保留供对比）
    pump_low_14d = min(d1d_l) if d1d_l else pump_low_48h   # D5新基准

    avg_vol = sum(d1h['v'][-168:]) / max(len(d1h['v'][-168:]), 1)

    return {
        'sym': sym,
        'cur': cur,
        'chg24': float(tk.get('priceChangePercent', 0)),
        'vol_m': float(tk.get('quoteVolume', 0)) / 1e6,
        'rsi15': calc_rsi(d15['c']),
        'rsi1h': calc_rsi(d1h['c']),
        'rsi4h': calc_rsi(d4h['c']),
        'ema20_1h': ema_last(d1h['c'], 20),
        'ema55_1h': ema_last(d1h['c'], 55),
        'pump_high': pump_high,
        'pump_low_48h': pump_low_48h,
        'pump_low_14d': pump_low_14d,
        'from_high': (cur - pump_high) / pump_high * 100,
        'vol_ratio': d1h['v'][-1] / avg_vol if avg_vol > 0 else 1,
        'fr':  get_funding(sym),
        'lsr': get_lsr(sym),
        'oi_delta': get_oi_delta(sym),
        'rsi_divergence': detect_rsi_divergence(d1h['c'], d1h['h']),  # D6
        'regime': get_symbol_regime(sym),                              # D1
        'fib_14d': calc_fib(pump_low_14d, pump_high),                 # D5
        'fib_48h': calc_fib(pump_low_48h, pump_high),                 # 对比用
    }


# ══════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════
GRADE = {(75, 200): '🔴 S级做空', (60, 75): '🟠 A级做空',
         (45, 60): '🟡 B级观察', (30, 45): '⚪ 等待确认', (0, 30): '❌ 不做空'}

def grade(score):
    for (lo, hi), label in GRADE.items():
        if lo <= score < hi: return label
    return '❌ 不做空'

def print_report(results):
    now = datetime.now(timezone.utc)
    print(f"\n{'='*68}")
    print(f"🧠 梵天 · Pump做空扫描器 v1.0  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*68}")
    print(f"{'标的':<16} {'现价':>10} {'24h%':>8} {'体制':>18} {'原始分':>7} {'×乘数':>7} {'最终分':>7} {'级别'}")
    print(f"{'─'*88}")

    for r in sorted(results, key=lambda x: -x['final']):
        sym  = r['sym']
        wr, tag = REGIME_SHORT_WR.get(r['detail']['regime'], ('WR=?', ''))
        regime_str = f"{r['detail']['regime']}({wr})"
        print(f"  {sym:<14} {r['data']['cur']:>10.5f} {r['data']['chg24']:>+7.1f}%  {regime_str:<24} "
              f"{r['raw']:>5.0f}  ×{r['mult']:>4.1f}  {r['final']:>6.0f}  {grade(r['final'])}")

    print()
    for r in sorted(results, key=lambda x: -x['final']):
        if r['final'] < 30 and not r.get('reasons'): continue
        sym = r['sym']
        d   = r['detail']
        fib = r['data']['fib_14d']
        print(f"\n{'━'*68}")
        print(f"📌 {sym}  最终分={r['final']:.0f}({grade(r['final'])})  原始分={r['raw']:.0f} × 体制乘数{r['mult']}x")
        print(f"   体制: {d['regime']}  SHORT乘数={d['regime_mult']}x  {REGIME_SHORT_WR.get(d['regime'],('',''))[0]}")
        print(f"   技术: 1H RSI={r['data']['rsi1h']:.0f} | 4H RSI={r['data']['rsi4h']:.0f} | EMA20偏离={d['ema_gap']:+.1f}% | 距高点={d['from_high']:+.1f}%")
        lsr_str = f"{r['data']['lsr']:.2f}" if r['data']['lsr'] else 'N/A'
        print(f"   衍生: FR={r['data']['fr']*100:+.4f}% | LSR={lsr_str} | OI={r['data']['oi_delta']:+.0f}% | 量比={r['data']['vol_ratio']:.2f}x")
        print(f"   Fib（14天日线基准）: 0.236={fib['0.236']:.5f} | 0.382={fib['0.382']:.5f} | 0.618={fib['0.618']:.5f}")
        if r['data']['rsi_divergence']:
            print(f"   🔴 RSI顶部背离检测: 已触发")
        if r.get('reasons'):
            print(f"   ✅ 做空支持: {' | '.join(r['reasons'][:4])}")
        if r.get('warnings'):
            print(f"   ⚠️  风险: {' | '.join(r['warnings'][:3])}")

        # 策略
        if r['final'] >= 30:
            atr = r['data'].get('atr1h', 0)
            entry = r['data']['cur']
            sl    = r['data']['pump_high'] * 1.015
            tp1   = fib['0.382']
            tp2   = fib['0.618']
            rr    = abs(entry - tp1) / abs(sl - entry) if abs(sl - entry) > 0 else 0
            print(f"   📕 策略: 等反弹至 {fib['0.236']:.5f}~{r['data']['pump_high']:.5f} 区间入场做空")
            print(f"      SL={sl:.5f}(高点+1.5%)  TP1={tp1:.5f}(0.382)  TP2={tp2:.5f}(0.618)  RR≈{rr:.1f}x")
        else:
            print(f"   → 当前不做空。等待：体制转为BEAR_TREND/BEAR_EARLY 或 RSI背离信号")

    print(f"\n{'─'*68}")
    print(f"注：最终分 = 原始分 × 流动性系数 × 体制乘数（D1-D6全部集成）")
    print(f"    最终分≥60 才建议执行做空 | 体制为BEAR_RECOVERY时理论最高=40\n")


# ══════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='Pump做空扫描器')
    ap.add_argument('--symbols', nargs='+', default=['RESOLVUSDT','BICOUSDT','ALICEUSDT','BELUSDT','BTWUSDT'])
    ap.add_argument('--min-chg', type=float, default=0, help='最小24h涨幅过滤')
    ap.add_argument('--dry', action='store_true', help='仅打印，不写文件')
    args = ap.parse_args()

    symbols = [s.upper() if not s.endswith('USDT') else s.upper() for s in args.symbols]
    # 自动补USDT
    symbols = [s if s.endswith('USDT') else s+'USDT' for s in symbols]

    now = datetime.now(timezone.utc)
    results = []

    for sym in symbols:
        print(f"  采集 {sym}...", end=' ', flush=True)
        d = collect(sym)
        if not d:
            print("❌ 失败")
            continue
        if args.min_chg and d['chg24'] < args.min_chg:
            print(f"跳过（涨幅{d['chg24']:.1f}%<{args.min_chg}%）")
            continue

        final, raw, mult, reasons, warnings, detail = score_short(sym, d)
        print(f"✓ final={final:.0f} regime={d['regime']}")

        results.append({
            'sym': sym, 'final': final, 'raw': raw, 'mult': mult,
            'reasons': reasons, 'warnings': warnings, 'detail': detail,
            'data': d,
        })

    if not results:
        print("无有效标的")
        return

    print_report(results)

    # 保存扫描结果
    if not args.dry:
        out_path = BASE / 'data' / 'pump_scan_result.json'
        save_data = []
        for r in results:
            save_data.append({
                'sym': r['sym'], 'final': round(r['final'], 1),
                'raw': round(r['raw'], 1), 'mult': r['mult'],
                'regime': r['detail']['regime'],
                'grade': grade(r['final']),
                'reasons': r['reasons'][:4],
                'warnings': r['warnings'][:3],
                'ts': now.isoformat(),
                'price': r['data']['cur'],
                'chg24': r['data']['chg24'],
            })
        out_path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
        print(f"  结果已保存: {out_path}")


if __name__ == '__main__':
    main()
