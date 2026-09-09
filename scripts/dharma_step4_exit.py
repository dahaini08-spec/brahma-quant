"""dharma_step4_exit.py — Step4: 5种出场方式 + Step5: 组合最优"""
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

# ═══ 5种出场方式 ═══
def exit_natural(bars, idx, d, ep, atr, tf, rr=2.0):
    """出场1: 自然SL/TP（基线）"""
    sl_pct=0.02; hold={'15m':48,'1h':24}.get(tf,24)
    min_sl=1.5*atr if atr else ep*0.01
    sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG': sl=ep-sl_d; tp=ep+sl_d*rr
    else: sl=ep+sl_d; tp=ep-sl_d*rr
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if d=='LONG':
            if b['l']<=sl:
                g=(sl-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if b['h']>=tp:
                g=(tp-ep)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
        else:
            if b['h']>=sl:
                g=(ep-sl)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if b['l']<=tp:
                g=(ep-tp)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TIME'}

def exit_trailing(bars, idx, d, ep, atr, tf, rr=2.0):
    """出场2: trailing SL（仅大利润移动）"""
    sl_pct=0.02; hold={'15m':48,'1h':24}.get(tf,24)
    min_sl=1.5*atr if atr else ep*0.01
    sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG': sl=ep-sl_d; tp=ep+sl_d*rr
    else: sl=ep+sl_d; tp=ep-sl_d*rr
    tsl=sl  # trailing SL初始=原始SL
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        # 更新trailing: 浮盈>4ATR锁2ATR，>8ATR锁5ATR
        if atr:
            if d=='LONG':
                profit=b['h']-ep
                if profit>8*atr: tsl=max(tsl,ep+5*atr)
                elif profit>4*atr: tsl=max(tsl,ep+2*atr)
            else:
                profit=ep-b['l']
                if profit>8*atr: tsl=min(tsl,ep-5*atr)
                elif profit>4*atr: tsl=min(tsl,ep-2*atr)
        if d=='LONG':
            if b['l']<=tsl:
                g=(tsl-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TSL' if tsl>sl else 'SL'}
            if b['h']>=tp:
                g=(tp-ep)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
        else:
            if b['h']>=tsl:
                g=(ep-tsl)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TSL' if tsl<sl else 'SL'}
            if b['l']<=tp:
                g=(ep-tp)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TIME'}

def exit_structure(bars, idx, d, ep, atr, tf, rr=2.0):
    """出场3: 结构感知（OB失效出场+自然SL/TP）"""
    sl_pct=0.02; hold={'15m':48,'1h':24}.get(tf,24)
    min_sl=1.5*atr if atr else ep*0.01
    sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG': sl=ep-sl_d; tp=ep+sl_d*rr
    else: sl=ep+sl_d; tp=ep-sl_d*rr
    # 获取入场点附近的OB作为结构参考
    ob_ref = detect_ob_advanced(bars, idx)
    ob_top = ob_ref.get('top', 0) or 0
    ob_bottom = ob_ref.get('bottom', 0) or 0
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if d=='LONG':
            # SL: 价格跌破OB bottom = 结构失效
            if b['l']<=sl:
                g=(sl-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if ob_bottom and b['l']<=ob_bottom and b['c']<ob_bottom:
                g=(b['c']-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'OB_FAIL'}
            if b['h']>=tp:
                g=(tp-ep)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
        else:
            if b['h']>=sl:
                g=(ep-sl)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if ob_top and b['h']>=ob_top and b['c']>ob_top:
                g=(ep-b['c'])/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'OB_FAIL'}
            if b['l']<=tp:
                g=(ep-tp)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TIME'}

def exit_structure_trailing(bars, idx, d, ep, atr, tf, rr=2.0):
    """出场4: 结构感知 + trailing SL"""
    sl_pct=0.02; hold={'15m':48,'1h':24}.get(tf,24)
    min_sl=1.5*atr if atr else ep*0.01
    sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG': sl=ep-sl_d; tp=ep+sl_d*rr
    else: sl=ep+sl_d; tp=ep-sl_d*rr
    ob_ref = detect_ob_advanced(bars, idx)
    ob_top = ob_ref.get('top', 0) or 0
    ob_bottom = ob_ref.get('bottom', 0) or 0
    tsl=sl
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if atr:
            if d=='LONG':
                profit=b['h']-ep
                if profit>8*atr: tsl=max(tsl,ep+5*atr)
                elif profit>4*atr: tsl=max(tsl,ep+2*atr)
            else:
                profit=ep-b['l']
                if profit>8*atr: tsl=min(tsl,ep-5*atr)
                elif profit>4*atr: tsl=min(tsl,ep-2*atr)
        if d=='LONG':
            if b['l']<=tsl:
                g=(tsl-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TSL' if tsl>sl else 'SL'}
            if ob_bottom and b['l']<=ob_bottom and b['c']<ob_bottom:
                g=(b['c']-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'OB_FAIL'}
            if b['h']>=tp:
                g=(tp-ep)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
        else:
            if b['h']>=tsl:
                g=(ep-tsl)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TSL' if tsl<sl else 'SL'}
            if ob_top and b['h']>=ob_top and b['c']>ob_top:
                g=(ep-b['c'])/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'OB_FAIL'}
            if b['l']<=tp:
                g=(ep-tp)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TIME'}

def exit_adaptive_hold(bars, idx, d, ep, atr, tf, rr=2.0):
    """出场5: 体制自适应hold时间"""
    sl_pct=0.02
    # 趋势体制延长hold，震荡缩短
    # 通过rmap传入regime，这里简化用bars趋势判断
    # 用50根EMA判断
    if len(bars) > idx + 50:
        ema50 = sum(bars[j]['c'] for j in range(idx-50, idx)) / 50
        is_trend = bars[idx]['c'] > ema50 * 1.02 or bars[idx]['c'] < ema50 * 0.98
    else:
        is_trend = False
    hold = (36 if is_trend else 12) if tf == '1h' else (72 if is_trend else 24)
    min_sl=1.5*atr if atr else ep*0.01
    sl_d=max(ep*sl_pct,min_sl)
    if d=='LONG': sl=ep-sl_d; tp=ep+sl_d*rr
    else: sl=ep+sl_d; tp=ep-sl_d*rr
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if d=='LONG':
            if b['l']<=sl:
                g=(sl-ep)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if b['h']>=tp:
                g=(tp-ep)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
        else:
            if b['h']>=sl:
                g=(ep-sl)/ep; return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'SL'}
            if b['l']<=tp:
                g=(ep-tp)/ep; return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TP'}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit':'TIME'}

