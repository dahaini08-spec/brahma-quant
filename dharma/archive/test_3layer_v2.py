#!/usr/bin/env python3
"""
test_3layer_v2.py · 达摩院三层方案回测 v2（修正版）
设计院 2026-06-01

修正问题：
  v1 1H基线用RSI>55直接入场，每天9笔，严重失真
  v2 1H基线也要求OB结构确认，才能与三层方案公平对比

三方案对比：
  A. 1H+OB基线（公平基线）
  B. 三层：4H体制 + 1H OB + 15M CHoCH
  C. 三层+GapGate（gap<0.5%拒绝）

评估维度：WR / PF / 日均笔数 / 最大连亏 / 总R
"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

DATA_DIR  = os.path.join(os.path.dirname(__file__), 'data')
IS_CUTOFF = '2025-01-01'
SYMBOLS   = ['btcusdt', 'ethusdt', 'solusdt', 'dogeusdt', 'bnbusdt', 'ltcusdt']

RR_TARGET    = 2.5
SL_MULT_1H   = 1.5
SL_MULT_15M  = 0.8
MAX_BARS_1H  = 48
MAX_BARS_15M = 24    # 6H等价
GAP_MIN      = 0.005

# ─── 工具 ────────────────────────────────────────────────────
def load(sym, tf):
    path = os.path.join(DATA_DIR, f'{sym}_{tf}_2018_2026.parquet')
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df

def calc_atr(df, n=14):
    h,l,c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def calc_rsi(series, n=14):
    d = series.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1 + g/(l+1e-9))

def detect_regime_4h(arr_c, arr_h, arr_l):
    """最后一个4H时点的体制"""
    if len(arr_c) < 50: return 'CHOP_MID'
    c = arr_c
    def ema(x, n):
        out = np.zeros(len(x))
        k = 2/(n+1)
        out[0] = x[0]
        for i in range(1, len(x)):
            out[i] = x[i]*k + out[i-1]*(1-k)
        return out
    e20 = ema(c, 20)[-1]
    e50 = ema(c, 50)[-1]
    price = c[-1]
    chg = (price - c[-20]) / c[-20]
    if price < e20 < e50:
        return 'BEAR_TREND' if chg < -0.08 else 'BEAR_EARLY'
    if price > e20 > e50:
        return 'BULL_TREND'
    return 'CHOP_MID'

def find_ob(opens, highs, lows, closes, direction, lookback=40):
    """
    1H OB识别：找最近明确的机构订单块
    做空OB: 大阳线(>0.4%)后接阴线 → OB=阳线区间
    做多OB: 大阴线(>0.4%)后接阳线 → OB=阴线区间
    """
    n = min(len(closes), lookback)
    o = opens[-n:]; h = highs[-n:]; l = lows[-n:]; c = closes[-n:]
    if direction == 'SHORT':
        for j in range(len(c)-2, 2, -1):
            body = (c[j-1]-o[j-1])/max(o[j-1],1e-9)
            if body > 0.004 and c[j] < o[j]:    # 阳线>0.4%→阴线
                return float(h[j-1]), float(l[j-1])
    else:
        for j in range(len(c)-2, 2, -1):
            body = (o[j-1]-c[j-1])/max(o[j-1],1e-9)
            if body > 0.004 and c[j] > o[j]:    # 阴线>0.4%→阳线
                return float(h[j-1]), float(l[j-1])
    return None, None

def find_choch_15m(highs, lows, closes, direction, lookback=30):
    """15M CHoCH：结构转变确认"""
    n = min(len(closes), lookback)
    h = highs[-n:]; l = lows[-n:]; c = closes[-n:]
    if len(c) < 8: return False
    if direction == 'SHORT':
        lh = sum(1 for x in range(len(h)-3, len(h)-1) if h[x] < h[x-2])
        return lh >= 1 and c[-1] < np.min(l[-5:]) * 1.002
    else:
        hl = sum(1 for x in range(len(l)-3, len(l)-1) if l[x] > l[x-2])
        return hl >= 1 and c[-1] > np.max(h[-5:]) * 0.998

def max_drawdown_r(pnl_list):
    """最大连续亏损笔数"""
    max_dd = cur = 0
    for p in pnl_list:
        if p < 0: cur += 1; max_dd = max(max_dd, cur)
        else: cur = 0
    return max_dd

# ─── 回测核心 ─────────────────────────────────────────────────
def run(sym, scheme):
    """
    scheme: 'A'=1H+OB基线  'B'=三层  'C'=三层+GapGate
    """
    df1h = load(sym, '1h')
    if df1h is None: return pd.DataFrame()

    has_15m = has_4h = False
    df4h = df15m = None

    if scheme in ('B','C'):
        df4h  = load(sym, '4h')
        df15m = load(sym, '15m')
        has_4h  = df4h  is not None
        has_15m = df15m is not None
        if not has_4h or not has_15m:
            return pd.DataFrame()

    atr1h  = calc_atr(df1h, 14)
    rsi1h  = calc_rsi(df1h['close'], 14)

    # 预转 numpy 加速
    opens1h  = df1h['open'].values
    highs1h  = df1h['high'].values
    lows1h   = df1h['low'].values
    closes1h = df1h['close'].values
    idx1h    = df1h.index

    if has_4h:
        highs4h  = df4h['high'].values
        lows4h   = df4h['low'].values
        closes4h = df4h['close'].values
        idx4h    = df4h.index
    if has_15m:
        highs15m  = df15m['high'].values
        lows15m   = df15m['low'].values
        closes15m = df15m['close'].values
        idx15m    = df15m.index

    trades = []
    skip_until = None

    for i in range(50, len(df1h)):
        ts = idx1h[i]
        if ts < pd.Timestamp(IS_CUTOFF, tz='UTC'): continue
        if skip_until and ts < skip_until: continue

        price = closes1h[i]
        atr   = atr1h.iloc[i]
        rsi   = rsi1h.iloc[i]
        if pd.isna(atr) or atr <= 0 or pd.isna(rsi): continue

        # 方向：RSI > 58 → 做空；< 42 → 做多
        if   rsi > 58: direction = 'SHORT'
        elif rsi < 42: direction = 'LONG'
        else: continue

        # ── 所有方案都要求1H OB确认 ──────────────────────────
        ob_hi, ob_lo = find_ob(
            opens1h[max(0,i-40):i+1],
            highs1h[max(0,i-40):i+1],
            lows1h[max(0,i-40):i+1],
            closes1h[max(0,i-40):i+1],
            direction
        )
        if ob_hi is None: continue

        if direction == 'SHORT':
            entry = min(ob_hi, price * 1.015)   # 入场在OB上沿或价格+1.5%
            sl    = entry + atr * SL_MULT_1H
            tp1   = entry - atr * SL_MULT_1H * RR_TARGET
            gap   = (entry - price) / price
        else:
            entry = max(ob_lo, price * 0.985)
            sl    = entry - atr * SL_MULT_1H
            tp1   = entry + atr * SL_MULT_1H * RR_TARGET
            gap   = (price - entry) / price

        if tp1 <= 0: continue

        # ── B/C 额外过滤 ──────────────────────────────────────
        if scheme in ('B','C'):
            # 4H体制
            j4 = idx4h.searchsorted(ts) - 1
            if j4 < 50: continue
            regime = detect_regime_4h(closes4h[j4-50:j4+1],
                                       highs4h[j4-50:j4+1],
                                       lows4h[j4-50:j4+1])
            if regime == 'BULL_TREND' and direction == 'SHORT': continue
            if regime == 'BEAR_TREND' and direction == 'LONG':  continue

            # 15M CHoCH
            j15 = idx15m.searchsorted(ts) - 1
            if j15 < 30: continue
            if not find_choch_15m(highs15m[j15-30:j15+1],
                                   lows15m[j15-30:j15+1],
                                   closes15m[j15-30:j15+1],
                                   direction): continue
            sl   = entry + atr * SL_MULT_15M if direction=='SHORT' else entry - atr * SL_MULT_15M
            tp1  = entry - atr * SL_MULT_15M * RR_TARGET if direction=='SHORT' else entry + atr * SL_MULT_15M * RR_TARGET

        # ── C: GapGate ────────────────────────────────────────
        if scheme == 'C' and gap < GAP_MIN: continue

        if tp1 <= 0: continue

        # ── 逐K结算 ──────────────────────────────────────────
        max_bars = MAX_BARS_15M if scheme in ('B','C') else MAX_BARS_1H
        result = 'TIMEOUT'
        for k in range(i+1, min(i+1+max_bars, len(df1h))):
            hi = highs1h[k]; lo = lows1h[k]
            if direction == 'SHORT':
                if hi >= sl: result = 'SL'; break
                if lo <= tp1: result = 'TP'; break
            else:
                if lo <= sl: result = 'SL'; break
                if hi >= tp1: result = 'TP'; break

        if result == 'TIMEOUT': continue

        pnl = RR_TARGET if result=='TP' else -1.0
        trades.append({'ts':ts,'dir':direction,'result':result,'pnl':pnl})
        skip_until = ts + pd.Timedelta(minutes=90)

    return pd.DataFrame(trades)

# ─── 执行 ────────────────────────────────────────────────────
print("=" * 66)
print("  达摩院三层方案回测 v2  OOS=2025-01-01~2026-05-28")
print("=" * 66)
print(f"{'方案':<8}{'标的':<10}{'笔数':>6}{'WR':>8}{'PF':>8}{'日均':>7}{'连亏':>7}{'总R':>8}")
print("─" * 66)

all_res = {}
DAYS_OOS = 514  # 17个月

for sym in SYMBOLS:
    for sid, slabel in [('A','1H+OB基线'), ('B','三层无Gate'), ('C','三层+GapGate')]:
        df = run(sym, sid)
        if df is None or len(df) == 0:
            print(f"  {slabel:<8}{sym.upper():<10} {'─':>6}")
            continue
        n    = len(df)
        wins = (df['result']=='TP').sum()
        wr   = wins/n*100
        loss_r = abs(df[df['pnl']<0]['pnl'].sum())
        pf   = df[df['pnl']>0]['pnl'].sum() / max(loss_r, 1e-9)
        dpd  = n / DAYS_OOS
        mdd  = max_drawdown_r(df['pnl'].tolist())
        tot  = df['pnl'].sum()
        tag  = '⬅' if sid=='A' else ('🆕' if sid=='C' else '  ')
        print(f"  {slabel:<8}{sym.upper():<10}{n:>6}{wr:>7.1f}%{pf:>8.2f}{dpd:>7.2f}{mdd:>7}  {tot:>+6.1f}R  {tag}")
        all_res[f'{sym}_{sid}'] = dict(n=n, wr=wr, pf=pf, dpd=dpd, mdd=mdd, tot=tot)
    print()

# ─── 设计院裁决 ──────────────────────────────────────────────
print("=" * 66)
print("  设计院裁决")
print("=" * 66)
btc_a  = all_res.get('btcusdt_A',  {})
btc_c  = all_res.get('btcusdt_C',  {})
eth_a  = all_res.get('ethusdt_A',  {})
eth_c  = all_res.get('ethusdt_C',  {})

for sym, k in [('BTC','btcusdt'), ('ETH','ethusdt')]:
    a = all_res.get(f'{k}_A',{})
    c = all_res.get(f'{k}_C',{})
    if not a or not c: continue
    wr_delta  = c.get('wr',0)  - a.get('wr',0)
    pf_delta  = c.get('pf',0)  - a.get('pf',0)
    dpd_delta = c.get('dpd',0) - a.get('dpd',0)
    print(f"\n  {sym}: 三层+GapGate vs 1H基线")
    print(f"    WR  {a.get('wr',0):.1f}% → {c.get('wr',0):.1f}%  ({wr_delta:+.1f}%)")
    print(f"    PF  {a.get('pf',0):.2f} → {c.get('pf',0):.2f}  ({pf_delta:+.2f})")
    print(f"    日均 {a.get('dpd',0):.2f}笔 → {c.get('dpd',0):.2f}笔")
    print(f"    连亏 {a.get('mdd',0)} → {c.get('mdd',0)} 笔")

print()
