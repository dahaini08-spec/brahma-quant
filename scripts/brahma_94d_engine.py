#!/usr/bin/env python3
"""
brahma_94d_engine.py v2 — 94维12步骤强制分析引擎
[封印 2026-10-30 苏摩111]

改进：
  K线足量：15M×200 / 1H×200 / 4H×100 / 1D×100 / 1W×52
  12步骤全部显式输出，推理链完全透明
  多空双向评分，不单边看盘
  高频预判：5M Taker/OI实时触发
"""
import json, math, urllib.request, time, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).resolve().parent.parent

def _fapi(path, **p):
    url = 'https://fapi.binance.com' + path
    if p: url += '?' + '&'.join('%s=%s'%(k,v) for k,v in p.items())
    return json.loads(urllib.request.urlopen(url, timeout=6).read())

# ─── 技术计算 ────────────────────────────────────────────
def _cl(kl): return [float(k[4]) for k in kl]
def _hi(kl): return [float(k[2]) for k in kl]
def _lo(kl): return [float(k[3]) for k in kl]
def _vo(kl): return [float(k[5]) for k in kl]

def _rsi(kl, n=14):
    c=_cl(kl)
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))]
    l=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
    if len(g)<n: return 50.0
    ag=sum(g[-n:])/n; al=sum(l[-n:])/n
    return round(100-100/(1+ag/al),1) if al else 100.0

def _atr(kl, n=14):
    trs=[]
    for i in range(1,len(kl)):
        h=float(kl[i][2]); l=float(kl[i][3]); pc=float(kl[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return round(sum(trs[-n:])/n, 2) if trs else 0.0

def _bb(kl, n=20):
    c=_cl(kl)
    if len(c)<n: return 0.0,0.0,0.0,0.0
    seg=c[-n:]; m=sum(seg)/n
    s=math.sqrt(sum((x-m)**2 for x in seg)/n)
    return round(m,2), round(m+2*s,2), round(m-2*s,2), round(s*2/m*100,3)

def _ema(kl, n):
    c=_cl(kl)
    if len(c)<n: return c[-1] if c else 0.0
    k2=2/(n+1); e=sum(c[:n])/n
    for x in c[n:]: e=x*k2+e*(1-k2)
    return round(e,2)

def _stoch_rsi(kl, n=14):
    c=_cl(kl)
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))]
    l=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
    rv=[]
    for i in range(n,len(g)):
        ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n
        rv.append(100-100/(1+ag/al) if al else 100.0)
    if len(rv)<n: return 50.0
    mn=min(rv[-n:]); mx=max(rv[-n:])
    return round((rv[-1]-mn)/(mx-mn)*100,1) if mx!=mn else 50.0

def _macd(kl, fast=12, slow=26, sig=9):
    c=_cl(kl)
    if len(c)<slow+sig: return 0.0,0.0,0.0
    def ema_arr(arr, n):
        k2=2/(n+1); e=sum(arr[:n])/n; res=[e]
        for x in arr[n:]: e=x*k2+e*(1-k2); res.append(e)
        return res
    e12=ema_arr(c,fast); e26=ema_arr(c,slow)
    diff=[e12[i+slow-fast]-e26[i] for i in range(len(e26))]
    sarr=ema_arr(diff,sig)
    return round(diff[-1],2), round(sarr[-1],2), round(diff[-1]-sarr[-1],2)

def _vwap(kl, n=20):
    seg=kl[-n:]
    tp=[(float(k[2])+float(k[3])+float(k[4]))/3 for k in seg]
    vol=[float(k[5]) for k in seg]
    return round(sum(t*v for t,v in zip(tp,vol))/sum(vol),2) if sum(vol) else 0.0

def _vol_ratio(kl, n=5, base=20):
    v=_vo(kl)
    if len(v)<base: return 1.0
    ar=sum(v[-n:])/n; ab=sum(v[-base:-n])/(base-n) if base>n else 1
    return round(ar/ab,2) if ab else 1.0

def _structure(kl, n=3):
    h=_hi(kl); lo=_lo(kl)
    sh=[h[i] for i in range(n,len(h)-n)
        if all(h[i]>=h[j] for j in range(i-n,i+n+1) if j!=i)]
    sl=[lo[i] for i in range(n,len(lo)-n)
        if all(lo[i]<=lo[j] for j in range(i-n,i+n+1) if j!=i)]
    if len(sh)>=2 and len(sl)>=2:
        if sh[-1]>sh[-2] and sl[-1]>sl[-2]: return 'HH+HL↑牛市',sh[-2],sh[-1],sl[-2],sl[-1]
        if sh[-1]<sh[-2] and sl[-1]<sl[-2]: return 'LH+LL↓熊市',sh[-2],sh[-1],sl[-2],sl[-1]
        if sh[-1]>sh[-2]: return 'HH+LL扩张',sh[-2],sh[-1],sl[-2],sl[-1]
        return 'LH+HL收缩',sh[-2],sh[-1],sl[-2],sl[-1]
    return '?',0,0,0,0

def _fvg(kl, px, max_age=50):
    b=[(float(k[1]),float(k[2]),float(k[3]),float(k[4])) for k in kl]
    bulls=[]; bears=[]
    for i in range(2,len(b)):
        age=len(b)-1-i
        if age>max_age: continue
        if b[i][2]>b[i-2][1]:
            lo=round(b[i-2][1],2); hi=round(b[i][2],2)
            filled=any(float(k[3])<=lo for k in kl[i:])
            if not filled and px<hi:
                bulls.append({'lo':lo,'hi':hi,'mid':round((lo+hi)/2,2),'age':age})
        if b[i][1]<b[i-2][2]:
            lo=round(b[i][1],2); hi=round(b[i-2][2],2)
            filled=any(float(k[2])>=hi for k in kl[i:])
            if not filled and px>lo:
                bears.append({'lo':lo,'hi':hi,'mid':round((lo+hi)/2,2),'age':age})
    bull=sorted(bulls,key=lambda x:x['age'])[-1] if bulls else None
    bear=sorted(bears,key=lambda x:x['age'])[-1] if bears else None
    return bull, bear

