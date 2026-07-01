#!/usr/bin/env python3
"""
梵天 v23.0 vs v24.0 联合模拟测试
BTC SHORT / ETH SHORT / BTC LONG 三策略全量对比
"""
import sys, json, warnings, math
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')

BASE = Path('/root/.openclaw/workspace/trading-system')
sys.path.insert(0, str(BASE))
from dharma.backtest_1000u import load_and_build, INIT_CAPITAL, LEVERAGE, COST_RATE, RISK_PER_TRADE

TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
BEAR = {'BEAR_TREND','BEAR_EARLY'}
BULL = {'BULL_TREND','BULL_EARLY'}


def run_sim(df, direction, sl, tp1, hold, rsi, regimes):
    if direction == 'SHORT':
        sig = df['regime'].isin(regimes) & (df['rsi'] > rsi) & (df['close'] < df['ema200'])
    else:
        sig = df['regime'].isin(regimes) & (df['rsi'] < rsi) & (df['close'] > df['ema200'])

    idx = np.where(sig.values)[0]
    if len(idx) == 0:
        return None

    closes=df['close'].values; highs=df['high'].values
    lows=df['low'].values;     atrs=df['atr'].values; n=len(df)
    cap=INIT_CAPITAL; peak=INIT_CAPITAL; mdd=0.0
    trades=[]; last=-1

    for i in idx:
        if i <= last: continue
        e=closes[i]; a=atrs[i]
        if a<=0 or cap<=0: continue
        if direction == 'SHORT':
            sl_p=e+a*sl; tp_p=e-a*tp1; rp=(sl_p-e)/e
        else:
            sl_p=e-a*sl; tp_p=e+a*tp1; rp=(e-sl_p)/e
        if rp<=0: continue
        notl = min((cap*RISK_PER_TRADE)/rp, cap*LEVERAGE*0.5)
        res='TO'; pp=-COST_RATE
        for j in range(i+1, min(i+hold+1, n)):
            h,l = highs[j], lows[j]
            if direction == 'SHORT':
                if h>=sl_p: res='SL'; pp=(e-sl_p)/e-COST_RATE; last=j; break
                if l<=tp_p: res='TP'; pp=(e-tp_p)/e-COST_RATE; last=j; break
            else:
                if l<=sl_p: res='SL'; pp=(sl_p-e)/e-COST_RATE; last=j; break
                if h>=tp_p: res='TP'; pp=(tp_p-e)/e-COST_RATE; last=j; break
        else:
            last = min(i+hold, n-1)

        pnl_u = pp * notl
        cap=max(0, cap+pnl_u); peak=max(peak,cap)
        dd=(peak-cap)/peak if peak>0 else 0; mdd=max(mdd,dd)
        trades.append({'res':res,'pp':pp,'pnl_u':pnl_u,'yr':df.index[i].year,'cap':cap})

    if not trades:
        return None

    wins=[t for t in trades if t['res']=='TP']
    losses=[t for t in trades if t['res']=='SL']
    tos=[t for t in trades if t['res']=='TO']
    wr  = len(wins)/(len(wins)+len(losses)) if (wins or losses) else 0
    pf  = sum(t['pp'] for t in wins)/abs(sum(t['pp'] for t in losses)) \
          if losses and sum(t['pp'] for t in losses)<0 else 0
    yrs = (df.index[-1]-df.index[0]).days/365.25
    cagr= (cap/INIT_CAPITAL)**(1/max(yrs,.1))-1 if cap>0 else -1

    yearly={}
    for t in trades:
        y=str(t['yr']); yearly.setdefault(y,{'pnl':0.0,'n':0,'w':0,'sl':0,'to':0})
        yearly[y]['n']+=1; yearly[y]['pnl']+=t['pnl_u']
        if t['res']=='TP':  yearly[y]['w']+=1
        elif t['res']=='SL': yearly[y]['sl']+=1
        else: yearly[y]['to']+=1

    for v in yearly.values():
        v['pnl']=round(v['pnl'],2)

    return dict(n=len(trades), wins=len(wins), losses=len(losses), tos=len(tos),
                wr=round(wr,4), pf=round(pf,3), cagr=round(cagr*100,2),
                mdd=round(mdd*100,2), final=round(cap,0), yearly=yearly)


# ── 策略配置 ──────────────────────────────────────────────────────
STRATEGIES = [
    dict(name='BTC SHORT', sym='BTCUSDT', d='SHORT', regimes=BEAR, rsi=55,
         v23=dict(sl=0.6, tp1=2.0, hold=72),
         v24=dict(sl=0.6, tp1=2.5, hold=72)),
    dict(name='ETH SHORT', sym='ETHUSDT', d='SHORT', regimes=BEAR, rsi=60,
         v23=dict(sl=0.6, tp1=2.0, hold=72),
         v24=dict(sl=0.6, tp1=2.85, hold=72)),
    dict(name='BTC LONG',  sym='BTCUSDT', d='LONG',  regimes=BULL, rsi=35,
         v23=dict(sl=2.0, tp1=4.0, hold=16),
         v24=dict(sl=1.8, tp1=2.5, hold=110)),
]

print('='*60)
print('  梵天 v23.0 vs v24.0 联合模拟测试')
print(f'  数据截止: 2026-06-12  |  初始资金: $1,000  |  杠杆: 5x')
print('='*60)

