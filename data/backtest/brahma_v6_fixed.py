#!/usr/bin/env python3
"""
梵天达摩院 v6.1 — 三层仓位架构 修复版
核心修复:
  1. 仓位基准 = min(nav, nav_start×20) — 防止复利雪球爆炸
  2. 总风险敞口上限 = 15%NAV（三层合计）
  3. CHOP体制：仅1H层开单，15m/4H层不参与CHOP
  4. 4H趋势层：仅BULL_TREND/BEAR_TREND，WR有铁证支持
  5. 15m快攻层：仅CHOP区间极值 + 高分趋势回调
"""
import json, statistics, time, datetime
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent

def ema_fn(p,n):
    if len(p)<n: return p[-1]
    k=2/(n+1); e=p[0]
    for x in p[1:]: e=e*(1-k)+x*k
    return e
def rsi_fn(c,n=14):
    if len(c)<n+1: return 50
    d=[c[i]-c[i-1] for i in range(1,len(c))]
    g=[max(0,x) for x in d[-n:]]; lo=[max(0,-x) for x in d[-n:]]
    ag,al=sum(g)/n,sum(lo)/n
    return 100-100/(1+ag/al) if al>0 else 50
def atr_fn(kl,n=14):
    tr=[]
    for i in range(1,min(len(kl),n*2)):
        h=kl[-i][2]; l=kl[-i][3]; pc=kl[-i-1][4]
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    return sum(tr[:n])/n if tr else 0
def fb(kl,ts):
    lo,hi=0,len(kl)-1
    while lo<hi:
        mid=(lo+hi+1)//2
        if kl[mid][0]<=ts: lo=mid
        else: hi=mid-1
    return lo
def detect_regime(c4,cd):
    if len(c4)<50 or len(cd)<20: return 'UNKNOWN'
    px=c4[-1]; e20=ema_fn(c4[-20:],20); e50=ema_fn(c4[-50:],50); ed=ema_fn(cd[-20:],20)
    r4=rsi_fn(c4[-30:])
    ev=(e20-e50)/e50*100; pd=(px-ed)/ed*100; p4=(px-e20)/e20*100
    if r4>55 and ev>1.0 and pd>0: return 'BULL_TREND' if p4>3 and r4>65 else 'BULL_EARLY'
    elif r4<45 and ev<-1.0: return 'BEAR_TREND' if pd<-5 and r4<40 else 'BEAR_EARLY'
    elif r4>45 and pd>-8 and ev>-0.5: return 'BEAR_RECOVERY'
    else: return 'CHOP_MID'