def _ob(kl, px, lookback=30):
    obs=[]; seg=kl[-lookback:]
    for i in range(1,len(seg)-1):
        o2=float(seg[i][1]); c2=float(seg[i][4])
        po=float(seg[i-1][1]); pc=float(seg[i-1][4])
        size=abs(c2-o2); avg=sum(abs(float(k[4])-float(k[1])) for k in seg)/len(seg)
        if size<avg*1.5: continue
        if c2<o2 and pc>po:
            lo2=min(po,pc); hi2=max(po,pc); age=len(seg)-1-i
            if not any(float(k[3])<lo2 for k in seg[i+1:]):
                obs.append({'type':'bull','lo':round(lo2,2),'hi':round(hi2,2),'age':age})
        if c2>o2 and pc<po:
            lo2=min(po,pc); hi2=max(po,pc); age=len(seg)-1-i
            if not any(float(k[2])>hi2 for k in seg[i+1:]):
                obs.append({'type':'bear','lo':round(lo2,2),'hi':round(hi2,2),'age':age})
    bull_obs=[x for x in obs if x['type']=='bull']
    bear_obs=[x for x in obs if x['type']=='bear']
    return (sorted(bull_obs,key=lambda x:abs(px-x['lo']))[:1],
            sorted(bear_obs,key=lambda x:abs(px-x['hi']))[:1])

def _oi_chg(ov):
    if not ov: return 0.0
    v=[float(o['sumOpenInterest']) for o in ov]
    return round((v[-1]-v[0])/v[0]*100,3) if v[0] else 0.0

def _oi_seq(ov, n=6):
    return [round(float(o['sumOpenInterest'])/1000,1) for o in (ov or [])[-n:]]


