"""dharma_rr3.py — 精简版：只跑3个最关键配置"""
import gzip, json, math, time
from datetime import datetime
from collections import defaultdict
from pathlib import Path
BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR = BASE / "data"
exec(open(BASE / "scripts" / "dharma_ultimate_validator.py").read().split("def simulate_exit_ultimate")[0])

def score_slim(regime, direction, rsi, res, bb_w):
    s=50.0
    bonus={('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0}
    s+=bonus.get((regime,direction),0)
    if direction=='LONG':
        if rsi and rsi<30:s+=15
        elif rsi and rsi>70:s-=15
    else:
        if rsi and rsi>70:s+=15
        elif rsi and rsi<30:s-=15
    if bb_w<1.0:s+=12
    elif bb_w>5.0:s-=8
    if res['score']>=3:s+=10
    elif res['score']==2:s+=5
    elif res['score']==0:s-=3
    return max(0,min(150,s))

def sim_exit(bars,idx,d,ep,atr,tf,rr=2.0,cost=0.0002,slip=0.0005):
    sl_pct=0.02;hold={'15m':48,'1h':24}.get(tf,24)
    min_sl=1.5*atr if atr else ep*0.01;sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG':sl=ep-sl_d;tp=ep+sl_d*rr
    else:sl=ep+sl_d;tp=ep-sl_d*rr
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if d=='LONG':
            if b['l']<=sl:g=(sl-ep)/ep;return{'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-cost-slip)*100,'exit':'SL'}
            if b['h']>=tp:g=(tp-ep)/ep;return{'is_win':True,'pnl_gross':g*100,'pnl_net':(g-cost-slip)*100,'exit':'TP'}
        else:
            if b['h']>=sl:g=(ep-sl)/ep;return{'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-cost-slip)*100,'exit':'SL'}
            if b['l']<=tp:g=(ep-tp)/ep;return{'is_win':True,'pnl_gross':g*100,'pnl_net':(g-cost-slip)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return{'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-cost-slip)*100,'exit':'TIME'}

def calc_ic(sigs):
    if len(sigs)<10:return 0.0
    s=[x['score'] for x in sigs];o=[1.0 if x['is_win'] else 0.0 for x in sigs]
    n=len(s);ms=sum(s)/n;mo=sum(o)/n
    cov=sum((s[i]-ms)*(o[i]-mo) for i in range(n))/n
    ss=math.sqrt(sum((x-ms)**2 for x in s)/n);so=math.sqrt(sum((x-mo)**2 for x in o)/n)
    return round(cov/(ss*so),4) if ss and so else 0.0

def run_bt(label,rr=2.5,min_score=110):
    all=[];S={('BULL_TREND','LONG'),('BEAR_TREND','SHORT')}
    for sym in ['BTC','ETH']:
        SYM=sym.upper()+'USDT'
        for tf in ['15m','1h']:
            bars=load_klines(SYM,tf);rmap=load_regime_labels(SYM)
            if not bars:continue
            atrs=calc_atr(bars,14);rsis=calc_rsi(bars,14)
            mg={'15m':4*15*60*1000,'1h':4*3600*1000}.get(tf,4*3600*1000)
            sigs=[];lt=0
            for i in range(100,len(bars)-50):
                b=bars[i];ts=b['ts']
                if ts-lt<mg:continue
                t4=(ts//(4*3600*1000))*(4*3600*1000)-4*3600*1000
                reg=rmap.get(t4)
                if not reg:
                    for off in range(-3,4):
                        reg=rmap.get(t4+off*4*3600*1000)
                        if reg:break
                if not reg:continue
                atr=atrs[i];rsi=rsis[i]
                if not atr or not rsi:continue
                if i>=20:
                    cl=[bars[j]['c'] for j in range(i-20,i)]
                    mc=sum(cl)/20;sc=math.sqrt(sum((c-mc)**2 for c in cl)/20)
                    bw=sc*2/mc*100 if mc else 0
                else:bw=0
                fvg=detect_fvg_advanced(bars,i);ob=detect_ob_advanced(bars,i)
                liq=build_liquidity_map(bars,i);res=detect_resonance(bars,i,fvg,ob,liq,atr)
                for d in ['LONG','SHORT']:
                    if (reg,d) not in S:continue
                    sv=score_slim(reg,d,rsi,res,bw)
                    if sv<min_score:continue
                    ni=i+1
                    if ni>=len(bars):continue
                    ep=bars[ni]['o']
                    er=sim_exit(bars,ni,d,ep,atr,tf,rr=rr)
                    sigs.append({'ts':ts,'score':round(sv,1),'direction':d,'regime':reg,'tf':tf,**er})
                    lt=ts
            all.extend(sigs)
    n=len(all)
    if n==0:print(f"  {label}: ❌");return None
    wr=sum(1 for s in all if s['is_win'])/n*100
    ic=calc_ic(all);pn=sum(s['pnl_net'] for s in all)/n
    ed=defaultdict(int)
    for s in all:ed[s.get('exit','?')]+=1
    pos=100000*0.05*5;fee=pos*(0.0002+0.0005)*2;sl_d=0.02
    tp=0;cum=0;pk=0;mdd=0;pl=[]
    for s in all:
        p=(sl_d*rr*pos-fee) if s['is_win'] else(-sl_d*pos-fee)
        tp+=p;cum+=p
        if cum>pk:pk=cum
        if pk-cum>mdd:mdd=pk-cum
        pl.append(p)
    mp=sum(pl)/len(pl);sp=math.sqrt(sum((p-mp)**2 for p in pl)/len(pl)) if len(pl)>1 else 0
    tpy=min(n/4.5,5*365);sh=mp/sp*math.sqrt(tpy) if sp>0 else 0
    if n>100:
        tmin=min(s['ts'] for s in all);tmax=max(s['ts'] for s in all)
        tm=(tmax-tmin)/(86400*30*1000);bpm=n/tm
        folds=[]
        for f in range(min(9,max(3,int((tm-24-6)/3)+1)) if tm>30 else 3):
            te=int((f*3+24)*bpm);e2=int((f*3+30)*bpm)
            fs=all[te:e2]
            if len(fs)<10:continue
            wrf=sum(1 for s in fs if s['is_win'])/len(fs)*100
            evf=sum(s['pnl_net'] for s in fs)/len(fs)
            folds.append({'f':f+1,'n':len(fs),'wr':round(wrf,1),'ev':round(evf,3)})
        pf=sum(1 for f in folds if f['wr']>=52);ff=sum(1 for f in folds if f['wr']<50)
        stab=pf/len(folds) if folds else 0
    else:folds=[];pf=0;ff=0;stab=0
    pc=sum([pn>0,stab>=0.6,sh>0.5,mdd<50000])
    print(f"\n  📊 {label}")
    print(f"  {'✅' if pn>0 else '❌'} n={n} WR={wr:.1f}% IC={ic:+.4f} net={pn:+.3f}% Sharpe={sh:.2f} DD=${mdd:,.0f}")
    print(f"  出场: {dict(ed)}")
    print(f"  {'✅' if stab>=0.6 else '⚠️'} WF: {len(folds)}折 稳定性={stab:.1%} 通过={pf} 失败={ff}")
    for f in folds:
        fl='✅' if f['wr']>=52 else('⚠️' if f['wr']>=50 else '❌')
        print(f"    {fl} F{f['f']}: n={f['n']:>5} WR={f['wr']:>5.1f}% EV={f['ev']:>+.3f}%")
    print(f"  硬标准: {pc}/4 {'🟢全通过!' if pc==4 else '⚠️'}")
    return{'label':label,'n':n,'wr':round(wr,1),'ic':ic,'pnl_net':round(pn,3),'sharpe':round(sh,2),'max_dd':round(mdd,0),'wf_stab':round(stab,3),'wf_pass':pf,'hard_pass':pc}

def main():
    t0=time.time();R={}
    print("🔥 ms=110+maker RR扫描（3配置）")
    print("="*70)
    R['rr20']=run_bt('ms=110 maker RR=2.0',rr=2.0)
    R['rr25']=run_bt('ms=110 maker RR=2.5',rr=2.5)
    R['rr30']=run_bt('ms=110 maker RR=3.0',rr=3.0)
    el=time.time()-t0
    print(f"\n{'='*70}\n📊 汇总\n{'='*70}")
    print(f"{'配置':<25} {'n':>6} {'WR':>6} {'IC':>8} {'netEV':>8} {'Sharpe':>7} {'WF':>6} {'硬标准':>6}")
    print("-"*75)
    for k,r in R.items():
        if r:print(f"{r['label']:<25} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['pnl_net']:>+7.3f}% {r['sharpe']:>7.2f} {r['wf_stab']:>5.1%} {r['hard_pass']:>5}/4")
    ap=[r for r in R.values() if r and r['hard_pass']==4]
    if ap:
        b=max(ap,key=lambda x:x['pnl_net'])
        print(f"\n🟢 全标准通过: {b['label']} EV={b['pnl_net']:+.3f}% WR={b['wr']:.1f}% WF={b['wf_stab']:.1%}")
    else:
        eb=max([r for r in R.values() if r],key=lambda x:x['pnl_net'])
        print(f"\n⚠️ 最优: {eb['label']} EV={eb['pnl_net']:+.3f}% WF={eb['wf_stab']:.1%}")
    print(f"\n耗时: {el:.1f}s")
    with open(OUT_DIR/'dharma_rr_sweep_result.json','w') as f:
        json.dump({'ts':datetime.utcnow().isoformat()+'Z','results':{k:v for k,v in R.items() if v}},f,ensure_ascii=False,indent=2,default=str)

if __name__=='__main__':
    main()