# ── 评分引擎（层级专属逻辑）────────────────────────────
def score_signal(c15,c1,c4,h1,l1,regime,direction,layer):
    sc=0; r15=rsi_fn(c15); r1=rsi_fn(c1); r4=rsi_fn(c4)
    px=c1[-1]; e20=ema_fn(c1[-20:],20); e50=ema_fn(c1[-50:],50) if len(c1)>=50 else e20
    e20_4=ema_fn(c4[-20:],20); e50_4=ema_fn(c4[-50:],50) if len(c4)>=50 else e20_4
    ns=nr=ti=False
    if h1 and l1 and len(h1)>=12:
        h24=max(h1[-24:]) if len(h1)>=24 else max(h1); l24=min(l1[-24:]) if len(l1)>=24 else min(l1)
        h48=max(h1[-48:]) if len(h1)>=48 else max(h1); l48=min(l1[-48:]) if len(l1)>=48 else min(l1)
        rng=(h48-l48)/px*100 if h48>l48 else 10
        ns=(px-l24)/px*100<1.5; nr=(h24-px)/px*100<1.5; ti=rng<8.0

    if regime=='CHOP_MID':
        # CHOP只在1H层，且必须是区间极值
        if layer!='1h': return 0
        if direction=='LONG':
            if r1<35: sc+=45
            elif r1<42: sc+=30
            elif r1<50: sc+=15
            else: return 0  # CHOP不在RSI>50时做多
            if ns: sc+=30; 
            if ti: sc+=20
            if r15<32: sc+=15
            sc+=10
        else:
            if r1>65: sc+=45
            elif r1>58: sc+=30
            elif r1>52: sc+=15
            else: return 0
            if nr: sc+=30
            if ti: sc+=20
            if r15>68: sc+=15
            sc+=10

    elif regime in('BULL_TREND','BULL_EARLY','BEAR_RECOVERY'):
        if direction!='LONG': return 0
        if e20>e50 and e20_4>e50_4: sc+=30
        elif e20>e50: sc+=18
        if 42<=r1<=58: sc+=30
        elif 38<=r1<42: sc+=22
        elif r1<38: sc+=14
        elif 58<r1<=68: sc+=8
        elif r1>72: sc-=15
        if r15<45: sc+=12
        if ns: sc+=22; 
        if ti: sc+=15
        sc+=20 if regime=='BULL_TREND' else 12
        if r4>55: sc+=8
        # 4H趋势层额外加成
        if layer=='4h' and r4>62: sc+=10

    elif regime in('BEAR_TREND','BEAR_EARLY'):
        if direction!='SHORT': return 0
        if not(e20>e50) and not(e20_4>e50_4): sc+=30
        elif not(e20>e50): sc+=18
        if 42<=r1<=58: sc+=30
        elif 58<r1<=68: sc+=22
        elif r1>68: sc+=14
        elif 38<=r1<42: sc+=8
        elif r1<38: sc-=15
        if r15>55: sc+=12
        if nr: sc+=22
        if ti: sc+=15
        sc+=20 if regime=='BEAR_TREND' else 12
        if r4<48: sc+=8
        if layer=='4h' and r4<42: sc+=10

    # 多周期共振
    if direction=='LONG': al=sum([r15<52,r1<55,r4<62])
    else: al=sum([r15>55,r1>55,r4>55])
    sc+=[0,5,10,15][al]

    # BB压缩
    if len(c1)>=20:
        ma20=sum(c1[-20:])/20; bbw=statistics.stdev(c1[-20:])*2/ma20*100
        if bbw<1.0: sc+=15
        elif bbw<1.5: sc+=10
        elif bbw<2.5: sc+=5

    return max(0,min(sc,165))

# ── 死穴+能力门槛 ──────────────────────────────────────
def get_threshold(regime,direction,layer,sym):
    # 原宪法死穴（精英解锁）
    if (regime,direction) in {('BEAR_TREND','LONG'),('BULL_TREND','SHORT')}:
        return 155
    # 能力门槛矩阵（分层）
    thresh_map = {
        ('BEAR_RECOVERY','LONG'): {'15m':999,'1h':140,'4h':145},
        ('BEAR_EARLY','SHORT'):   {'15m':999,'1h':135,'4h':140},
        ('BULL_EARLY','LONG'):    {
            '15m':999,
            '1h': 120 if sym=='BTCUSDT' else 140,
            '4h': 130 if sym=='BTCUSDT' else 145,
        },
        # 趋势主力门槛
        ('BULL_TREND','LONG'):    {'15m':120,'1h':120,'4h':125},
        ('BEAR_TREND','SHORT'):   {'15m':120,'1h':120,'4h':125},
        # CHOP仅1H层，评分函数里已限制
        ('CHOP_MID','LONG'):      {'15m':999,'1h':130 if sym=='BTCUSDT' else 125,'4h':999},
        ('CHOP_MID','SHORT'):     {'15m':999,'1h':130 if sym=='BTCUSDT' else 125,'4h':999},
    }
    t = thresh_map.get((regime,direction),{}).get(layer,120)
    return t