# ─── 主分析 ─────────────────────────────────────────────
def analyze(sym):
    sym=sym.upper()
    tasks={
        'px':   lambda:_fapi('/fapi/v1/ticker/price',symbol=sym+'USDT'),
        'fr':   lambda:_fapi('/fapi/v1/premiumIndex',symbol=sym+'USDT'),
        'stat': lambda:_fapi('/fapi/v1/ticker/24hr',symbol=sym+'USDT'),
        'oi15': lambda:_fapi('/futures/data/openInterestHist',symbol=sym+'USDT',period='15m',limit=12),
        'oi1h': lambda:_fapi('/futures/data/openInterestHist',symbol=sym+'USDT',period='1h',limit=12),
        'oi4h': lambda:_fapi('/futures/data/openInterestHist',symbol=sym+'USDT',period='4h',limit=8),
        'lsr':  lambda:_fapi('/futures/data/globalLongShortAccountRatio',symbol=sym+'USDT',period='5m',limit=4),
        'blsr': lambda:_fapi('/futures/data/topLongShortAccountRatio',symbol=sym+'USDT',period='1h',limit=6),
        'plsr': lambda:_fapi('/futures/data/topLongShortPositionRatio',symbol=sym+'USDT',period='1h',limit=4),
        'tk':   lambda:_fapi('/futures/data/takerlongshortRatio',symbol=sym+'USDT',period='5m',limit=12),
        'k5':   lambda:_fapi('/fapi/v1/klines',symbol=sym+'USDT',interval='5m',limit=100),
        'k15':  lambda:_fapi('/fapi/v1/klines',symbol=sym+'USDT',interval='15m',limit=200),
        'k1h':  lambda:_fapi('/fapi/v1/klines',symbol=sym+'USDT',interval='1h',limit=200),
        'k4h':  lambda:_fapi('/fapi/v1/klines',symbol=sym+'USDT',interval='4h',limit=100),
        'k1d':  lambda:_fapi('/fapi/v1/klines',symbol=sym+'USDT',interval='1d',limit=100),
        'k1w':  lambda:_fapi('/fapi/v1/klines',symbol=sym+'USDT',interval='1w',limit=52),
    }
    R={}
    with ThreadPoolExecutor(max_workers=14) as pool:
        futs={pool.submit(fn):k for k,fn in tasks.items()}
        for f in as_completed(futs):
            k=futs[f]
            try: R[k]=f.result()
            except: R[k]=None

    px    = float(R['px']['price'])           if R['px']   else 0.0
    fr    = float(R['fr']['lastFundingRate'])*100 if R['fr'] else 0.0
    idx_px= float(R['fr']['indexPrice'])      if R['fr']   else px
    mark_px=float(R['fr']['markPrice'])       if R['fr']   else px
    basis = round((mark_px-idx_px)/idx_px*100,4) if idx_px else 0.0
    chg24 = float(R['stat']['priceChangePercent']) if R['stat'] else 0.0
    hi24  = float(R['stat']['highPrice'])     if R['stat'] else px
    lo24  = float(R['stat']['lowPrice'])      if R['stat'] else px
    vol24 = float(R['stat']['quoteVolume'])/1e9 if R['stat'] else 0.0

    k5=R['k5']; k15=R['k15']; k1h=R['k1h']; k4h=R['k4h']; k1d=R['k1d']; k1w=R['k1w']
    nk = {'5M':len(k5 or []),'15M':len(k15 or []),'1H':len(k1h or []),
           '4H':len(k4h or []),'1D':len(k1d or []),'1W':len(k1w or [])}

    # Step1: RSI全周期
    r5m=_rsi(k5) if k5 else 50; r15=_rsi(k15) if k15 else 50
    r1h=_rsi(k1h) if k1h else 50; r4h=_rsi(k4h) if k4h else 50
    r1d=_rsi(k1d) if k1d else 50; r1w=_rsi(k1w) if k1w else 50
    st5=_stoch_rsi(k5) if k5 else 50; st15=_stoch_rsi(k15) if k15 else 50
    st1h=_stoch_rsi(k1h) if k1h else 50; st4h=_stoch_rsi(k4h) if k4h else 50
    rsi_bull=sum([r1h<35,r4h<40,r1d<35,r1w<45,st1h<20])
    rsi_bear=sum([r1h>65,r4h>60,r1d>65,r1w>55,st1h>80])

    # Step2: 布林带
    bb15m_m,bb15m_u,bb15m_l,bbw15=_bb(k15) if k15 else (px,)*3+(0,)
    bb1h_m,bb1h_u,bb1h_l,bbw1h=_bb(k1h) if k1h else (px,)*3+(0,)
    bb4h_m,bb4h_u,bb4h_l,bbw4h=_bb(k4h) if k4h else (px,)*3+(0,)
    bb1d_m,bb1d_u,bb1d_l,bbw1d=_bb(k1d) if k1d else (px,)*3+(0,)
    squeeze=bbw1h<2.0 or bbw4h<2.5
    pos1h='上轨' if px>bb1h_u else ('下轨' if px<bb1h_l else '中段')
    pos4h='上轨' if px>bb4h_u else ('下轨' if px<bb4h_l else '中段')

    # Step3: EMA + VWAP
    ema7_1h=_ema(k1h,7) if k1h else px
    ema20_1h=_ema(k1h,20) if k1h else px
    ema50_4h=_ema(k4h,50) if k4h else px
    ema100_4h=_ema(k4h,100) if k4h else px
    ema200_1d=_ema(k1d,200) if k1d and len(k1d)>=200 else (_ema(k1d,len(k1d)) if k1d else px)
    vwap1h=_vwap(k1h,20) if k1h else px
    vwap4h=_vwap(k4h,20) if k4h else px
    ema_bull=sum([px>ema7_1h,px>ema20_1h,px>ema50_4h,px>ema200_1d])
    ema_bear=4-ema_bull

    # Step4: MACD
    macd1h_d,macd1h_s,macd1h_h=_macd(k1h) if k1h else (0,0,0)
    macd4h_d,macd4h_s,macd4h_h=_macd(k4h) if k4h else (0,0,0)
    macd1d_d,macd1d_s,macd1d_h=_macd(k1d) if k1d else (0,0,0)

    # Step5: ATR + 成交量
    atr5m=_atr(k5) if k5 else 0; atr15=_atr(k15) if k15 else 0
    atr1h=_atr(k1h) if k1h else 0; atr4h=_atr(k4h) if k4h else 0
    atr1d=_atr(k1d) if k1d else 0
    vr1h=_vol_ratio(k1h) if k1h else 1.0
    vr4h=_vol_ratio(k4h) if k4h else 1.0
    sl_1h=max(atr1h*1.5, px*0.012)
    sl_4h=max(atr4h*1.5, px*0.015)

    # Step6: SMC结构全周期
    st1h_s,sh1h_p,sh1h_c,sl1h_p,sl1h_c=_structure(k1h) if k1h else ('?',0,0,0,0)
    st4h_s,sh4h_p,sh4h_c,sl4h_p,sl4h_c=_structure(k4h) if k4h else ('?',0,0,0,0)
    st1d_s,sh1d_p,sh1d_c,sl1d_p,sl1d_c=_structure(k1d) if k1d else ('?',0,0,0,0)
    st1w_s,sh1w_p,sh1w_c,sl1w_p,sl1w_c=_structure(k1w) if k1w else ('?',0,0,0,0)
    struct_bull=sum(['↑' in x for x in [st1h_s,st4h_s,st1d_s,st1w_s]])
    struct_bear=sum(['↓' in x for x in [st1h_s,st4h_s,st1d_s,st1w_s]])

    # Step7: FVG + OB
    fb1h,fbe1h=_fvg(k1h,px) if k1h else (None,None)
    fb4h,fbe4h=_fvg(k4h,px) if k4h else (None,None)
    fb1d,fbe1d=_fvg(k1d,px) if k1d else (None,None)
    ob_b1h,ob_s1h=_ob(k1h,px) if k1h else ([],[])
    ob_b4h,ob_s4h=_ob(k4h,px) if k4h else ([],[])
    fvg_bull=sum([fb1h is not None,fb4h is not None,fb1d is not None])
    fvg_bear=sum([fbe1h is not None,fbe4h is not None,fbe1d is not None])

    # Step8: OI
    oi15c=_oi_chg(R['oi15']); oi1hc=_oi_chg(R['oi1h']); oi4hc=_oi_chg(R['oi4h'])
    oi_seq=_oi_seq(R['oi1h'],6)
    if oi4hc<-1.5: oi_sig='SHORT_SQUEEZE🔥'; oi_note='空头大量减仓=反弹动力'
    elif oi15c>0.15 and oi1hc>0.25: oi_sig='BUILD多'; oi_note='多头主动建仓'
    elif oi1hc<-0.3: oi_sig='UNWIND多'; oi_note='多头撤退'
    elif oi15c<-0.1: oi_sig='BUILD空'; oi_note='空头短线建仓'
    else: oi_sig='中性'; oi_note='无明显方向'
    oi_bull=sum([oi4hc<-1.5, oi15c>0.1, fr<-0.002])
    oi_bear=sum([oi4hc>1.0, oi15c<-0.1, fr>0.01])

    # Step9: LSR + Taker
    lsr_d=R['lsr'] or []; blsr_d=R['blsr'] or []; plsr_d=R['plsr'] or []; tk_d=R['tk'] or []
    lp=float(lsr_d[-1]['longAccount'])*100 if lsr_d else 50.0
    bl=[round(float(o['longAccount'])*100,1) for o in blsr_d] if blsr_d else [50.0]
    bp=[round(float(o['longAccount'])*100,1) for o in plsr_d] if plsr_d else [50.0]
    tkv=[round(float(t['buySellRatio']),3) for t in tk_d] if tk_d else []
    bl_chg=round(bl[-1]-bl[0],1) if len(bl)>=2 else 0.0
    bp_chg=round(bp[-1]-bp[0],1) if len(bp)>=2 else 0.0
    tk_rec=tkv[-4:] if len(tkv)>=4 else tkv
    tk_bull=sum(1 for t in tk_rec if t>1.0)
    tk_bear=sum(1 for t in tk_rec if t<1.0)
    tk_trend='↑↑买方连续' if tk_bull>=3 else ('↓↓卖方连续' if tk_bear>=3 else '↕混乱')
    taker_confirm=tk_bull>=2 and (tk_rec[-1]>1.0 if tk_rec else False)
    cvd1h=0.0
    try: cvd1h=float(json.loads((BASE/'data'/('cvd_realtime_%susdt.json'%sym.lower())).read_text()).get('cvd_1h',0) or 0)
    except: pass
    lsr_bull=sum([lp<45,bl[-1]>60,bl_chg>1,bp_chg>1])
    lsr_bear=sum([lp>70,bl[-1]<45,bl_chg<-1])

    # Step10: 宏观+方仓+GEX
    bs={}
    try: bs=json.loads((BASE/'data'/('brahma_state_%s.json'%sym.lower())).read_text())
    except: pass
    fc=bs.get('fc_signal','?'); fcsim=float(bs.get('fc_sim',0) or 0)
    regime=bs.get('regime','CHOP_MID'); score=float(bs.get('score_final',0) or 0)
    gzf=float(bs.get('gex_zf',0) or 0)
    macro={}
    try: macro=json.loads((BASE/'data'/'macro_real.json').read_text())
    except: pass
    fed=float(macro.get('fed_rate',5.5) or 5.5)
    macro_age=round((time.time()-float(macro.get('ts',0) or 0))/3600,1)
    liq_s=liq_l=0.0
    try:
        ld=json.loads((BASE/'data'/('liq_heatmap_%susdt.json'%sym.lower())).read_text())
        cur=float(ld.get('current_px') or px)
        sw=sorted([w['price'] for w in ld.get('sell_walls',[]) if w['price']>cur])
        bw=sorted([w['price'] for w in ld.get('buy_walls',[]) if w['price']<cur],reverse=True)
        liq_s=sw[0] if sw else float(bs.get('liq_short',0) or 0)
        liq_l=bw[0] if bw else float(bs.get('liq_long',0) or 0)
    except: pass

    # Step11: 多空综合评分（双向）
    bull=rsi_bull*2+struct_bull*3+fvg_bull*1+oi_bull*2+lsr_bull*1+ema_bull*1+(3 if fc=='LONG' else 0)+(2 if taker_confirm else 0)
    bear=rsi_bear*2+struct_bear*3+fvg_bear*1+oi_bear*2+lsr_bear*1+ema_bear*1+(3 if fc=='SHORT' else 0)+(2 if not taker_confirm and tk_bear>=2 else 0)
    total=bull+bear
    bull_pct=round(bull/total*100) if total else 50
    bear_pct=100-bull_pct
    bias=('强多头' if bull>bear*1.5 else '强空头' if bear>bull*1.5 else '偏多' if bull>bear else '偏空' if bear>bull else '中性')

    # Step12: 三周期裁决
    min_sl1h=max(atr1h*1.5,px*0.012); min_sl4h=max(atr4h*1.5,px*0.015)
    s_dir='WAIT'; s_e=s_sl=s_t1=s_t2=0.0; s_trig=''; s_logic=''
    is_ov=st1h<20 and r1h<35; is_ob=st1h>80 and r1h>65
    is_sq=oi4hc<-1.5; is_frn=fr<-0.002
    if (is_ov or is_sq or is_frn) and bull>=bear:
        s_dir='LONG'
        anchor=fbe1h['lo'] if fbe1h else (ob_b1h[0]['hi'] if ob_b1h else bb1h_l)
        s_e=round(max(anchor,px*0.999),2); s_sl=round(s_e-min_sl1h,2)
        # [修复 2026-10-30] TP1取 max(FVG中点, BB1H中轨, 入场价+0.5×SL距离)，确保RR有意义
        _fvg_tp=fb1h['mid'] if fb1h and fb1h['mid']>s_e*1.003 else 0
        _bb_tp=bb1h_m if bb1h_m>s_e*1.003 else 0
        _min_tp=round(s_e+(s_e-s_sl)*0.8,2)  # 至少0.8×SL距离
        s_t1=round(max(_fvg_tp,_bb_tp,_min_tp,liq_s*0.993 if liq_s and liq_s>s_e else 0),2)
        # [修复] TP2必须>TP1，且取更高目标
        _tp2_cands=[liq_s*0.993 if liq_s and liq_s>s_t1*1.002 else 0,
                    bb1h_u*0.99 if bb1h_u>s_t1*1.002 else 0,
                    s_t1+(s_t1-s_e)*0.5]  # TP2=TP1+0.5倍TP1距离
        s_t2=round(max(c for c in _tp2_cands if c>s_t1),2)
        r=[];
        if is_ov: r.append('StochRSI1H=%s底部'%st1h)
        if is_sq: r.append('4H SHORT_SQUEEZE(%+.2f%%)'%oi4hc)
        if is_frn: r.append('FR=%+.4f%%负值'%fr)
        s_logic='+'.join(r); s_trig='15M Taker连续2根>1.0+5M收阳'
    elif is_ob and bear>bull:
        s_dir='SHORT'
        anchor=fbe1h['hi'] if fbe1h else (ob_s1h[0]['lo'] if ob_s1h else bb1h_u)
        s_e=round(min(anchor,liq_s if liq_s else px*1.002),2)
        s_sl=round(s_e+min_sl1h,2); s_t1=round(bb1h_m,2); s_t2=round(bb1h_l*1.01,2)
        s_logic='StochRSI=%s超买+Taker=%s'%(st1h,tk_trend); s_trig='15M Taker连续2根<1.0'
    # [修复 2026-10-30] WAIT时补充明确原因（is_ov/is_sq/is_frn均在此之前定义）
    if s_dir=='WAIT' and not s_trig:
        _wr=[]
        if not is_ov: _wr.append('StochRSI1H=%.1f(需<20)'%st1h)
        if not is_sq: _wr.append('4H OI=%.3f%%无SQUEEZE(需<-1.5%%)'%oi4hc)
        if not (bull>=bear): _wr.append('多头%d<空头%d'%(bull,bear))
        s_trig='、'.join(_wr) if _wr else '信号未触发'; s_logic='WAIT: '+s_trig
    s_rr=round(abs(s_t1-s_e)/abs(s_e-s_sl),1) if s_sl and s_e and abs(s_e-s_sl)>0 else 0
    # [修复 2026-10-30] RR<1.0时短线信号降级：找更好的入场点
    if s_rr < 1.0 and s_dir!='WAIT':
        s_dir='WAIT'; s_e=s_sl=s_t1=s_t2=0.0
        s_trig='RR=%.1f<1.0，等更好入场点（当前StochRSI1H=%.1f，需<20或>80）'%(s_rr,st1h)
        s_logic='RR不达标（最小要求1.0）'

    m_dir='WAIT'; m_e=m_sl=m_t1=m_t2=0.0; m_logic=m_note=''
    if '↑' in st4h_s:
        m_dir='LONG'; m_e=round(bb4h_l*1.001,2)
        # [修复 2026-10-30] 多单SL必须在入场价下方
        _sl_ref=sl4h_c*0.99 if sl4h_c and sl4h_c<m_e else m_e*0.985
        m_sl=round(min(m_e-min_sl4h, _sl_ref),2)
        m_t1=round(bb4h_m,2); m_t2=round(sh4h_c*0.98 if sh4h_c else bb4h_u*0.98,2)
        m_logic='4H %s  fc=%s(%.2f)  EMA50_4H=$%s'%(st4h_s,fc,fcsim,'{:,.0f}'.format(ema50_4h))
        m_note='跌破BB4H下轨$%s=结构破坏→转观望'%'{:,.0f}'.format(bb4h_l)
    elif '↓' in st4h_s:
        m_dir='SHORT'; m_e=round(bb4h_u*0.999,2)
        # [修复 2026-10-30] 空单SL必须在入场价上方且足够远
        _sl_ref=sh4h_c*1.01 if sh4h_c and sh4h_c>m_e else m_e*1.015
        m_sl=round(max(m_e+min_sl4h, _sl_ref),2)
        m_t1=round(bb4h_m,2); m_t2=round(sl4h_c*1.02 if sl4h_c else bb4h_l*1.02,2)
        m_logic='4H %s  OI=%s  EMA50_4H=$%s'%(st4h_s,oi_sig,'{:,.0f}'.format(ema50_4h))
        m_note='突破BB4H上轨$%s→止损'%'{:,.0f}'.format(bb4h_u)
    m_rr=round(abs(m_t1-m_e)/abs(m_e-m_sl),1) if m_sl and m_e and abs(m_e-m_sl)>0 else 0

    lg_dir='WAIT'; lg_elo=lg_ehi=lg_sl=lg_tp=0.0; lg_logic=''
    if r1d<32 and r1w<45:
        lg_dir='LONG'; lg_elo=round(bb1d_l*0.97,2); lg_ehi=round(bb1d_l*1.02,2)
        lg_sl=round(lg_elo*0.94,2); lg_tp=round(sh1d_c*0.96 if sh1d_c else px*1.18,2)
        lg_logic='RSI1D=%s RSI1W=%s 历史超卖→中长线价值区 周期2-4周'%(r1d,r1w)
    elif r1d>70 and r1w>65:
        lg_dir='SHORT'; lg_elo=round(bb1d_u*0.99,2); lg_ehi=round(bb1d_u*1.01,2)
        lg_sl=round(lg_ehi*1.05,2); lg_tp=round(sl1d_c*1.03 if sl1d_c else px*0.84,2)
        lg_logic='RSI1D=%s RSI1W=%s 周线超买 周期2-4周'%(r1d,r1w)

    return {
        'sym':sym,'px':px,'ts':time.time(),
        'ts_str':time.strftime('%H:%M UTC %Y-%m-%d',time.gmtime()),
        'nk':nk,
        'meta':{'fr':fr,'basis':basis,'chg24':chg24,'hi24':hi24,'lo24':lo24,
                'vol24B':round(vol24,1),'mark_px':mark_px,'idx_px':idx_px,
                'regime':regime,'score':score,'fc':fc,'fc_sim':fcsim,
                'gex_zf':gzf,'fed':fed,'macro_age':macro_age},
        'steps':{
            's1':{'5m':r5m,'15m':r15,'1h':r1h,'4h':r4h,'1d':r1d,'1w':r1w,
                  'st':{'5m':st5,'15m':st15,'1h':st1h,'4h':st4h},
                  'bull':rsi_bull,'bear':rsi_bear},
            's2':{'15m':{'m':bb15m_m,'u':bb15m_u,'l':bb15m_l,'w':bbw15},
                  '1h':{'m':bb1h_m,'u':bb1h_u,'l':bb1h_l,'w':bbw1h},
                  '4h':{'m':bb4h_m,'u':bb4h_u,'l':bb4h_l,'w':bbw4h},
                  '1d':{'m':bb1d_m,'u':bb1d_u,'l':bb1d_l,'w':bbw1d},
                  'sq':squeeze,'p1h':pos1h,'p4h':pos4h},
            's3':{'e7_1h':ema7_1h,'e20_1h':ema20_1h,'e50_4h':ema50_4h,
                  'e100_4h':ema100_4h,'e200_1d':ema200_1d,
                  'vwap_1h':vwap1h,'vwap_4h':vwap4h,'bull':ema_bull,'bear':ema_bear},
            's4':{'1h':{'d':macd1h_d,'s':macd1h_s,'h':macd1h_h},
                  '4h':{'d':macd4h_d,'s':macd4h_s,'h':macd4h_h},
                  '1d':{'d':macd1d_d,'s':macd1d_s,'h':macd1d_h}},
            's5':{'5m':atr5m,'15m':atr15,'1h':atr1h,'4h':atr4h,'1d':atr1d,
                  'vr_1h':vr1h,'vr_4h':vr4h,'sl_1h':round(sl_1h,1),'sl_4h':round(sl_4h,1)},
            's6':{'1h':st1h_s,'4h':st4h_s,'1d':st1d_s,'1w':st1w_s,
                  'hi':{'1h':sh1h_c,'4h':sh4h_c,'1d':sh1d_c,'1w':sh1w_c},
                  'lo':{'1h':sl1h_c,'4h':sl4h_c,'1d':sl1d_c,'1w':sl1w_c},
                  'bull':struct_bull,'bear':struct_bear},
            's7':{'fvg':{'bull_1h':fb1h,'bear_1h':fbe1h,'bull_4h':fb4h,'bear_4h':fbe4h,'bull_1d':fb1d,'bear_1d':fbe1d},
                  'ob':{'bull_1h':ob_b1h[:1],'bear_1h':ob_s1h[:1],'bull_4h':ob_b4h[:1],'bear_4h':ob_s4h[:1]},
                  'bull':fvg_bull,'bear':fvg_bear},
            's8':{'15m':oi15c,'1h':oi1hc,'4h':oi4hc,'seq':oi_seq,
                  'sig':oi_sig,'note':oi_note,'bull':oi_bull,'bear':oi_bear},
            's9':{'retail':round(lp,1),'big':bl,'pos':bp,'bl_chg':bl_chg,'bp_chg':bp_chg,
                  'tk':tkv[-6:],'tk_trend':tk_trend,'tk_confirm':taker_confirm,
                  'cvd_1h':cvd1h,'bull':lsr_bull,'bear':lsr_bear},
            's10':{'fr':fr,'basis':basis,'regime':regime,'score':score,'fc':fc,'fc_sim':fcsim,
                   'gzf':gzf,'liq_s':liq_s,'liq_l':liq_l,'fed':fed,'macro_age':macro_age},
            's11':{'bull':bull,'bear':bear,'total':total,'bull_pct':bull_pct,'bear_pct':bear_pct,'bias':bias},
            's12':{'short_dir':s_dir,'short_trigger':s_trig,'short_logic':s_logic,'short_rr':s_rr,
                   'mid_dir':m_dir,'mid_logic':m_logic,
                   'long_dir':lg_dir,'long_logic':lg_logic},
        },
        'short':{'dir':s_dir,'entry':s_e,'sl':s_sl,'tp1':s_t1,'tp2':s_t2,
                 'trigger':s_trig,'logic':s_logic,'rr':s_rr,'period':'1H+15M · 1-3天'},
        'mid':{'dir':m_dir,'entry':m_e,'sl':m_sl,'tp1':m_t1,'tp2':m_t2,
               'logic':m_logic,'note':m_note,'rr':m_rr,'period':'4H · 1-2周'},
        'long':{'dir':lg_dir,'elo':lg_elo,'ehi':lg_ehi,'sl':lg_sl,'tp':lg_tp,
                'logic':lg_logic,'period':'1D+1W · 2-4周'},
    }


