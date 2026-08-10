#!/usr/bin/env python3
"""
达摩院 外测引擎 v2.0 — 无上帝视角 高效版
主战场：1H 信号源（每N根bar扫描一次，因果正确）
15M：用于精确入场时机（在1H信号触发后扫15M确认）
"""
import json, math, sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / 'data' / 'backtest'

# ── 工具函数 ──
def ema(px, n):
    if len(px) < n: return px[-1] if px else 0
    k = 2/(n+1); e = sum(px[:n])/n
    for p in px[n:]: e = e*(1-k)+p*k
    return e

def rsi(cl, n=14):
    if len(cl) < n+1: return 50.0
    d=[cl[i]-cl[i-1] for i in range(1,len(cl))]
    g=[max(0,x) for x in d[-n:]]; lo=[max(0,-x) for x in d[-n:]]
    ag,al=sum(g)/n,sum(lo)/n
    return 100-100/(1+ag/al) if al>0 else 100.0

def atr_v(h,lo,cl,n=14):
    tr=[]
    for i in range(1,len(cl)):
        tr.append(max(h[i]-lo[i],abs(h[i]-cl[i-1]),abs(lo[i]-cl[i-1])))
    return sum(tr[-n:])/min(n,len(tr)) if tr else cl[-1]*0.01

def bbw(cl, n=20):
    if len(cl)<n: return 0.05
    w=cl[-n:]; mu=sum(w)/n
    std=math.sqrt(sum((x-mu)**2 for x in w)/n)
    return std*2/mu if mu>0 else 0.05

# ── 体制判断（因果） ──
def regime_1d(c1d):
    if len(c1d)<50: return 'CHOP_MID'
    e20=ema(c1d[-20:],20); e50=ema(c1d[-50:],50)
    r=rsi(c1d[-20:]); px=c1d[-1]
    if e20>e50 and px>e20 and r>55: return 'BULL_TREND'
    if e20>e50 and r>50: return 'BULL_EARLY'
    if e20<e50 and px<e20 and r<45: return 'BEAR_TREND'
    if e20<e50 and r<50: return 'BEAR_EARLY'
    return 'CHOP_MID'

def regime_4h(c4h, reg1d):
    if len(c4h)<50: return reg1d
    e20=ema(c4h[-20:],20); e50=ema(c4h[-50:],50); r=rsi(c4h[-20:])
    if reg1d=='BEAR_TREND':
        return 'BEAR_TREND' if e20<e50 and r<50 else 'CHOP_MID'
    if reg1d=='BULL_TREND':
        return 'BULL_TREND' if e20>e50 and r>50 else 'CHOP_MID'
    return reg1d

# ── 梵天宪法常量 ──
DEAD = {('BEAR_TREND','LONG'),('BULL_TREND','SHORT'),
        ('BEAR_EARLY','SHORT'),('BULL_EARLY','LONG')}
REG_MUL = {
    'BULL_TREND':{'LONG':1.6,'SHORT':0.15},
    'BULL_EARLY':{'LONG':0.8,'SHORT':0.35},
    'CHOP_MID':  {'LONG':0.5,'SHORT':0.88},
    'BEAR_EARLY':{'LONG':0.35,'SHORT':1.2},
    'BEAR_TREND':{'LONG':0.10,'SHORT':1.6},
}
SL_MAP = {
    ('BEAR_TREND','SHORT'):0.020, ('BULL_TREND','LONG'):0.020,
    ('CHOP_MID','SHORT'):0.025,   ('CHOP_MID','LONG'):0.025,
    ('BEAR_EARLY','SHORT'):0.020, ('BULL_EARLY','LONG'):0.025,
}

