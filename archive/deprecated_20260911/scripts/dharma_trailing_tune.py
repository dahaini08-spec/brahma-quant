"""dharma_trailing_tune.py — trailing SL阈值调优：4/6/8/10/12 ATR"""
import gzip, json, math, time
from datetime import datetime
from collections import defaultdict
from pathlib import Path
BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR = BASE / "data"
COST = 0.0008; SLIPPAGE = 0.0005
exec(open(BASE / "scripts" / "dharma_ultimate_validator.py").read().split("def simulate_exit_ultimate")[0])

def score_slim(regime, direction, rsi, res, bb_w):
    s = 50.0
    bonus = {('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,
        ('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
        ('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,
        ('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,
        ('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0}
    s += bonus.get((regime,direction),0)
    if direction=='LONG':
        if rsi and rsi<30: s+=15
        elif rsi and rsi>70: s-=15
    else:
        if rsi and rsi>70: s+=15
        elif rsi and rsi<30: s-=15
    if bb_w<1.0: s+=12
    elif bb_w>5.0: s-=8
    if res['score']>=3: s+=10
    elif res['score']==2: s+=5
    elif res['score']==0: s-=3
    return max(0, min(150, s))

def exit_trailing_tuned(bars, idx, d, ep, atr, tf, rr=2.0, t1=4, t2=8):
    """trailing SL with configurable ATR thresholds
    t1: 锁利触发ATR倍数（浮盈>t1*ATR → SL移到入场+t1/2*ATR）
    t2: 大利触发ATR倍数（浮盈>t2*ATR → SL移到入场+t2/2*ATR）
    """
    sl_pct=0.02; hold={'15m':48,'1h':24}.get(tf,24)
    min_sl=1.5*atr if atr else ep*0.01; sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG': sl=ep-sl_d; tp=ep+sl_d*rr
    else: sl=ep+sl_d; tp=ep-sl_d*rr
    tsl=sl
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if atr:
            if d=='LONG':
                profit=b['h']-ep
                if profit>t2*atr: tsl=max(tsl,ep+(t2/2)*atr)
                elif profit>t1*atr: tsl=max(tsl,ep+(t1/2)*atr)
            else:
                profit=ep-b['l']
                if profit>t2*atr: tsl=min(tsl,ep-(t2/2)*atr)
                elif profit>t1*atr: tsl=min(tsl,ep-(t1/2)*atr)
        if d=='LONG':
            if b['l']<=tsl: g=(tsl-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TSL' if tsl>sl else 'SL'}
            if b['h']>=tp: g=(tp-ep)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
        else:
            if b['h']>=tsl: g=(ep-tsl)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TSL' if tsl<sl else 'SL'}
            if b['l']<=tp: g=(ep-tp)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TIME'}

def exit_natural(bars, idx, d, ep, atr, tf, rr=2.0):
    sl_pct=0.02; hold={'15m':48,'1h':24}.get(tf,24)
    min_sl=1.5*atr if atr else ep*0.01; sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG': sl=ep-sl_d; tp=ep+sl_d*rr
    else: sl=ep+sl_d; tp=ep-sl_d*rr
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if d=='LONG':
            if b['l']<=sl: g=(sl-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if b['h']>=tp: g=(tp-ep)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
        else:
            if b['h']>=sl: g=(ep-sl)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if b['l']<=tp: g=(ep-tp)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TIME'}

def calc_ic(sigs):
    if len(sigs)<10: return 0.0
    s=[x['score'] for x in sigs]; o=[1.0 if x['is_win'] else 0.0 for x in sigs]
    n=len(s); ms=sum(s)/n; mo=sum(o)/n
    cov=sum((s[i]-ms)*(o[i]-mo) for i in range(n))/n
    ss=math.sqrt(sum((x-ms)**2 for x in s)/n); so=math.sqrt(sum((x-mo)**2 for x in o)/n)
    return round(cov/(ss*so),4) if ss and so else 0.0

def run_bt(label, exit_fn, rr=2.0, min_score=100, **exit_kw):
    all_sigs=[]; S={('BULL_TREND','LONG'),('BEAR_TREND','SHORT')}
    for sym in ['BTC','ETH']:
        SYM=sym.upper()+'USDT'
        for tf in ['15m','1h']:
            bars=load_klines(SYM,tf); rmap=load_regime_labels(SYM)
            if not bars: continue
            atrs=calc_atr(bars,14); rsis=calc_rsi(bars,14)
            min_gap={'15m':4*15*60*1000,'1h':4*3600*1000}.get(tf,4*3600*1000)
            sigs=[]; last_ts=0
            for i in range(100,len(bars)-50):
                b=bars[i]; ts=b['ts']
                if ts-last_ts<min_gap: continue
                ts_4h=(ts//(4*3600*1000))*(4*3600*1000)-4*3600*1000
                regime=rmap.get(ts_4h)
                if not regime:
                    for off in range(-3,4):
                        regime=rmap.get(ts_4h+off*4*3600*1000)
                        if regime: break
                if not regime: continue
                atr=atrs[i]; rsi=rsis[i]
                if not atr or not rsi: continue
                if i>=20:
                    cl=[bars[j]['c'] for j in range(i-20,i)]
                    mc=sum(cl)/20; sc=math.sqrt(sum((c-mc)**2 for c in cl)/20)
                    bb_w=sc*2/mc*100 if mc else 0
                else: bb_w=0
                fvg=detect_fvg_advanced(bars,i); ob=detect_ob_advanced(bars,i)
                liq=build_liquidity_map(bars,i); res=detect_resonance(bars,i,fvg,ob,liq,atr)
                for d in ['LONG','SHORT']:
                    if (regime,d) not in S: continue
                    sc_val=score_slim(regime,d,rsi,res,bb_w)
                    if sc_val<min_score: continue
                    ni=i+1
                    if ni>=len(bars): continue
                    ep=bars[ni]['o']
                    er=exit_fn(bars,ni,d,ep,atr,tf,rr=rr,**exit_kw)
                    sigs.append({'ts':ts,'score':round(sc_val,1),'direction':d,'regime':regime,'tf':tf,**er})
                    last_ts=ts
            all_sigs.extend(sigs)
    n=len(all_sigs)
    if n==0: print(f"  {label}: ❌"); return None
    wr=sum(1 for s in all_sigs if s['is_win'])/n*100
    ic=calc_ic(all_sigs); pnl_n=sum(s['pnl_net'] for s in all_sigs)/n
    ed=defaultdict(int)
    for s in all_sigs: ed[s.get('exit','?')]+=1
    nav=100000;pos=nav*0.05*5;fee=pos*(COST+SLIPPAGE)*2;sl_d=0.02
    tp=0;cum=0;pk=0;mdd=0;pl=[]
    for s in all_sigs:
        p=(sl_d*rr*pos-fee) if s['is_win'] else (-sl_d*pos-fee)
        tp+=p;cum+=p
        if cum>pk: pk=cum
        if pk-cum>mdd: mdd=pk-cum
        pl.append(p)
    mp=sum(pl)/len(pl); sp=math.sqrt(sum((p-mp)**2 for p in pl)/len(pl)) if len(pl)>1 else 0
    tpy=min(n/4.5,5*365);sh=mp/sp*math.sqrt(tpy) if sp>0 else 0
    if n>100:
        tmin=min(s['ts'] for s in all_sigs);tmax=max(s['ts'] for s in all_sigs)
        tm=(tmax-tmin)/(86400*30*1000);bpm=n/tm
        folds=[]
        for f in range(min(9,max(3,int((tm-24-6)/3)+1)) if tm>30 else 3):
            te=int((f*3+24)*bpm);ed2=int((f*3+30)*bpm)
            fs=all_sigs[te:ed2]
            if len(fs)<10: continue
            wrf=sum(1 for s in fs if s['is_win'])/len(fs)*100
            evf=sum(s['pnl_net'] for s in fs)/len(fs)
            folds.append({'f':f+1,'n':len(fs),'wr':round(wrf,1),'ev':round(evf,3)})
        pf=sum(1 for f in folds if f['wr']>=52);ff=sum(1 for f in folds if f['wr']<50)
        stab=pf/len(folds) if folds else 0
    else: folds=[];pf=0;ff=0;stab=0
    flag='✅' if pnl_n>0 else '❌'
    print(f"\n  📊 {label}")
    print(f"  {flag} n={n} WR={wr:.1f}% IC={ic:+.4f} net={pnl_n:+.3f}% Sharpe={sh:.2f} DD=${mdd:,.0f}")
    print(f"  出场: {dict(ed)}")
    print(f"  WF: {len(folds)}折 稳定性={stab:.1%} 通过={pf} 失败={ff}")
    for f in folds:
        fl='✅' if f['wr']>=52 else ('⚠️' if f['wr']>=50 else '❌')
        print(f"    {fl} F{f['f']}: n={f['n']:>5} WR={f['wr']:>5.1f}% EV={f['ev']:>+.3f}%")
    return {'label':label,'n':n,'wr':round(wr,1),'ic':ic,'pnl_net':round(pnl_n,3),
            'sharpe':round(sh,2),'max_dd':round(mdd,0),'wf_stab':round(stab,3),'wf_pass':pf,'wf_fail':ff,
            'exit_dist':dict(ed)}

def main():
    t0=time.time(); R={}

    print("="*70)
    print("🔥 trailing SL阈值调优（ms=100+RR=2.0+最强组合）")
    print("="*70)

    # 基线：natural
    R['natural']=run_bt('natural（基线）',exit_natural)

    # 5种trailing阈值：t1=锁利触发, t2=大利触发
    configs = [
        (4, 8, '4/8 ATR'),   # 原版（已知WF=77.8% EV=-0.118%）
        (6, 12, '6/12 ATR'),
        (8, 16, '8/16 ATR'),
        (10, 20, '10/20 ATR'),
        (6, 10, '6/10 ATR'),
        (8, 12, '8/12 ATR'),
    ]
    for t1, t2, desc in configs:
        R[f't{t1}_{t2}']=run_bt(f'trailing {desc}',exit_trailing_tuned,t1=t1,t2=t2)

    elapsed=time.time()-t0
    print("\n"+"="*70)
    print("📊 trailing SL调优汇总")
    print("="*70)
    print(f"\n{'配置':<25} {'n':>6} {'WR':>6} {'IC':>8} {'netEV':>8} {'Sharpe':>7} {'WFstab':>7} {'WFpass':>6}")
    print("-"*80)
    for k,r in R.items():
        if r: print(f"{r['label']:<25} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['pnl_net']:>+7.3f}% {r['sharpe']:>7.2f} {r['wf_stab']:>6.1%} {r['wf_pass']:>5}")

    # 找同时通过net EV>0 + WF>=60%的配置
    valid=[r for r in R.values() if r and r['pnl_net']>0 and r.get('wf_stab',0)>=0.6]
    if valid:
        best=max(valid,key=lambda x:x['pnl_net'])
        print(f"\n  🟅 同时通过: {best['label']} EV={best['pnl_net']:+.3f}% WF={best['wf_stab']:.1%}")
        print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f} Sharpe={best['sharpe']:.2f}")
    else:
        # 找最接近的
        allr=[r for r in R.values() if r]
        # 优先WF>=60%里EV最高的
        wf_pass=[r for r in allr if r.get('wf_stab',0)>=0.6]
        if wf_pass:
            best=max(wf_pass,key=lambda x:x['pnl_net'])
            print(f"\n  ⚠️ WF通过但EV仍负: {best['label']} EV={best['pnl_net']:+.3f}% WF={best['wf_stab']:.1%}")
        else:
            # EV最高的
            best=max(allr,key=lambda x:x['pnl_net'])
            print(f"\n  ⚠️ 最优EV: {best['label']} EV={best['pnl_net']:+.3f}% WF={best.get('wf_stab',0):.1%}")

    print(f"\n耗时: {elapsed:.1f}s")
    with open(OUT_DIR/'dharma_trailing_tune_result.json','w') as f:
        json.dump({'ts':datetime.utcnow().isoformat()+'Z','results':{k:v for k,v in R.items() if v}},f,ensure_ascii=False,indent=2,default=str)

if __name__=='__main__':
    main()