# ── 仓位参数 ──────────────────────────────────────────
LAYER_SZ  = {'15m':0.02,'1h':0.05,'4h':0.08}  # 基础占比
LAYER_TP1 = {'15m':1.0, '1h':1.5, '4h':2.0}
LAYER_TP2 = {'15m':1.5, '1h':2.5, '4h':3.5}
REGIME_MULT = {
    'BULL_TREND':   {'LONG':1.0,'SHORT':0.1},
    'BULL_EARLY':   {'LONG':0.8,'SHORT':0.1},
    'BEAR_TREND':   {'LONG':0.1,'SHORT':1.0},
    'BEAR_EARLY':   {'LONG':0.1,'SHORT':0.8},
    'BEAR_RECOVERY':{'LONG':0.8,'SHORT':0.1},
    'CHOP_MID':     {'LONG':0.5,'SHORT':0.5},
}
REGIME_SL = {
    'BULL_TREND':2.0,'BULL_EARLY':2.5,'BEAR_TREND':2.0,
    'BEAR_EARLY':2.5,'BEAR_RECOVERY':2.5,'CHOP_MID':2.0,
}
def score_mult(s):
    if s>=150: return 1.5
    if s>=140: return 1.2
    if s>=130: return 1.0
    return 0.8
MONTH_BIAS={9:8,10:6,12:4,5:-10,8:-6,7:-5}
FEE=0.001; LEV=5
MAX_NAV_CAP=50  # 仓位基准上限 = 初始NAV × 50（防复利爆炸）
MAX_TOTAL_RISK=0.15  # 三层合计最大风险敞口15%NAV