# ── 1H 信号生成 ──
def sig_1h(c1h,h1h,l1h,v1h,reg4h):
    if len(c1h)<50: return None
    r1h=rsi(c1h[-20:]); e20=ema(c1h[-20:],20)
    e50=ema(c1h[-50:],50) if len(c1h)>=50 else e20
    px=c1h[-1]; bw=bbw(c1h[-20:])
    avg_v=sum(v1h[-21:-1])/20 if len(v1h)>=21 else v1h[-1]
    vr=v1h[-1]/avg_v if avg_v>0 else 1.0

    best_s=0; best_d=None
    # LONG
    if reg4h not in ('BEAR_TREND','BEAR_EARLY'):
        s=0
        if r1h<30: s+=35
        elif r1h<40: s+=22
        elif r1h<50: s+=10
        if px>e20 and e20>e50: s+=25
        elif px>e20: s+=12
        mul=REG_MUL.get(reg4h,{}).get('LONG',0.5)
        s=int(s*mul*2)  # 乘2让得分空间映射合理
        if bw<0.03 and vr>1.5: s+=20
        if s>best_s: best_s=s; best_d='LONG'
    # SHORT — EMA20 门控
    if reg4h not in ('BULL_TREND','BULL_EARLY') and px<e20:
        s=0
        if r1h>70: s+=35
        elif r1h>60: s+=22
        elif r1h>50: s+=10
        if px<e20 and e20<e50: s+=25
        elif px<e20: s+=12
        mul=REG_MUL.get(reg4h,{}).get('SHORT',0.5)
        s=int(s*mul*2)
        if bw<0.03 and vr>1.5: s+=20
        if s>best_s: best_s=s; best_d='SHORT'

    if best_s<55 or best_d is None: return None
    return {'d':best_d,'s':best_s,'r1h':r1h,'e20':e20,'reg':reg4h,
            'atr':atr_v(h1h[-20:],l1h[-20:],c1h[-20:]),'src':'1H'}

# ── 15m 精确入场（在已知1H方向后，用15m找更优入场） ──
def sig_15m_refine(c15,h15,l15,v15,direction,reg4h,e20_1h):
    if len(c15)<30: return None
    r15=rsi(c15[-15:]); e8=ema(c15[-8:],8); e21=ema(c15[-21:],21)
    px=c15[-1]
    avg_v=sum(v15[-16:-1])/15 if len(v15)>=16 else v15[-1]
    vr=v15[-1]/avg_v if avg_v>0 else 1.0
    bw15=bbw(c15[-20:])
    atr15=atr_v(h15[-15:],l15[-15:],c15[-15:])

    if direction=='LONG':
        # 15m 金叉 + RSI 未过热 + 价格>e20_1h
        if e8>e21 and 35<r15<55 and px>e20_1h*0.998 and vr>1.1:
            return {'d':'LONG','s':80,'atr15':atr15,'src':'15M'}
    else:
        # 15m 死叉 + RSI 未过冷 + 价格<e20_1h
        if e8<e21 and 45<r15<65 and px<e20_1h*1.002 and vr>1.1:
            return {'d':'SHORT','s':80,'atr15':atr15,'src':'15M'}
    return None

