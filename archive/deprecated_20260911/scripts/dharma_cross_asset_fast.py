#!/usr/bin/env python3
"""dharma_cross_asset_fast.py — 跨标的验证精简版
只跑1h TF，从2022-01-01开始，5个标的，BTC regime代理
"""
import gzip, json, math, time, requests
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / "data" / "historical"
OUT  = BASE / "data"
FAPI = "https://fapi.binance.com"

def dl(sym, tf, start_ms):
    bars = []; cur = start_ms; end = int(time.time()*1000)
    while cur < end:
        try:
            r = requests.get(f"{FAPI}/fapi/v1/klines",
                params={"symbol":sym,"interval":tf,"startTime":cur,"endTime":end,"limit":1500}, timeout=10)
            d = r.json()
        except: time.sleep(0.5); continue
        if not d: break
        for k in d: bars.append({"ts":k[0],"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4])})
        cur = d[-1][0]+1; time.sleep(0.05)
        if len(d)<1500: break
    return bars

def save(sym, tf, bars):
    with gzip.open(DATA/f"{sym}_{tf}.jsonl.gz","wt") as f:
        for b in bars: f.write(json.dumps(b)+"\n")

def load(sym, tf):
    f = DATA/f"{sym}_{tf}.jsonl.gz"
    if not f.exists(): return []
    with gzip.open(f,"rt") as fh: return sorted([json.loads(l) for l in fh if l.strip()], key=lambda x:x['ts'])

def load_regime(sym):
    f = DATA/f"{sym}_regime_labels.jsonl.gz"
    if not f.exists(): return {}
    with gzip.open(f,"rt") as fh: return {json.loads(l)['ts']:json.loads(l)['regime'] for l in fh if l.strip()}

def atr(bars, p=14):
    a=[0.0]*len(bars)
    for i in range(p,len(bars)):
        tr=max(bars[i]['h']-bars[i]['l'],abs(bars[i]['h']-bars[i-1]['c']),abs(bars[i]['l']-bars[i-1]['c']))
        a[i]=(a[i-1]*(p-1)+tr)/p if a[i-1] else tr
    return a

def rsi(bars, p=14):
    r=[0.0]*len(bars)
    for i in range(p,len(bars)):
        g=[max(bars[j]['c']-bars[j-1]['c'],0) for j in range(i-p+1,i+1)]
        l=[max(bars[j-1]['c']-bars[j]['c'],0) for j in range(i-p+1,i+1)]
        ag=sum(g)/p; al=sum(l)/p
        r[i]=100-100/(1+ag/al) if al>0 else 100
    return r

def score(reg,d,rsi_v,bb_w,res_s):
    s=50.0
    b={('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
       ('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25}
    s+=b.get((reg,d),0)
    if d=='LONG':
        if rsi_v<30: s+=15
        elif rsi_v>70: s-=15
    else:
        if rsi_v>70: s+=15
        elif rsi_v<30: s-=15
    if bb_w<1.0: s+=12
    elif bb_w>5.0: s-=8
    if res_s>=3: s+=10
    elif res_s==2: s+=5
    elif res_s==0: s-=3
    return max(0,min(150,s))

def sim(bars,idx,d,ep,at,rr=2.0,cost=0.0002,slip=0.0003):
    sp=0.02; hold=24; ms=1.5*at if at else ep*0.01; sd=max(ep*sp,ms)
    if d=='LONG': sl=ep-sd; tp=ep+sd*rr
    else: sl=ep+sd; tp=ep-sd*rr
    for i in range(idx+1,min(idx+hold+1,len(bars))):
        b=bars[i]
        if d=='LONG':
            if b['l']<=sl: g=(sl-ep)/ep; return {'w':g>0,'n':(g-cost-slip)*100}
            if b['h']>=tp: g=(tp-ep)/ep; return {'w':True,'n':(g-cost-slip)*100}
        else:
            if b['h']>=sl: g=(ep-sl)/ep; return {'w':g>0,'n':(g-cost-slip)*100}
            if b['l']<=tp: g=(ep-tp)/ep; return {'w':True,'n':(g-cost-slip)*100}
    c=bars[min(idx+hold,len(bars)-1)]['c']
    g=((c-ep)/ep) if d=='LONG' else ((ep-c)/ep)
    return {'w':g>0,'n':(g-cost-slip)*100}

def main():
    t0=time.time()
    SYMS=['SOLUSDT','BNBUSDT','ADAUSDT','XRPUSDT','DOGEUSDT']
    start=int(datetime(2022,1,1,tzinfo=timezone.utc).timestamp()*1000)
    
    # 下载/加载
    for s in SYMS:
        f=DATA/f"{s}_1h.jsonl.gz"
        if not f.exists() or (f.exists() and len(gzip.open(f,'rt').readlines())<100):
            print(f"⬇️ {s} 1h...",end="",flush=True)
            b=dl(s,'1h',start)
            if b: save(s,'1h',b); print(f" {len(b)} bars")
            else: print(" ❌"); continue
        else:
            print(f"✅ {s} 1h exists")
    
    # BTC regime
    rg=load_regime('BTCUSDT')
    print(f"BTC regime: {len(rg)} labels")
    
    STRONG={('BULL_TREND','LONG'),('BEAR_TREND','SHORT'),('BULL_EARLY','LONG'),('BEAR_RECOVERY','LONG')}
    
    results={}
    for s in SYMS:
        bars=load(s,'1h')
        if len(bars)<200: print(f"❌ {s}: not enough data"); results[s]={'n':0}; continue
        at=atr(bars); rs=rsi(bars)
        sigs=[]; last=0; gap=4*3600*1000
        for i in range(100,len(bars)-50):
            ts=bars[i]['ts']
            if ts-last<gap: continue
            t4=(ts//(4*3600*1000))*(4*3600*1000)-4*3600*1000
            reg=rg.get(t4)
            if not reg:
                for off in range(-3,4):
                    reg=rg.get(t4+off*4*3600*1000)
                    if reg: break
            if not reg: continue
            a=at[i]; r=rs[i]
            if not a or not r: continue
            cl=[bars[j]['c'] for j in range(max(0,i-20),i)]
            mc=sum(cl)/len(cl) if cl else 0
            sc=math.sqrt(sum((c-mc)**2 for c in cl)/len(cl)) if cl else 0
            bw=sc*2/mc*100 if mc else 0
            # 简化共振
            res_s=2  # 基础分
            for d in ['LONG','SHORT']:
                if (reg,d) not in STRONG: continue
                sv=score(reg,d,r,bw,res_s)
                if sv<110: continue
                ni=i+1
                if ni>=len(bars): continue
                ep=bars[ni]['o']
                er=sim(bars,ni,d,ep,a)
                sigs.append({'s':round(sv,1),'w':er['w'],'n':er['n'],'d':d})
                last=ts
        n=len(sigs)
        if n==0: print(f"❌ {s}: 0 signals"); results[s]={'n':0}; continue
        wr=sum(1 for x in sigs if x['w'])/n*100
        ev=sum(x['n'] for x in sigs)/n
        # IC
        ss=[x['s'] for x in sigs]; oo=[1.0 if x['w'] else 0.0 for x in sigs]
        nn=len(ss); ms=sum(ss)/nn; mo=sum(oo)/nn
        cov=sum((ss[i]-ms)*(oo[i]-mo) for i in range(nn))/nn
        sds=math.sqrt(sum((x-ms)**2 for x in ss)/nn); sdo=math.sqrt(sum((x-mo)**2 for x in oo)/nn)
        ic=round(cov/(sds*sdo),4) if sds and sdo else 0
        
        flag='✅' if wr>=52 else ('⚠️' if wr>=50 else '❌')
        print(f"{flag} {s}: n={n} WR={wr:.1f}% IC={ic:+.4f} EV={ev:+.3f}%")
        results[s]={'n':n,'wr':round(wr,1),'ic':ic,'ev':round(ev,3)}
    
    print("\n"+"="*60)
    print(f"{'Symbol':<12} {'n':>6} {'WR':>6} {'IC':>8} {'EV':>8} {'Pass':>4}")
    print("-"*50)
    pc=0; vc=0
    for s in SYMS:
        r=results.get(s,{})
        if r.get('n',0)>0:
            vc+=1
            f='✅' if r['wr']>=52 else '❌'
            if r['wr']>=52: pc+=1
            print(f"{s:<12} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['ev']:>+7.3f}% {f:>4}")
        else:
            print(f"{s:<12} {'N/A':>6}")
    print(f"\n通过: {pc}/{vc} (标准≥3/5)")
    if pc>=3: print("🟢 alpha普适！")
    elif pc>=1: print("⚠️ alpha部分普适")
    else: print("🔴 alpha可能局部")
    
    with open(OUT/'dharma_cross_asset_result.json','w') as f:
        json.dump({'ts':datetime.utcnow().isoformat()+'Z','pass':pc,'total':vc,'results':results},f,indent=2)
    print(f"\n耗时: {time.time()-t0:.1f}s")

if __name__=='__main__':
    main()
