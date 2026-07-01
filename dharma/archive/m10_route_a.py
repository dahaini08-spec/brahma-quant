#!/usr/bin/env python3
"""
m10_route_a.py · 达摩院M10路线A
六标的 × SL倍数(5) × GapGate(5) = 25组参数网格
IS数据：截止2024-12-31  OOS：2025-01-01+
"""
import sys, os, warnings
warnings.filterwarnings('ignore')
import pandas as pd
import numpy as np

DATA     = '/root/.openclaw/workspace/trading-system/dharma/data'
IS_END   = '2025-01-01'
SYMS     = ['btcusdt','ethusdt','solusdt','bnbusdt','ltcusdt','dogeusdt']
RR       = 2.5
MAX_BARS = 24   # 6H超时

SL_MULTS  = [0.6, 0.8, 1.0, 1.2, 1.5]
GAP_GATES = [0.003, 0.005, 0.008, 0.010, 0.015]

# ─── 工具 ────────────────────────────────────────────────────
def load(sym, tf):
    p = f'{DATA}/{sym}_{tf}_2018_2026.parquet'
    if not os.path.exists(p): return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df

def calc_atr(df, n=14):
    h,l,c = df['high'],df['low'],df['close']
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()

def calc_rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1+g/(l+1e-9))

def ema_np(arr, n):
    out = np.empty(len(arr)); out[0] = arr[0]; k = 2/(n+1)
    for i in range(1,len(arr)): out[i] = arr[i]*k + out[i-1]*(1-k)
    return out

def regime_4h(c4, i):
    if i < 50: return 'CHOP'
    w = c4[i-50:i+1]
    e20 = ema_np(w,20)[-1]; e50 = ema_np(w,50)[-1]; p = w[-1]
    chg = (p - w[-20])/w[-20]
    if p < e20 < e50: return 'BEAR_TREND' if chg < -0.08 else 'BEAR_EARLY'
    if p > e20 > e50: return 'BULL_TREND'
    return 'CHOP'

def find_ob(o,h,l,c,direction,lb=40):
    n = min(len(c),lb)
    o_=o[-n:]; h_=h[-n:]; l_=l[-n:]; c_=c[-n:]
    if direction=='SHORT':
        for j in range(len(c_)-2,2,-1):
            if (c_[j-1]-o_[j-1])/max(o_[j-1],1e-9)>0.004 and c_[j]<o_[j]:
                return float(h_[j-1]), float(l_[j-1])
    else:
        for j in range(len(c_)-2,2,-1):
            if (o_[j-1]-c_[j-1])/max(o_[j-1],1e-9)>0.004 and c_[j]>o_[j]:
                return float(h_[j-1]), float(l_[j-1])
    return None,None

def choch_15m(h,l,c,direction,lb=30):
    n=min(len(c),lb); h_=h[-n:]; l_=l[-n:]; c_=c[-n:]
    if len(c_)<8: return False
    if direction=='SHORT':
        lh = sum(1 for x in range(len(h_)-3,len(h_)-1) if h_[x]<h_[x-2])
        return lh>=1 and c_[-1]<np.min(l_[-5:])*1.002
    else:
        hl = sum(1 for x in range(len(l_)-3,len(l_)-1) if l_[x]>l_[x-2])
        return hl>=1 and c_[-1]>np.max(h_[-5:])*0.998