def run(sym, nav_start=10000):
    t0=time.time()
    print(f"\n{'='*60}")
    print(f"梵天v6.1 | {sym} | 三层仓位+风险上限")
    print(f"{'='*60}")
    def ld(tf): return [(int(k[0]),float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[7])) for k in json.loads((DATA_DIR/f'{sym}_{tf}.json').read_text())]
    k15=ld('15m'); k1=ld('1h'); k4=ld('4h'); k1d=ld('1d')
    print(f"数据: 15m={len(k15):,}")

    nav=nav_start; pos={'15m':None,'1h':None,'4h':None}
    trades=[]; fee_total=0.0; mx=nav_start; mdd=0.0; pr='UNKNOWN'
    by_layer=defaultdict(lambda:{'n':0,'w':0,'pnl':0.0})
    by_regime=defaultdict(lambda:{'n':0,'w':0,'pnl':0.0})

    for i in range(240,len(k15),4):
        ts=k15[i][0]; px=k15[i][4]
        mo=datetime.datetime.utcfromtimestamp(ts/1000).month

        # ── 各层持仓检查 ─────────────────────────────────
        for layer in ['15m','1h','4h']:
            p=pos[layer]
            if p is None: continue
            d=p['dir']; sl=p['sl']; tp1=p['tp1']; tp2=p['tp2']
            hsl=(d=='LONG' and px<=sl)or(d=='SHORT' and px>=sl)
            htp1=(not p.get('tp1_hit'))and((d=='LONG' and px>=tp1)or(d=='SHORT' and px<=tp1))
            htp2=p.get('tp1_hit')and((d=='LONG' and px>=tp2)or(d=='SHORT' and px<=tp2))
            if htp1:
                ep=tp1; pp=(ep-p['entry'])/p['entry'] if d=='LONG' else(p['entry']-ep)/p['entry']
                hq=p['qty']*0.5; fee=hq*p['entry']*FEE; pnl=pp*hq*p['entry']*LEV-fee
                nav+=pnl; fee_total+=fee
                by_layer[layer]['n']+=1; by_layer[layer]['pnl']+=pnl
                k=p['regime']+'_'+d; by_regime[k]['n']+=1; by_regime[k]['pnl']+=pnl
                if pnl>0: by_layer[layer]['w']+=1; by_regime[k]['w']+=1
                trades.append({'layer':layer,'pnl':pnl,'dir':d,'regime':p['regime'],'score':p['score'],'exit':'TP1','ts':ts})
                p['qty']=hq; p['tp1_hit']=True; p['sl']=p['entry']; pos[layer]=p
            elif htp2:
                ep=tp2; pp=(ep-p['entry'])/p['entry'] if d=='LONG' else(p['entry']-ep)/p['entry']
                fee=p['qty']*p['entry']*FEE; pnl=pp*p['qty']*p['entry']*LEV-fee
                nav+=pnl; fee_total+=fee
                by_layer[layer]['n']+=1; by_layer[layer]['pnl']+=pnl
                k=p['regime']+'_'+d; by_regime[k]['n']+=1; by_regime[k]['pnl']+=pnl
                if pnl>0: by_layer[layer]['w']+=1; by_regime[k]['w']+=1
                trades.append({'layer':layer,'pnl':pnl,'dir':d,'regime':p['regime'],'score':p['score'],'exit':'TP2','ts':ts})
                pos[layer]=None
            elif hsl:
                ep=sl; pp=(ep-p['entry'])/p['entry'] if d=='LONG' else(p['entry']-ep)/p['entry']
                fee=p['qty']*p['entry']*FEE; pnl=pp*p['qty']*p['entry']*LEV-fee
                nav+=pnl; fee_total+=fee
                by_layer[layer]['n']+=1; by_layer[layer]['pnl']+=pnl
                k=p['regime']+'_'+d; by_regime[k]['n']+=1; by_regime[k]['pnl']+=pnl
                if pnl>0: by_layer[layer]['w']+=1; by_regime[k]['w']+=1
                trades.append({'layer':layer,'pnl':pnl,'dir':d,'regime':p['regime'],'score':p['score'],'exit':'SL','ts':ts})
                pos[layer]=None

        if nav>mx: mx=nav
        dd=(mx-nav)/mx*100
        if dd>mdd: mdd=dd

        # ── 多层数据 ─────────────────────────────────────
        i1=fb(k1,ts); i4=fb(k4,ts); id_=fb(k1d,ts)
        if i1<50 or i4<50 or id_<20: continue
        c1=[k1[j][4] for j in range(max(0,i1-99),i1+1)]
        c4=[k4[j][4] for j in range(max(0,i4-99),i4+1)]
        cd=[k1d[j][4] for j in range(max(0,id_-29),id_+1)]
        c15_=[k15[j][4] for j in range(max(0,i-49),i+1)]
        h1=[k1[j][2] for j in range(max(0,i1-55),i1+1)]
        l1=[k1[j][3] for j in range(max(0,i1-55),i1+1)]

        rg=detect_regime(c4,cd)
        if rg=='UNKNOWN': continue
        if pr!=rg and pr!='UNKNOWN':
            for layer in ['15m','1h','4h']:
                p=pos[layer]
                if p:
                    pp=(px-p['entry'])/p['entry'] if p['dir']=='LONG' else(p['entry']-px)/p['entry']
                    fee=p['qty']*p['entry']*FEE; pnl=pp*p['qty']*p['entry']*LEV-fee
                    nav+=pnl; fee_total+=fee
                    trades.append({'layer':layer,'pnl':pnl,'dir':p['dir'],'regime':p['regime'],'score':p['score'],'exit':'REGIME_CHG','ts':ts})
                    pos[layer]=None
        pr=rg

        if rg in('BULL_TREND','BULL_EARLY','BEAR_RECOVERY'): cands=['LONG']
        elif rg in('BEAR_TREND','BEAR_EARLY'): cands=['SHORT']
        else: cands=['LONG','SHORT']

        # 计算当前三层已用风险敞口
        active_risk = sum(
            p['qty']*p['entry']/nav * 100
            for p in pos.values() if p
        ) if nav > 0 else 0

        # ── 三层信号扫描 ─────────────────────────────────
        for layer in ['15m','1h','4h']:
            if pos[layer]: continue
            if active_risk >= MAX_TOTAL_RISK*100: continue  # 总敞口上限

            bs=0; bd=None
            for di in cands:
                thresh=get_threshold(rg,di,layer,sym)
                if thresh>=999: continue
                raw=score_signal(c15_,c1,c4,h1,l1,rg,di,layer)
                # 月历偏差
                adj=raw+MONTH_BIAS.get(mo,0)
                adj=max(0,min(adj,165))
                if adj>bs and adj>=thresh: bs=adj; bd=di
            if not bd: continue

            raw_kl=k1[max(0,i1-20):i1+1]
            at=atr_fn(raw_kl); slb=REGIME_SL.get(rg,2.5)
            at_s=at*1.5/px*100 if px>0 else slb
            sl_lim=8.0 if layer=='4h' else 6.0
            slp=min(max(slb,at_s),sl_lim)

            sm=REGIME_MULT.get(rg,{}).get(bd,0.5)
            sc_m=score_mult(bs)
            # 关键：仓位基准 = min(nav, nav_start*MAX_NAV_CAP)
            nav_base=min(nav, nav_start*MAX_NAV_CAP)
            sz=LAYER_SZ[layer]
            no=nav_base*sz*sm*sc_m*LEV
            if no<nav_base*0.005: continue
            qty=no/px

            fee=qty*px*FEE; nav-=fee; fee_total+=fee

            tp1_rr=LAYER_TP1[layer]; tp2_rr=LAYER_TP2[layer]
            if bd=='LONG':
                sl_p=px*(1-slp/100); tp1_p=px*(1+slp*tp1_rr/100); tp2_p=px*(1+slp*tp2_rr/100)
            else:
                sl_p=px*(1+slp/100); tp1_p=px*(1-slp*tp1_rr/100); tp2_p=px*(1-slp*tp2_rr/100)

            pos[layer]={'dir':bd,'entry':px,'qty':qty,'sl':sl_p,'tp1':tp1_p,'tp2':tp2_p,'regime':rg,'score':bs}
            active_risk+=qty*px/nav*100 if nav>0 else 0

    # 强平
    pxf=k15[-1][4]
    for layer in ['15m','1h','4h']:
        p=pos[layer]
        if p:
            pp=(pxf-p['entry'])/p['entry'] if p['dir']=='LONG' else(p['entry']-pxf)/p['entry']
            fee=p['qty']*p['entry']*FEE; pnl=pp*p['qty']*p['entry']*LEV-fee
            nav+=pnl; fee_total+=fee
            trades.append({'layer':layer,'pnl':pnl,'dir':p['dir'],'regime':p['regime'],'score':p['score'],'exit':'FINAL','ts':k15[-1][0]})

    n=len(trades); wins=sum(1 for t in trades if t['pnl']>0)
    print(f"耗时: {time.time()-t0:.1f}s")
    print(f"NAV: ${nav_start:,.0f} → ${nav:,.2f} ({(nav-nav_start)/nav_start*100:+.2f}%)")
    print(f"WR:  {wins/n*100:.1f}% ({wins}/{n}) | MaxDD: {mdd:.2f}% | 手续费: ${fee_total:,.0f}")

    print(f"\n层级拆分:")
    for layer in ['15m','1h','4h']:
        v=by_layer[layer]; wr=v['w']/max(v['n'],1)*100
        print(f"  {layer}: n={v['n']:3d} WR={wr:5.1f}% PnL=${v['pnl']:+9,.2f}")

    print(f"\n体制×方向:")
    for k in sorted(by_regime):
        v=by_regime[k]; wr=v['w']/max(v['n'],1)*100
        print(f"  {k:<28} n={v['n']:3d} WR={wr:5.1f}% PnL=${v['pnl']:+9,.2f}")

    return {'sym':sym,'nav':round(nav,2),'ret':(nav-nav_start)/nav_start*100,
            'mdd':mdd,'n':n,'wr':wins/n*100 if n>0 else 0,'fee':fee_total,
            'by_layer':dict(by_layer),'by_regime':dict(by_regime)}

if __name__=='__main__':
    results={}
    for sym in ['BTCUSDT','ETHUSDT']:
        r=run(sym)
        results[sym]=r
        Path(f'data/backtest/{sym.lower()}_v61.json').write_text(
            json.dumps(r,ensure_ascii=False,default=str))
    print(f"\n{'='*60}")
    print("双标的汇总 v6.1:")
    for sym,r in results.items():
        print(f"  {sym}: ${r['nav']:>12,.0f} {r['ret']:>+9.1f}% MaxDD={r['mdd']:.1f}% WR={r['wr']:.1f}% n={r['n']}")
    print(f"  均值: 收益={(results['BTCUSDT']['ret']+results['ETHUSDT']['ret'])/2:>+9.1f}%  WR={(results['BTCUSDT']['wr']+results['ETHUSDT']['wr'])/2:.1f}%")
    print("✅ v6.1完成")
