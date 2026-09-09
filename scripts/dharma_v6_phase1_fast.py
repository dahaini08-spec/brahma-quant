"""
dharma_v6_phase1_fast.py — Phase 1 精简版（分批跑，避免OOM）
苏摩111 2026-09-09

只跑最关键的3个验证：
  1. 最强组合 only（BULL_TREND:LONG + BEAR_TREND:SHORT）
  2. 全组合（加BULL_EARLY:LONG + BEAR_RECOVERY:LONG）
  3. 最强组合 + RR搜索（1.2/1.5/1.8/2.0）
"""
import gzip, json, math, time, os, sys
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR = BASE / "data"
COST = 0.0008; SLIPPAGE = 0.0005

exec(open(BASE / "scripts" / "dharma_ultimate_validator.py").read().split("def simulate_exit_ultimate")[0])

def score_v5_slim(regime, direction, rsi, resonance, bb_w):
    s = 50.0
    bonus = {('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,
        ('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
        ('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,
        ('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,
        ('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0,
        ('CHOP_HIGH','LONG'):5,('CHOP_HIGH','SHORT'):5}
    s += bonus.get((regime,direction),0)
    if direction=='LONG':
        if rsi and rsi<30: s+=15
        elif rsi and rsi>70: s-=15
    else:
        if rsi and rsi>70: s+=15
        elif rsi and rsi<30: s-=15
    if bb_w<1.0: s+=12
    elif bb_w>5.0: s-=8
    if resonance['score']>=3: s+=10
    elif resonance['score']==2: s+=5
    elif resonance['score']==0: s-=3
    return max(0, min(150, s))

def sim_exit(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    sl_pct = 0.02
    hold = {'15m':48,'1h':24,'4h':12}.get(tf,24)
    min_sl = 1.5*atr if atr else entry_price*0.01
    sl_dist = max(entry_price*sl_pct, min_sl)
    if direction=='LONG':
        sl=entry_price-sl_dist; tp=entry_price+sl_dist*rr
    else:
        sl=entry_price+sl_dist; tp=entry_price-sl_dist*rr
    for i in range(entry_idx+1, min(entry_idx+hold+1, len(bars))):
        b=bars[i]
        if direction=='LONG':
            if b['l']<=sl:
                g=(sl-entry_price)/entry_price
                return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit_reason':'SL'}
            if b['h']>=tp:
                g=(tp-entry_price)/entry_price
                return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit_reason':'TP'}
        else:
            if b['h']>=sl:
                g=(entry_price-sl)/entry_price
                return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit_reason':'SL'}
            if b['l']<=tp:
                g=(entry_price-tp)/entry_price
                return {'is_win':True,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit_reason':'TP'}
    close=bars[min(entry_idx+hold,len(bars)-1)]['c']
    g=((close-entry_price)/entry_price) if direction=='LONG' else ((entry_price-close)/entry_price)
    return {'is_win':g>0,'pnl_gross':g*100,'pnl_net':(g-COST-SLIPPAGE)*100,'exit_reason':'TIME'}

def gen_sigs(bars, rmap, tf, rr=1.5, min_score=90, whitelist=None, start_ts=0):
    atrs=calc_atr(bars,14); rsis=calc_rsi(bars,14)
    min_gap={'15m':4*15*60*1000,'1h':4*3600*1000}.get(tf,4*3600*1000)
    sigs=[]; last_ts=0
    for i in range(100, len(bars)-50):
        b=bars[i]; ts=b['ts']
        if ts-last_ts<min_gap: continue
        if ts<start_ts: continue
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
            sc_val=score_v5_slim(regime,d,rsi,res,bb_w)
            if sc_val<min_score: continue
            ni=i+1
            if ni>=len(bars): continue
            ep=bars[ni]['o']
            er=sim_exit(bars,ni,d,ep,atr,tf,rr=rr)
            sigs.append({'ts':ts,'score':round(sc_val,1),'direction':d,'regime':regime,
                        'tf':tf,'rsi':round(rsi,1) if rsi else None,'bb_w':round(bb_w,3),
                        **er})
            last_ts=ts
    return sigs

def calc_ic(sigs):
    if len(sigs)<10: return 0.0
    s=[x['score'] for x in sigs]; o=[1.0 if x['is_win'] else 0.0 for x in sigs]
    n=len(s); ms=sum(s)/n; mo=sum(o)/n
    cov=sum((s[i]-ms)*(o[i]-mo) for i in range(n))/n
    ss=math.sqrt(sum((x-ms)**2 for x in s)/n); so=math.sqrt(sum((x-mo)**2 for x in o)/n)
    return round(cov/(ss*so),4) if ss and so else 0.0

def run_bt(label, rr=1.5, min_score=90, whitelist=None):
    start_ts=int(datetime.strptime('2022-01-01','%Y-%m-%d').timestamp()*1000)
    all_sigs=[]
    for sym in ['BTC','ETH']:
        SYM=sym.upper()+'USDT'
        for tf in ['15m','1h']:
            bars=load_klines(SYM,tf,start_ts)
            rmap=load_regime_labels(SYM)
            if not bars: continue
            sigs=gen_sigs(bars,rmap,tf,rr=rr,min_score=min_score,whitelist=whitelist,start_ts=start_ts)
            all_sigs.extend(sigs)
    n=len(all_sigs)
    if n==0: print(f"  {label}: ❌ 无信号"); return None
    wr=sum(1 for s in all_sigs if s['is_win'])/n*100
    ic=calc_ic(all_sigs)
    pnl_g=sum(s['pnl_gross'] for s in all_sigs)/n
    pnl_n=sum(s['pnl_net'] for s in all_sigs)/n
    exit_dist=defaultdict(int)
    for s in all_sigs: exit_dist[s['exit_reason']]+=1
    # WF
    ts_min=min(s['ts'] for s in all_sigs); ts_max=max(s['ts'] for s in all_sigs)
    total_m=(ts_max-ts_min)/(86400*30*1000); bpm=n/total_m
    folds=[]
    for f in range(min(9,max(3,int((total_m-24-6)/3)+1)) if total_m>30 else 3):
        te=int((f*3+24)*bpm); ed=int((f*3+30)*bpm)
        fs=all_sigs[te:ed]
        if len(fs)<10: continue
        wr_f=sum(1 for s in fs if s['is_win'])/len(fs)*100
        ev_f=sum(s['pnl_net'] for s in fs)/len(fs)
        folds.append({'f':f+1,'n':len(fs),'wr':round(wr_f,1),'ev':round(ev_f,3)})
    pf=sum(1 for f in folds if f['wr']>=52); ff=sum(1 for f in folds if f['wr']<50)
    stab=pf/len(folds) if folds else 0
    # PnL
    nav=100000;pos=nav*0.05*5;fee=pos*(COST+SLIPPAGE)*2;sl_dist=0.02
    total_pnl=0;cum=0;peak=0;max_dd=0;pnl_list=[]
    for s in all_sigs:
        p=sl_dist*rr*pos-fee if s['is_win'] else -sl_dist*pos-fee
        total_pnl+=p;cum+=p
        if cum>peak: peak=cum
        if peak-cum>max_dd: max_dd=peak-cum
        pnl_list.append(p)
    mean_p=sum(pnl_list)/len(pnl_list) if pnl_list else 0
    std_p=math.sqrt(sum((p-mean_p)**2 for p in pnl_list)/len(pnl_list)) if len(pnl_list)>1 else 0
    tpy=min(n/4.5,5*365); sharpe=mean_p/std_p*math.sqrt(tpy) if std_p>0 else 0
    # 体制分布
    reg_dist=defaultdict(int)
    for s in all_sigs: reg_dist[f"{s['regime']}:{s['direction']}"]+=1
    flag='✅' if pnl_n>0 else '❌'
    print(f"\n  📊 {label}")
    print(f"  {flag} n={n} WR={wr:.1f}% IC={ic:+.4f} gross={pnl_g:+.3f}% net={pnl_n:+.3f}%")
    print(f"  出场: {dict(exit_dist)}")
    print(f"  PnL=${total_pnl:,.0f} Sharpe={sharpe:.2f} DD=${max_dd:,.0f}")
    print(f"  WF: {len(folds)}折 稳定性={stab:.1%} 通过={pf} 失败={ff}")
    for f in folds:
        fl='✅' if f['wr']>=52 else ('⚠️' if f['wr']>=50 else '❌')
        print(f"    {fl} F{f['f']}: n={f['n']:>5} WR={f['wr']:>5.1f}% EV={f['ev']:>+.3f}%")
    print(f"  体制: {dict(reg_dist)}")
    return {'label':label,'n':n,'wr':round(wr,1),'ic':ic,'pnl_gross':round(pnl_g,3),
            'pnl_net':round(pnl_n,3),'sharpe':round(sharpe,2),'max_dd':round(max_dd,0),
            'total_pnl':round(total_pnl,0),'folds':folds,'wf_stab':round(stab,3),
            'wf_pass':pf,'wf_fail':ff,'exit_dist':dict(exit_dist),'regime_dist':dict(reg_dist)}

def main():
    t0=time.time()
    results={}
    
    # 1. 最强组合 only
    print("="*70)
    print("🔥 P2-1: 最强组合 only (BULL_TREND:LONG + BEAR_TREND:SHORT)")
    print("="*70)
    strong={('BULL_TREND','LONG'),('BEAR_TREND','SHORT')}
    results['strong_only_rr15']=run_bt('最强 only RR=1.5',rr=1.5,min_score=90,whitelist=strong)
    
    # 2. 加次强
    print("\n"+"="*70)
    print("🔥 P2-2: +次强 (+BULL_EARLY:LONG + BEAR_RECOVERY:LONG)")
    print("="*70)
    medium=strong|{('BULL_EARLY','LONG'),('BEAR_RECOVERY','LONG')}
    results['with_medium_rr15']=run_bt('强+次强 RR=1.5',rr=1.5,min_score=90,whitelist=medium)
    
    # 3. 最强组合 RR搜索
    print("\n"+"="*70)
    print("🔍 P3-1: RR搜索（最强组合上）")
    print("="*70)
    for rr in [1.2,1.8,2.0]:
        results[f'strong_rr{rr}']=run_bt(f'最强 RR={rr}',rr=rr,min_score=90,whitelist=strong)
    
    # 4. 最强组合 min_score搜索
    print("\n"+"="*70)
    print("🔍 P1-1: min_score搜索（最强组合上）")
    print("="*70)
    for ms in [95,100,105]:
        results[f'strong_ms{ms}']=run_bt(f'最强 ms={ms}',rr=1.5,min_score=ms,whitelist=strong)
    
    elapsed=time.time()-t0
    
    # 汇总
    print("\n"+"="*70)
    print("📊 Phase 1 汇总")
    print("="*70)
    print(f"\n{'配置':<30} {'n':>6} {'WR':>6} {'IC':>8} {'net EV':>8} {'Sharpe':>7} {'WF':>6}")
    print("-"*75)
    for k,r in results.items():
        if r is None: continue
        print(f"{r['label']:<30} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['pnl_net']:>+7.3f}% {r['sharpe']:>7.2f} {r['wf_stab']:>5.1%}")
    
    valid=[r for r in results.values() if r and r['pnl_net']>0]
    if valid:
        best=max(valid,key=lambda x:x['pnl_net'])
        print(f"\n  🟢 最优: {best['label']} net EV={best['pnl_net']:+.3f}%/笔")
        print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f} Sharpe={best['sharpe']:.2f} WF={best['wf_stab']:.1%}")
    else:
        all_r=[r for r in results.values() if r]
        if all_r:
            best=max(all_r,key=lambda x:x['pnl_net'])
            print(f"\n  ⚠️ 仍无正EV，最优: {best['label']} net EV={best['pnl_net']:+.3f}%/笔")
            print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f}")
    
    print(f"\n  耗时: {elapsed:.1f}s")
    
    output={'ts':datetime.utcnow().isoformat()+'Z','results':{k:v for k,v in results.items() if v}}
    with open(OUT_DIR/'dharma_v6_phase1_result.json','w') as f:
        json.dump(output,f,ensure_ascii=False,indent=2,default=str)
    print(f"  结果: data/dharma_v6_phase1_result.json")

if __name__=='__main__':
    main()