EXITS = {
    'natural': exit_natural,
    'trailing': exit_trailing,
    'structure': exit_structure,
    'structure+trailing': exit_structure_trailing,
    'adaptive_hold': exit_adaptive_hold,
}

def calc_ic(sigs):
    if len(sigs)<10: return 0.0
    s=[x['score'] for x in sigs]; o=[1.0 if x['is_win'] else 0.0 for x in sigs]
    n=len(s); ms=sum(s)/n; mo=sum(o)/n
    cov=sum((s[i]-ms)*(o[i]-mo) for i in range(n))/n
    ss=math.sqrt(sum((x-ms)**2 for x in s)/n); so=math.sqrt(sum((x-mo)**2 for x in o)/n)
    return round(cov/(ss*so),4) if ss and so else 0.0

def run_bt(label, exit_fn=None, rr=2.0, min_score=90, whitelist=None):
    all_sigs=[]
    for sym in ['BTC','ETH']:
        SYM=sym.upper()+'USDT'
        for tf in ['15m','1h']:
            bars=load_klines(SYM,tf)
            rmap=load_regime_labels(SYM)
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
                    if whitelist and (regime,d) not in whitelist: continue
                    sc_val=score_slim(regime,d,rsi,res,bb_w)
                    if sc_val<min_score: continue
                    ni=i+1
                    if ni>=len(bars): continue
                    ep=bars[ni]['o']
                    er=exit_fn(bars,ni,d,ep,atr,tf,rr=rr)
                    sigs.append({'ts':ts,'score':round(sc_val,1),'direction':d,'regime':regime,'tf':tf,**er})
                    last_ts=ts
            all_sigs.extend(sigs)
    n=len(all_sigs)
    if n==0: print(f"  {label}: ❌ 无信号"); return None
    wr=sum(1 for s in all_sigs if s['is_win'])/n*100
    ic=calc_ic(all_sigs)
    pnl_n=sum(s['pnl_net'] for s in all_sigs)/n
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
    mp=sum(pl)/len(pl) if pl else 0
    sp=math.sqrt(sum((p-mp)**2 for p in pl)/len(pl)) if len(pl)>1 else 0
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
    t0=time.time()
    R={}
    S={('BULL_TREND','LONG'),('BEAR_TREND','SHORT')}

    # ═══ Step4: 5种出场方式（最强+RR=2.0+ms=90） ═══
    print("="*70)
    print("🔥 Step4: 5种出场方式对比（最强+RR=2.0+ms=90）")
    print("="*70)
    for name, fn in EXITS.items():
        R[f's4_{name}']=run_bt(f'最强 {name} RR=2.0',exit_fn=fn,rr=2.0,min_score=90,whitelist=S)

    # ═══ Step5: 组合最优（ms=100+各出场方式） ═══
    print("\n"+"="*70)
    print("🔥 Step5: 组合最优（ms=100+RR=2.0+各出场方式）")
    print("="*70)
    for name, fn in EXITS.items():
        R[f's5_ms100_{name}']=run_bt(f'ms=100 {name} RR=2.0',exit_fn=fn,rr=2.0,min_score=100,whitelist=S)

    elapsed=time.time()-t0
    print("\n"+"="*70)
    print("📊 Step4+5 汇总")
    print("="*70)
    print(f"\n{'配置':<35} {'n':>6} {'WR':>6} {'IC':>8} {'netEV':>8} {'Sharpe':>7} {'WFstab':>7} {'WFpass':>6}")
    print("-"*85)
    for k,r in R.items():
        if r: print(f"{r['label']:<35} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['pnl_net']:>+7.3f}% {r['sharpe']:>7.2f} {r['wf_stab']:>6.1%} {r['wf_pass']:>5}")

    # 找WF最优
    wf_best = max([r for r in R.values() if r], key=lambda x: x.get('wf_stab',0))
    ev_best = max([r for r in R.values() if r and r['pnl_net']>0] or [r for r in R.values() if r], key=lambda x: x.get('pnl_net',-999))

    print(f"\n  📋 最高WF稳定性: {wf_best['label']} WF={wf_best['wf_stab']:.1%} 通过={wf_best['wf_pass']}")
    print(f"  📋 最高net EV: {ev_best['label']} EV={ev_best['pnl_net']:+.3f}% Sharpe={ev_best['sharpe']:.2f}")

    valid=[r for r in R.values() if r and r['pnl_net']>0 and r.get('wf_stab',0)>=0.6]
    if valid:
        best=max(valid,key=lambda x:x['pnl_net'])
        print(f"\n  🟢 通过全部标准: {best['label']} EV={best['pnl_net']:+.3f}% WF={best['wf_stab']:.1%}")
    else:
        print(f"\n  ⚠️ 无配置同时通过 net EV>0 + WF≥60%")
        print(f"     最高EV: {ev_best['label']} EV={ev_best['pnl_net']:+.3f}% WF={ev_best.get('wf_stab',0):.1%}")

    print(f"\n耗时: {elapsed:.1f}s")
    with open(OUT_DIR/'dharma_step4_5_result.json','w') as f:
        json.dump({'ts':datetime.utcnow().isoformat()+'Z','results':{k:v for k,v in R.items() if v}},f,ensure_ascii=False,indent=2,default=str)

if __name__=='__main__':
    main()