def format_report(r):
    sym=r['sym']; px=r['px']; m=r['meta']; st=r['steps']
    s1=st['s1']; s2=st['s2']; s3=st['s3']; s4=st['s4']; s5=st['s5']
    s6=st['s6']; s7=st['s7']; s8=st['s8']; s9=st['s9']; s10=st['s10']; s11=st['s11']
    sh=r['short']; mid=r['mid']; lng=r['long']
    nk=r['nk']

    def fvg_s(f):
        if not f: return '无'
        return '$%s(%+.1f%% age=%d根)'%('{:,.1f}'.format(f['mid']),(f['mid']-px)/px*100,f['age'])
    def ob_s(lst):
        if not lst: return '无'
        o=lst[0]; return '$%s~$%s(age=%d根)'%('{:,.1f}'.format(o['lo']),'{:,.1f}'.format(o['hi']),o['age'])

    liq_s=s10['liq_s']; liq_l=s10['liq_l']
    liq_str=''
    if liq_s: liq_str+='上墙$%s(+%.1f%%)  '%('{:,.0f}'.format(liq_s),(liq_s-px)/px*100)
    if liq_l: liq_str+='下池$%s(%.1f%%)'%('{:,.0f}'.format(liq_l),(liq_l-px)/px*100)
    if not liq_str: liq_str='暂无数据'

    out=[]
    out.append('')
    out.append('╔════════════════════════════════════════════════════════════╗')
    out.append('║  %s/USDT  94维12步骤  %s'%(sym,r['ts_str']))
    out.append('║  K线：5M×100 / 15M×200 / 1H×200 / 4H×100 / 1D×100 / 1W×52')
    out.append('╚════════════════════════════════════════════════════════════╝')
    out.append('')
    out.append('▌ D0 宏观背景')
    out.append('  $%s  24H:%+.2f%%  Vol$%.1fB'%('{:,.2f}'.format(px),m['chg24'],m['vol24B']))
    out.append('  标记价$%s  指数价$%s  基差%+.4f%%'%('{:,.2f}'.format(m['mark_px']),'{:,.2f}'.format(m['idx_px']),m['basis']))
    fr_note='  ⚡负值=空头付费!' if m['fr']<-0.002 else ('  ⚠️偏高' if m['fr']>0.01 else '')
    out.append('  FR:%+.4f%%%s  Fed:%.2f%%  宏观:%.1fh前'%(m['fr'],fr_note,m['fed'],m['macro_age']))
    out.append('  体制:%s  score:%.0f  fc:%s(%.2f)  GEX_ZF:$%s  %s'%(
        m['regime'],m['score'],m['fc'],m['fc_sim'],'{:,.0f}'.format(m['gex_zf']),
        '负Gamma区⚠️' if px<m['gex_zf'] else '正Gamma区✅'))
    out.append('  清算: %s'%liq_str)
    out.append('')
    out.append('▌ Step1: RSI全周期  (多头%d/5  空头%d/5)'%(s1['bull'],s1['bear']))
    out.append('  5M=%s  15M=%s  1H=%s  4H=%s  1D=%s  1W=%s'%(
        s1['5m'],s1['15m'],s1['1h'],s1['4h'],s1['1d'],s1['1w']))
    st_note='  ⚡超卖底部!' if s1['st']['1h']<20 else ('  ⚠️超买!' if s1['st']['1h']>80 else '')
    out.append('  StochRSI 5M=%s  15M=%s  1H=%s%s  4H=%s'%(
        s1['st']['5m'],s1['st']['15m'],s1['st']['1h'],st_note,s1['st']['4h']))
    out.append('')
    out.append('▌ Step2: 布林带多周期  %s'%('⚡收窄=爆发前夕!' if s2['sq'] else '带宽正常'))
    out.append('  15M 上%s 中%s 下%s 宽%.2f%%'%('{:,.1f}'.format(s2['15m']['u']),'{:,.1f}'.format(s2['15m']['m']),'{:,.1f}'.format(s2['15m']['l']),s2['15m']['w']))
    out.append('  1H  上%s 中%s 下%s 宽%.2f%%  位置:%s'%('{:,.1f}'.format(s2['1h']['u']),'{:,.1f}'.format(s2['1h']['m']),'{:,.1f}'.format(s2['1h']['l']),s2['1h']['w'],s2['p1h']))
    out.append('  4H  上%s 中%s 下%s 宽%.2f%%  位置:%s'%('{:,.1f}'.format(s2['4h']['u']),'{:,.1f}'.format(s2['4h']['m']),'{:,.1f}'.format(s2['4h']['l']),s2['4h']['w'],s2['p4h']))
    out.append('  1D  上%s 中%s 下%s 宽%.2f%%'%('{:,.1f}'.format(s2['1d']['u']),'{:,.1f}'.format(s2['1d']['m']),'{:,.1f}'.format(s2['1d']['l']),s2['1d']['w']))
    out.append('')
    out.append('▌ Step3: EMA均线+VWAP  (上方%d/4  下方%d/4)'%(s3['bull'],s3['bear']))
    out.append('  EMA7_1H=$%s  EMA20_1H=$%s  EMA50_4H=$%s'%('{:,.0f}'.format(s3['e7_1h']),'{:,.0f}'.format(s3['e20_1h']),'{:,.0f}'.format(s3['e50_4h'])))
    out.append('  EMA100_4H=$%s  EMA200_1D=$%s'%('{:,.0f}'.format(s3['e100_4h']),'{:,.0f}'.format(s3['e200_1d'])))
    out.append('  VWAP_1H=$%s  VWAP_4H=$%s'%('{:,.0f}'.format(s3['vwap_1h']),'{:,.0f}'.format(s3['vwap_4h'])))
    out.append('')
    out.append('▌ Step4: MACD多周期')
    for tf,k in [('1H','1h'),('4H','4h'),('1D','1d')]:
        d2=s4[k]; bar='📈' if d2['h']>0 else '📉'
        out.append('  %s  DIF=%+.1f  DEA=%+.1f  柱=%+.2f%s'%(tf,d2['d'],d2['s'],d2['h'],bar))
    out.append('')
    out.append('▌ Step5: ATR波动率+成交量')
    out.append('  ATR 5M=$%.1f  15M=$%.1f  1H=$%s  4H=$%s  1D=$%s'%(
        s5['5m'],s5['15m'],'{:,.0f}'.format(s5['1h']),'{:,.0f}'.format(s5['4h']),'{:,.0f}'.format(s5['1d'])))
    out.append('  SL基准 1H=$%s(1.5×ATR)  4H=$%s'%('{:,.0f}'.format(s5['sl_1h']),'{:,.0f}'.format(s5['sl_4h'])))
    vr_note='⚡放量突破!' if s5['vr_1h']>1.8 else ('缩量' if s5['vr_1h']<0.7 else '量能正常')
    out.append('  量能比 1H=%.2fx  4H=%.2fx  %s'%(s5['vr_1h'],s5['vr_4h'],vr_note))
    out.append('')
    out.append('▌ Step6: SMC价格结构全周期  (多头%d/4  空头%d/4)'%(s6['bull'],s6['bear']))
    for tf,k in [('1H','1h'),('4H','4h'),('1D','1d'),('1W','1w')]:
        h=s6['hi'][k]; l=s6['lo'][k]
        out.append('  %s: %s  高=$%s  低=$%s'%(tf,s6[k],'{:,.1f}'.format(h),'{:,.1f}'.format(l)))
    out.append('')
    out.append('▌ Step7: FVG磁铁+OB订单块  (Bull磁铁%d/3  Bear磁铁%d/3)'%(s7['bull'],s7['bear']))
    fvg=s7['fvg']
    out.append('  1H Bull FVG: %s  1H Bear FVG: %s'%(fvg_s(fvg['bull_1h']),fvg_s(fvg['bear_1h'])))
    out.append('  4H Bull FVG: %s  4H Bear FVG: %s'%(fvg_s(fvg['bull_4h']),fvg_s(fvg['bear_4h'])))
    out.append('  1D Bull FVG: %s  1D Bear FVG: %s'%(fvg_s(fvg['bull_1d']),fvg_s(fvg['bear_1d'])))
    ob=s7['ob']
    out.append('  1H Bull OB: %s  1H Bear OB: %s'%(ob_s(ob['bull_1h']),ob_s(ob['bear_1h'])))
    out.append('  4H Bull OB: %s  4H Bear OB: %s'%(ob_s(ob['bull_4h']),ob_s(ob['bear_4h'])))
    out.append('')
    out.append('▌ Step8: OI资金流+FR  (多头维度%d/3  空头维度%d/3)'%(s8['bull'],s8['bear']))
    out.append('  OI序列(千张/1H): %s'%s8['seq'])
    out.append('  15M=%+.3f%%  1H=%+.3f%%  4H=%+.3f%%'%(s8['15m'],s8['1h'],s8['4h']))
    out.append('  信号: %s  %s'%(s8['sig'],s8['note']))
    cvd_note='买方主导' if s9['cvd_1h']>500 else ('卖方主导' if s9['cvd_1h']<-500 else '中性')
    out.append('  CVD_1H=%+.0f  %s'%(s9['cvd_1h'],cvd_note))
    out.append('')
    out.append('▌ Step9: LSR+Taker聪明钱  (多头维度%d/4  空头维度%d/3)'%(s9['bull'],s9['bear']))
    out.append('  散户账户:%s%%多  大户账户:%s  变化:%+.1fpt'%(s9['retail'],s9['big'],s9['bl_chg']))
    out.append('  大户仓位:%s  变化:%+.1fpt'%(s9['pos'],s9['bp_chg']))
    out.append('  Taker(5M):%s  趋势:%s'%(s9['tk'],s9['tk_trend']))
    out.append('  Taker确认:%s'%('✅已确认(≥2根>1.0)' if s9['tk_confirm'] else '⏳未确认'))
    out.append('')
    out.append('▌ Step10: 宏观+方仓+GEX')
    out.append('  体制:%s  score:%.0f  fc:%s(sim=%.2f)'%(s10['regime'],s10['score'],s10['fc'],s10['fc_sim']))
    out.append('  GEX ZF=$%s  %s'%('{:,.0f}'.format(s10['gzf']),'当前在ZF下方=负Gamma' if px<s10['gzf'] else '当前在ZF上方=正Gamma'))
    out.append('')
    out.append('▌ Step11: 多空综合评分（双向）')
    out.append('  多头得分: %d  空头得分: %d  总: %d'%(s11['bull'],s11['bear'],s11['total']))
    out.append('  多头占比: %d%%  空头占比: %d%%'%(s11['bull_pct'],s11['bear_pct']))
    out.append('  综合方向: 【%s】'%s11['bias'])
    out.append('')
    out.append('══════════════════════════════════════════════════════════════')
    out.append('▌ Step12: 三方联合裁决')
    out.append('══════════════════════════════════════════════════════════════')
    out.append('')
    out.append('【短线 %s】→ %s'%(sh['period'],sh['dir']))
    if sh['dir']!='WAIT' and sh.get('entry'):
        e=sh['entry']; s_=sh['sl']; t1=sh['tp1']; t2=sh['tp2']
        out.append('  入场 $%s  SL $%s  TP $%s→$%s  RR=%s'%(
            '{:,.2f}'.format(e),'{:,.2f}'.format(s_),'{:,.2f}'.format(t1),'{:,.2f}'.format(t2),sh['rr']))
        out.append('  触发: %s'%sh['trigger'])
        out.append('  逻辑: %s'%sh['logic'])
    else:
        out.append('  StochRSI未到极值或多空信号不明确，等待')
    out.append('')
    out.append('【中线 %s】→ %s'%(mid['period'],mid['dir']))
    if mid['dir']!='WAIT' and mid.get('entry'):
        e=mid['entry']; s_=mid['sl']; t1=mid['tp1']; t2=mid['tp2']
        out.append('  入场 $%s  SL $%s  TP $%s→$%s  RR=%s'%(
            '{:,.2f}'.format(e),'{:,.2f}'.format(s_),'{:,.2f}'.format(t1),'{:,.2f}'.format(t2),mid['rr']))
        out.append('  逻辑: %s'%mid['logic'])
        out.append('  注意: %s'%mid['note'])
    else:
        out.append('  4H结构%s  等待HH+HL或LH+LL确认方向'%s6['4h'])
    out.append('')
    out.append('【长线 %s】→ %s'%(lng['period'],lng['dir']))
    if lng['dir']!='WAIT' and lng.get('elo'):
        out.append('  建仓区 $%s~$%s  SL $%s  TP $%s'%(
            '{:,.2f}'.format(lng['elo']),'{:,.2f}'.format(lng['ehi']),
            '{:,.2f}'.format(lng['sl']),'{:,.2f}'.format(lng['tp'])))
        out.append('  逻辑: %s'%lng['logic'])
    else:
        out.append('  RSI1D=%s RSI1W=%s  未达超卖(<32)或超买(>70)'%(s1['1d'],s1['1w']))

    # Step12文本（方便AI直接读取）
    out.append('')
    out.append('▌ Step12 JSON结构（机器可读）')
    s12=st.get('s12',{})
    out.append('  短线: dir=%s  trigger=%s  rr=%s'%(s12.get('short_dir','?'),s12.get('short_trigger','?')[:30],s12.get('short_rr','?')))
    out.append('  中线: dir=%s  logic=%s'%(s12.get('mid_dir','?'),s12.get('mid_logic','?')[:40]))
    out.append('  长线: dir=%s  logic=%s'%(s12.get('long_dir','?'),s12.get('long_logic','?')[:40]))
    return '\n'.join(out)


if __name__=='__main__':
    syms=sys.argv[1:] or ['BTC','ETH']
    for sym in syms:
        try:
            r=analyze(sym.upper())
            print(format_report(r))
            print()
        except Exception as e:
            import traceback; traceback.print_exc()