# ══════════════════════════════════════════════════════════════
# 主回测（以 1H 为时间轴，15M 精确入场）
# ══════════════════════════════════════════════════════════════
def run(sym, mode='1H_ONLY', verbose=False):
    """
    mode: '1H_ONLY' | '1H+15M'（15m确认精确入场）
    """
    print(f'\n{"="*65}')
    print(f'  达摩院外测 v2 · {sym} · 模式={mode}')
    print(f'{"="*65}')

    k1d  = json.load(open(DATA/f'{sym}_1d.json'))
    k4h  = json.load(open(DATA/f'{sym}_4h.json'))
    k1h  = json.load(open(DATA/f'{sym}_1h.json'))
    k15m = json.load(open(DATA/f'{sym}_15m.json')) if '15M' in mode else None

    NAV=10000.0; LEV=5.0; FEE=0.0008
    n1h=len(k1h); n4h=len(k4h); n1d=len(k1d)
    n15m=len(k15m) if k15m else 0

    trades=[]; open_pos=None
    skip_dead=0; skip_score=0

    for i in range(300, n1h-1):
        ts=k1h[i][0]
        # 对齐高周期索引
        i4h=min(int(i*n4h/n1h), n4h-2)
        i1d=min(int(i*n1d/n1h), n1d-2)
        i15m_end=min(int(i*4), n15m-2) if k15m else 0

        # 历史窗口（严格无未来）
        c1d=[float(k[4]) for k in k1d[:i1d+1][-60:]]
        c4h=[float(k[4]) for k in k4h[:i4h+1][-100:]]
        c1h=[float(k[4]) for k in k1h[:i+1][-100:]]
        h1h=[float(k[2]) for k in k1h[:i+1][-100:]]; l1h=[float(k[3]) for k in k1h[:i+1][-100:]]
        v1h=[float(k[5]) for k in k1h[:i+1][-100:]]
        px=c1h[-1]

        reg1d=regime_1d(c1d); reg4=regime_4h(c4h, reg1d)
        e20_1h=ema(c1h[-20:],20) if len(c1h)>=20 else px

        # ── 持仓管理 ──
        if open_pos:
            p=open_pos
            bh=float(k1h[i][2]); bl=float(k1h[i][3])
            closed=False; cp=px; cr='hold'

            if p['d']=='LONG':
                if bl<=p['sl']: cp=p['sl']; cr='SL'; closed=True
                elif bh>=p['tp1'] and not p['t1']:
                    p['t1']=True
                    pnl_p=(p['tp1']-p['entry'])/p['entry']-FEE
                    NAV+=NAV*p['pos']*0.5*LEV*pnl_p
                    p['pos']*=0.5; p['sl']=p['entry']
                if p['t1'] and not closed:
                    tr=px*0.985; p['sl']=max(p['sl'],tr)
                    if bl<=p['sl']: cp=p['sl']; cr='TRAIL'; closed=True
            else:
                if bh>=p['sl']: cp=p['sl']; cr='SL'; closed=True
                elif bl<=p['tp1'] and not p['t1']:
                    p['t1']=True
                    pnl_p=(p['entry']-p['tp1'])/p['entry']-FEE
                    NAV+=NAV*p['pos']*0.5*LEV*pnl_p
                    p['pos']*=0.5; p['sl']=p['entry']
                if p['t1'] and not closed:
                    tr=px*1.015; p['sl']=min(p['sl'],tr)
                    if bh>=p['sl']: cp=p['sl']; cr='TRAIL'; closed=True

            # 超时（48根1H）
            if not closed and (i-p['oi'])>48:
                cp=px; cr='TIMEOUT'; closed=True

            if closed:
                if p['d']=='LONG': fpnl=(cp-p['entry'])/p['entry']-FEE
                else: fpnl=(p['entry']-cp)/p['entry']-FEE
                NAV=max(NAV+NAV*p['pos']*LEV*fpnl, NAV*0.01)
                dt=datetime.utcfromtimestamp(ts/1000).strftime('%Y-%m-%d')
                trades.append({'ts':ts,'dt':dt,'d':p['d'],'entry':p['entry'],
                    'close':cp,'pnl':fpnl*100,'reason':cr,'reg':p['reg'],
                    'score':p['score'],'src':p['src'],'nav':NAV})
                open_pos=None

        if open_pos: continue

        # ── 信号生成 ──
        sig=sig_1h(c1h,h1h,l1h,v1h,reg4)
        if sig is None: skip_score+=1; continue
        d=sig['d']; reg=sig['reg']
        if (reg,d) in DEAD: skip_dead+=1; continue

        # 15M 精确入场确认
        entry_src='1H'; entry_atr=sig['atr']
        if '15M' in mode and k15m:
            c15=[float(k[4]) for k in k15m[:i15m_end+1][-80:]]
            h15=[float(k[2]) for k in k15m[:i15m_end+1][-80:]]
            l15=[float(k[3]) for k in k15m[:i15m_end+1][-80:]]
            v15=[float(k[5]) for k in k15m[:i15m_end+1][-80:]]
            ref=sig_15m_refine(c15,h15,l15,v15,d,reg,e20_1h)
            if ref: entry_src='15M'; entry_atr=min(sig['atr'],ref['atr15'])
            # 如果15m不确认，等下一根1H bar（此处不跳过，用1H入场）

        # 仓位
        mul=REG_MUL.get(reg,{}).get(d,0.5)
        base=0.05
        if sig['s']>=90: base*=1.5
        elif sig['s']>=75: base*=1.2
        pos=min(base*mul,0.10)
        if pos==0: skip_dead+=1; continue

        # SL 计算（铁律）
        sl_pct=SL_MAP.get((reg,d),0.025)
        min_sl=1.5*entry_atr/px if px>0 else sl_pct
        sl_pct=max(sl_pct,min_sl)
        if d=='LONG':
            sl=px*(1-sl_pct); tp1=px*(1+sl_pct*1.5); tp2=px*(1+sl_pct*3)
        else:
            sl=px*(1+sl_pct); tp1=px*(1-sl_pct*1.5); tp2=px*(1-sl_pct*3)

        open_pos={'d':d,'entry':px,'sl':sl,'tp1':tp1,'tp2':tp2,
                  't1':False,'pos':pos,'reg':reg,'score':sig['s'],
                  'src':entry_src,'oi':i}

    # ── 统计 ──
    if not trades:
        print('  无交易'); return {}

    n=len(trades); wins=sum(1 for t in trades if t['pnl']>0)
    wr=wins/n*100
    aw=sum(t['pnl'] for t in trades if t['pnl']>0)/max(wins,1)
    al=sum(t['pnl'] for t in trades if t['pnl']<=0)/max(n-wins,1)
    pf=abs(aw)/abs(al) if al!=0 else 999
    tot_ret=(NAV-10000)/10000*100

    # MaxDD
    pk=10000; mdd=0
    for t in trades:
        pk=max(pk,t['nav']); mdd=max(mdd,(pk-t['nav'])/pk*100)

    # 体制分层
    reg_st=defaultdict(lambda:[0,0,0.0])
    for t in trades:
        k=f"{t['reg']}_{t['d']}"
        reg_st[k][0]+=1 if t['pnl']>0 else 0
        reg_st[k][1]+=1; reg_st[k][2]+=t['pnl']

    # 原因统计
    reason_st=defaultdict(int)
    for t in trades: reason_st[t['reason']]+=1

    # 年度统计
    by_year=defaultdict(lambda:[0,0,0.0])
    for t in trades:
        yr=t['dt'][:4]
        by_year[yr][0]+=1 if t['pnl']>0 else 0
        by_year[yr][1]+=1; by_year[yr][2]+=t['pnl']

    # SL 滑点分析（做多时SL触发的亏损分布）
    sl_pnls=[t['pnl'] for t in trades if t['reason']=='SL']

    print(f'\n  ✅ 总交易: {n} | 胜率: {wr:.1f}% | 收益: {tot_ret:+.1f}%')
    print(f'  MaxDD: {mdd:.1f}% | 平均盈: {aw:+.2f}% | 平均亏: {al:+.2f}%')
    print(f'  PF: {pf:.2f} | 净值: {NAV:.0f}U | 跳过: 死穴={skip_dead} 低分={skip_score}')

    print(f'\n  ── 体制×方向 分层（过滤n<5）──')
    for k,(w,tot,pnl) in sorted(reg_st.items(),key=lambda x:-x[1][0]/max(1,x[1][1])):
        if tot<5: continue
        wr_k=w/tot*100; ev=pnl/tot
        flag='✅' if wr_k>=52 else('⚠️' if wr_k>=45 else '❌')
        print(f'    {k:25s}: WR={wr_k:.1f}% n={tot:4d} EV={ev:+.2f}%/笔 {flag}')

    print(f'\n  ── 按年度 ──')
    for yr,(w,tot,pnl) in sorted(by_year.items()):
        wr_y=w/tot*100; ev=pnl/tot
        flag='✅' if ev>0 else '❌'
        print(f'    {yr}: WR={wr_y:.1f}% n={tot:3d} EV={ev:+.2f}%/笔 总PnL={pnl:+.1f}% {flag}')

    print(f'\n  ── 平仓原因 ──')
    for r,c in sorted(reason_st.items(),key=lambda x:-x[1]):
        pct=c/n*100
        print(f'    {r:12s}: {c:4d}笔 ({pct:.1f}%)')

    print(f'\n  ── SL 止损统计（n={len(sl_pnls)}）──')
    if sl_pnls:
        avg_sl=sum(sl_pnls)/len(sl_pnls)
        print(f'    平均SL亏损={avg_sl:.2f}% | 最大单笔={min(sl_pnls):.2f}%')

    return {'sym':sym,'mode':mode,'n':n,'wr':wr,'tot_ret':tot_ret,'mdd':mdd,
            'pf':pf,'aw':aw,'al':al,'nav':NAV}

if __name__=='__main__':
    results={}
    for sym in ['BTCUSDT','ETHUSDT']:
        for mode in ['1H_ONLY','1H+15M']:
            r=run(sym,mode)
            if r: results[f'{sym}_{mode}']=r

    print('\n'+'='*65)
    print('  ╔═══ 达摩院外测汇总（无上帝视角）═══╗')
    print('='*65)
    print(f'  {"标的+模式":22s}|{"n":5s}|{"WR":6s}|{"收益":8s}|{"MaxDD":7s}|{"PF":5s}|{"EV/笔":7s}')
    print('  '+'-'*64)
    for k,r in results.items():
        ev=r['aw']+r['al']*(1-r['wr']/100) if r.get('aw') else 0
        # 简化EV = avg_win*wr + avg_loss*(1-wr)
        ev2=(r['aw']*r['wr']/100+r['al']*(1-r['wr']/100))
        print(f'  {k:22s}|{r["n"]:5d}|{r["wr"]:5.1f}%|{r["tot_ret"]:+6.1f}%|{r["mdd"]:5.1f}%|{r["pf"]:5.2f}|{ev2:+.3f}%')