data_cache={}
all_out=[]

for cfg in STRATEGIES:
    sym=cfg['sym']
    if sym not in data_cache:
        print(f'\n  加载 {sym} ...')
        data_cache[sym] = load_and_build(sym)
    df = data_cache[sym]

    r23 = run_sim(df, cfg['d'], **cfg['v23'], regimes=cfg['regimes'], rsi=cfg['rsi'])
    r24 = run_sim(df, cfg['d'], **cfg['v24'], regimes=cfg['regimes'], rsi=cfg['rsi'])
    if not r23 or not r24:
        print(f'  {cfg["name"]}: 数据不足'); continue

    dc = r24['cagr']-r23['cagr']; dm=r24['mdd']-r23['mdd']
    dw = (r24['wr']-r23['wr'])*100; dto=r24['tos']-r23['tos']
    dpf= r24['pf']-r23['pf']

    print(f'\n{"─"*60}')
    print(f'  {cfg["name"]}')
    print(f'  v23: SL={cfg["v23"]["sl"]}x  TP1={cfg["v23"]["tp1"]}x  hold={cfg["v23"]["hold"]}H')
    print(f'  v24: SL={cfg["v24"]["sl"]}x  TP1={cfg["v24"]["tp1"]}x  hold={cfg["v24"]["hold"]}H')
    print(f'{"─"*60}')
    print(f'  {"指标":<10} {"v23.0":>10} {"v24.0":>10} {"变化":>10}')
    print(f'  {"-"*42}')
    print(f'  {"CAGR%":<10} {r23["cagr"]:>+9.1f}% {r24["cagr"]:>+9.1f}% {dc:>+9.1f}%')
    print(f'  {"MDD%":<10} {r23["mdd"]:>9.1f}% {r24["mdd"]:>9.1f}% {dm:>+9.1f}%')
    print(f'  {"WR%":<10} {r23["wr"]*100:>9.1f}% {r24["wr"]*100:>9.1f}% {dw:>+9.1f}%')
    print(f'  {"PF":<10} {r23["pf"]:>10.3f} {r24["pf"]:>10.3f} {dpf:>+10.3f}')
    print(f'  {"总交易":<10} {r23["n"]:>10} {r24["n"]:>10} {r24["n"]-r23["n"]:>+10}')
    print(f'  {"TIMEOUT":<10} {r23["tos"]:>10} {r24["tos"]:>10} {dto:>+10}')
    print(f'  {"最终资金":<10} ${r23["final"]:>8,.0f} ${r24["final"]:>8,.0f} ${r24["final"]-r23["final"]:>+8,.0f}')

    print(f'\n  逐年对比  (v23→v24 PnL变化 / W-L-TO):')
    for yr in sorted(set(list(r23['yearly'])+list(r24['yearly']))):
        y3=r23['yearly'].get(yr,{'pnl':0,'w':0,'sl':0,'to':0,'n':0})
        y4=r24['yearly'].get(yr,{'pnl':0,'w':0,'sl':0,'to':0,'n':0})
        diff=y4['pnl']-y3['pnl']
        flag='▲' if diff>30 else ('▼' if diff<-30 else '─')
        print(f'  {yr}: v23={y3["pnl"]:>+7.0f}U '
              f'({y3["w"]}W/{y3["sl"]}SL/{y3["to"]}TO) → '
              f'v24={y4["pnl"]:>+7.0f}U '
              f'({y4["w"]}W/{y4["sl"]}SL/{y4["to"]}TO)  '
              f'{flag}{abs(diff):>6.0f}U')

    all_out.append({'name':cfg['name'],'v23':r23,'v24':r24,
                    'delta':{'cagr':round(dc,2),'mdd':round(dm,2),
                             'wr':round(dw,2),'pf':round(dpf,3),
                             'final':round(r24['final']-r23['final'],0)}})

# ── 汇总 ─────────────────────────────────────────────────────────
print(f'\n{"="*60}')
print('  联合模拟测试汇总')
print(f'{"="*60}')
print(f'  {"策略":<12} {"v23 CAGR":>9} {"v24 CAGR":>9} {"提升":>7}'
      f' {"v23 MDD":>8} {"v24 MDD":>8} {"MDD变化":>8}')
print(f'  {"-"*64}')
for o in all_out:
    d=o['delta']
    mdd_flag='↑风险' if d['mdd']>2 else ('↓优' if d['mdd']<-2 else '持平')
    print(f'  {o["name"]:<12} {o["v23"]["cagr"]:>+8.1f}% {o["v24"]["cagr"]:>+8.1f}%'
          f' {d["cagr"]:>+6.1f}%'
          f' {o["v23"]["mdd"]:>7.1f}% {o["v24"]["mdd"]:>7.1f}%'
          f'  {mdd_flag}')
print(f'{"="*60}')

# 保存结果
out_path = BASE/'dharma/results'/f'sim_v23_v24_{TAG}.json'
out_path.write_text(json.dumps({'_meta':{'ts':TAG,'version':'sim_v23_v24'},
                                 'results':all_out},
                                ensure_ascii=False, indent=2, default=str))
print(f'\n  结果 → {out_path.name}')