# ─── 单次回测 ─────────────────────────────────────────────────
def run_one(sym, sl_mult, gap_min, split='IS'):
    df1 = load(sym,'1h');  df4 = load(sym,'4h');  df15 = load(sym,'15m')
    if df1 is None or df4 is None or df15 is None: return None

    atr1 = calc_atr(df1,14); rsi1 = calc_rsi(df1['close'],14)
    o1=df1['open'].values; h1=df1['high'].values
    l1=df1['low'].values;  c1=df1['close'].values; idx1=df1.index
    c4=df4['close'].values; h4=df4['high'].values; l4=df4['low'].values; idx4=df4.index
    h15=df15['high'].values; l15=df15['low'].values; c15=df15['close'].values; idx15=df15.index

    if split=='IS':
        mask = idx1 < IS_END
    else:
        mask = idx1 >= IS_END

    trades=[]; skip=None
    for i in np.where(mask)[0]:
        if i<50: continue
        ts=idx1[i]; price=c1[i]; atr=atr1.iloc[i]; rsi=rsi1.iloc[i]
        if pd.isna(atr) or atr<=0 or pd.isna(rsi): continue
        if skip and ts<skip: continue
        if   rsi>58: direction='SHORT'
        elif rsi<42: direction='LONG'
        else: continue

        # 4H体制
        j4=idx4.searchsorted(ts)-1
        if j4<50: continue
        reg=regime_4h(c4[j4-50:j4+1],50)
        if reg=='BULL_TREND' and direction=='SHORT': continue
        if reg=='BEAR_TREND' and direction=='LONG':  continue

        # 1H OB
        ob_hi,ob_lo=find_ob(o1[max(0,i-40):i+1],h1[max(0,i-40):i+1],
                             l1[max(0,i-40):i+1],c1[max(0,i-40):i+1],direction)
        if ob_hi is None: continue

        if direction=='SHORT':
            entry=min(ob_hi, price*1.015)
            gap=(entry-price)/price
        else:
            entry=max(ob_lo, price*0.985)
            gap=(price-entry)/price
        if gap<gap_min: continue

        # 15M CHoCH
        j15=idx15.searchsorted(ts)-1
        if j15<30: continue
        if not choch_15m(h15[j15-30:j15+1],l15[j15-30:j15+1],c15[j15-30:j15+1],direction): continue

        sl_dist=atr*sl_mult
        if direction=='SHORT':
            sl=entry+sl_dist; tp=entry-sl_dist*RR
        else:
            sl=entry-sl_dist; tp=entry+sl_dist*RR
        if tp<=0: continue

        res='TIMEOUT'
        for k in range(i+1,min(i+1+MAX_BARS,len(df1))):
            if direction=='SHORT':
                if h1[k]>=sl: res='SL'; break
                if l1[k]<=tp: res='TP'; break
            else:
                if l1[k]<=sl: res='SL'; break
                if h1[k]>=tp: res='TP'; break
        if res=='TIMEOUT': continue
        pnl=RR if res=='TP' else -1.0
        trades.append(pnl)
        skip=ts+pd.Timedelta(minutes=90)

    if not trades: return None
    t=np.array(trades)
    wins=(t>0).sum(); n=len(t)
    pf=t[t>0].sum()/max(abs(t[t<0].sum()),1e-9)
    return dict(n=n, wr=wins/n*100, pf=pf, total=t.sum())

# ─── 网格扫描 ─────────────────────────────────────────────────
print("="*72)
print("  M10 路线A — SL倍数 × GapGate 25组参数网格 (IS训练集)")
print("="*72)

IS_results  = {}
OOS_results = {}

for sym in SYMS:
    print(f"\n{'─'*30} {sym.upper()} {'─'*30}")
    _h='SL/Gap'
    print(f'  {_h:>8}', end='')
    for g in GAP_GATES: print(f"  gap={g*100:.1f}%", end='')
    print()

    best = {'wr':0,'pf':0,'params':None,'res':None}
    for sl in SL_MULTS:
        print(f"  sl={sl:.1f}x  ", end='', flush=True)
        for g in GAP_GATES:
            r = run_one(sym, sl, g, 'IS')
            if r:
                IS_results[(sym,sl,g)] = r
                tag = f"{r['wr']:.0f}%/{r['pf']:.1f}"
                print(f"  {tag:>10}", end='', flush=True)
                if r['pf']>best['pf'] and r['n']>=30:
                    best = {'wr':r['wr'],'pf':r['pf'],'params':(sl,g),'res':r}
            else:
                print(f"  {'─':>10}", end='')
        print()

    if best['params']:
        sl_b, g_b = best['params']
        print(f"  ★ IS最优: SL={sl_b:.1f}x  Gap={g_b*100:.1f}%  WR={best['wr']:.1f}%  PF={best['pf']:.2f}  n={best['res']['n']}")
        # OOS验证
        r_oos = run_one(sym, sl_b, g_b, 'OOS')
        if r_oos:
            OOS_results[sym] = dict(sl=sl_b, gap=g_b, **r_oos)
            print(f"  ✅ OOS验证: WR={r_oos['wr']:.1f}%  PF={r_oos['pf']:.2f}  n={r_oos['n']}")

# ─── 汇总 ────────────────────────────────────────────────────
print("\n" + "="*72)
print("  M10-A 最优参数汇总（IS训练→OOS验证）")
print("="*72)
print(f"  {'标的':<10} {'SL倍数':>7} {'Gap门槛':>8} {'IS WR':>7} {'IS PF':>7} {'OOS WR':>8} {'OOS PF':>8} {'OOS n':>6}")
print("  " + "─"*68)
for sym in SYMS:
    if sym not in OOS_results: continue
    r = OOS_results[sym]
    sl_b=r['sl']; g_b=r['gap']
    is_r = IS_results.get((sym,sl_b,g_b),{})
    print(f"  {sym.upper():<10} {sl_b:>7.1f}x {g_b*100:>7.1f}%  {is_r.get('wr',0):>6.1f}%  {is_r.get('pf',0):>6.2f}  {r['wr']:>7.1f}%  {r['pf']:>7.2f}  {r['n']:>5}")

print()
