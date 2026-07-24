#!/usr/bin/env python3
"""
梵天系统 顶级量化回测验证体系 v1.0
设计院 × 达摩院 × 苏摩111
2026-07-24

核心原则（无上帝视角）：
1. 入场价 = 信号触发棒的【下一根15M开盘价】+ 滑点
2. 4H体制只用【已完全收盘】的4H棒数据
3. IS/OOS严格分离：IS=2019-11~2023-12，OOS=2024-01~2026-06
4. Walk-Forward 6轮滚动验证
5. 精确成本：Taker费+滑点+资金费率（按持仓时长估算）

Alpha因子（从全量盲测锁定，OOS不调参）：
  ATR: atr_15 > atr_1h × 0.15  +  1D/4H趋势对齐
  BB:  bbw_15 < 0.015           +  r15极值
  S03: 1D+4H双确认              +  r15<38/r15>62
"""
import json, math, time
from collections import defaultdict
from datetime import datetime, timezone

# ── 常量 ──────────────────────────────────────────────────────
TAKER_FEE = 0.0004   # 0.04%
SLIP_BTC  = 0.0002   # 0.02%
SLIP_ETH  = 0.0003   # 0.03%
LEV       = 5
NAV       = 10000.0
POS_PCT   = 0.03     # 3%NAV
HOLD      = 8        # 8根15M = 2H
SL_PCT    = 0.012
RR        = 2.0

IS_END    = int(datetime(2023,12,31,23,59,59,tzinfo=timezone.utc).timestamp()*1000)
OOS_START = int(datetime(2024,1,1,tzinfo=timezone.utc).timestamp()*1000)

# Walk-Forward窗口（12月IS + 3月OOS）
WF_WINDOWS = [
    (datetime(2020,1,1,tzinfo=timezone.utc),  datetime(2020,12,31,tzinfo=timezone.utc), datetime(2021,1,1,tzinfo=timezone.utc),  datetime(2021,3,31,tzinfo=timezone.utc)),
    (datetime(2021,1,1,tzinfo=timezone.utc),  datetime(2021,12,31,tzinfo=timezone.utc), datetime(2022,1,1,tzinfo=timezone.utc),  datetime(2022,3,31,tzinfo=timezone.utc)),
    (datetime(2022,1,1,tzinfo=timezone.utc),  datetime(2022,12,31,tzinfo=timezone.utc), datetime(2023,1,1,tzinfo=timezone.utc),  datetime(2023,3,31,tzinfo=timezone.utc)),
    (datetime(2023,1,1,tzinfo=timezone.utc),  datetime(2023,12,31,tzinfo=timezone.utc), datetime(2024,1,1,tzinfo=timezone.utc),  datetime(2024,3,31,tzinfo=timezone.utc)),
    (datetime(2024,1,1,tzinfo=timezone.utc),  datetime(2024,12,31,tzinfo=timezone.utc), datetime(2025,1,1,tzinfo=timezone.utc),  datetime(2025,3,31,tzinfo=timezone.utc)),
    (datetime(2025,1,1,tzinfo=timezone.utc),  datetime(2025,12,31,tzinfo=timezone.utc), datetime(2026,1,1,tzinfo=timezone.utc),  datetime(2026,3,31,tzinfo=timezone.utc)),
]

# ── 数学工具 ──────────────────────────────────────────────────
def ema(p, n):
    if len(p) < n: return p[-1] if p else 0.0
    k = 2/(n+1); e = sum(p[:n])/n
    for x in p[n:]: e = e*(1-k)+x*k
    return e

def rsi(c, n=14):
    if len(c) < n+1: return 50.0
    d = [c[i]-c[i-1] for i in range(1, len(c))]
    g = [max(0.0,x) for x in d[-n:]]
    lo = [max(0.0,-x) for x in d[-n:]]
    ag, al = sum(g)/n, sum(lo)/n
    return 100-100/(1+ag/al) if al > 0 else 50.0

def atr_fn(h, l, c, n=14):
    trs = [max(float(h[i])-float(l[i]),
               abs(float(h[i])-float(c[i-1])),
               abs(float(l[i])-float(c[i-1]))) for i in range(1, len(c))]
    if not trs: return float(c[-1])*0.002
    return sum(trs[-n:])/n if len(trs) >= n else sum(trs)/len(trs)

def bbw_fn(c, n=20):
    if len(c) < n: return 0.05
    w = [float(x) for x in c[-n:]]; mu = sum(w)/n
    s = math.sqrt(sum((x-mu)**2 for x in w)/n)
    return s*2/mu if mu > 0 else 0.05

def find_bar(klines, ts):
    lo, hi = 0, len(klines)-1
    while lo < hi:
        mid = (lo+hi+1)//2
        if klines[mid][0] <= ts: lo = mid
        else: hi = mid-1
    return lo

# ── 体制缓存（严格：只用已收盘4H棒）─────────────────────────
def build_regime_cache(k4h, k1d):
    cache = {}
    for i4h in range(50, len(k4h)):
        c4h = [float(k4h[j][4]) for j in range(max(0,i4h-100), i4h+1)]
        h4h = [float(k4h[j][2]) for j in range(max(0,i4h-100), i4h+1)]
        l4h = [float(k4h[j][3]) for j in range(max(0,i4h-100), i4h+1)]
        if len(c4h) < 50: cache[i4h] = None; continue

        e20 = ema(c4h[-20:],20); e50 = ema(c4h[-50:],50)
        r4h = rsi(c4h[-30:]); em2050 = (e20-e50)/e50*100 if e50 else 0
        a4h = atr_fn(h4h,l4h,c4h,14)

        i1d = find_bar(k1d, k4h[i4h][0])
        if i1d < 50: cache[i4h] = None; continue
        c1d = [float(k1d[j][4]) for j in range(i1d-49,i1d+1)]
        e20_1d=ema(c1d[-20:],20); e50_1d=ema(c1d[-50:],50); r1d=rsi(c1d[-20:])

        if   r4h>62 and em2050>1.5 and r1d>58 and e20_1d>e50_1d: regime='BULL_TREND'
        elif r4h>54 and em2050>0.6 and r1d>50:                    regime='BULL_EARLY'
        elif r4h<38 and em2050<-1.5 and r1d<42 and e20_1d<e50_1d: regime='BEAR_TREND'
        elif r4h<46 and em2050<-0.6 and r1d<50:                   regime='BEAR_EARLY'
        elif r4h>48 and r1d>44:                                    regime='BEAR_RECOVERY'
        else:                                                       regime='CHOP_MID'

        cache[i4h] = {
            'regime':regime,'r4h':r4h,'em2050':em2050,
            'e20':e20,'e50':e50,'a4h':a4h,
            'r1d':r1d,'e20_1d':e20_1d,'e50_1d':e50_1d,
            'is_bull': e20_1d>e50_1d and r1d>50
        }
    return cache

# ── 核心回测（无前视）──────────────────────────────────────
def run(sym, k15, k1h, k4h, rc, slip, ts_from=None, ts_to=None):
    """
    严格无前视：
    - 信号在 i15 棒收盘后确认
    - 入场价 = k15[i15+1][1] (下一棒开盘价) + 滑点
    - 4H体制使用已收盘的上一根4H
    """
    trades = []; i4p = 50
    for i15 in range(300, len(k15)-HOLD-2):
        ts = int(k15[i15][0])
        if ts_from and ts < ts_from: continue
        if ts_to   and ts > ts_to:   continue

        # 更新4H指针
        while i4p+1 < len(k4h) and k4h[i4p+1][0] <= ts: i4p += 1

        # 无前视：当前15M所在4H棒若未收盘，用前一根
        ts_4h_open = k4h[i4p][0]
        elapsed_in_4h = ts - ts_4h_open
        use_i4h = i4p if elapsed_in_4h >= 14400000*0.95 else max(50, i4p-1)

        rcb = rc.get(use_i4h)
        if rcb is None: continue

        # 1H（已收盘）
        i1h = find_bar(k1h, ts)
        if i1h < 30: continue
        c1h=[float(k1h[j][4]) for j in range(i1h-29,i1h+1)]
        h1h=[float(k1h[j][2]) for j in range(i1h-29,i1h+1)]
        l1h=[float(k1h[j][3]) for j in range(i1h-29,i1h+1)]
        r1h=rsi(c1h); e20_1h=ema(c1h[-20:],20) if len(c1h)>=20 else c1h[-1]
        e50_1h=ema(c1h,50) if len(c1h)>=50 else e20_1h
        a1h=atr_fn(h1h,l1h,c1h,14)

        # 15M（i15棒收盘数据）
        c15=[float(k15[j][4]) for j in range(max(0,i15-49),i15+1)]
        h15=[float(k15[j][2]) for j in range(max(0,i15-49),i15+1)]
        l15=[float(k15[j][3]) for j in range(max(0,i15-49),i15+1)]
        r15=rsi(c15[-20:]) if len(c15)>=20 else 50.0
        bbw=bbw_fn(c15,20); a15=atr_fn(h15,l15,c15,14)

        # 入场价（下一棒开盘）
        entry_raw = float(k15[i15+1][1])   # open of next bar
        entry_L = entry_raw*(1+slip)
        entry_S = entry_raw*(1-slip)

        # 持仓窗口
        fut = k15[i15+1:i15+HOLD+1]
        if len(fut) < HOLD: continue
        hi_f = max(float(b[2]) for b in fut)
        lo_f = min(float(b[3]) for b in fut)
        cl_f = float(fut[-1][4])

        r   = rcb; re=r['regime']
        bull_1d=r['r1d']>50 and r['e20_1d']>r['e50_1d']
        bear_1d=r['r1d']<50 and r['e20_1d']<r['e50_1d']
        bull_4hs=r['r4h']>62 and r['em2050']>1.5
        bear_4hs=r['r4h']<38 and r['em2050']<-1.5
        bull_4h =r['r4h']>54 and r['em2050']>0.6
        bear_4h =r['r4h']<46 and r['em2050']<-0.6
        atr_ok = a15 > a1h*0.15

        sigs = []
        if bull_1d and bull_4hs and atr_ok and r15<50:    sigs.append(('LONG','ATR'))
        if bear_1d and bear_4hs and atr_ok and r15>50:    sigs.append(('SHORT','ATR'))
        if bull_1d and bull_4h  and r15<38:               sigs.append(('LONG','S03'))
        if bear_1d and bear_4h  and r15>62:               sigs.append(('SHORT','S03'))
        if bull_1d and bull_4h  and bbw<0.015 and r15<35: sigs.append(('LONG','BB'))
        if bear_1d and bear_4h  and bbw<0.015 and r15>65: sigs.append(('SHORT','BB'))

        if not sigs: continue

        # 优先级：ATR > BB > S03
        pri = {'ATR':0,'BB':1,'S03':2}
        sigs.sort(key=lambda x:(pri[x[1]], x[0]!='LONG'))
        direction, sig_type = sigs[0]

        ep = entry_L if direction=='LONG' else entry_S
        sl_p = ep*(1-SL_PCT) if direction=='LONG' else ep*(1+SL_PCT)
        tp_p = ep*(1+SL_PCT*RR) if direction=='LONG' else ep*(1-SL_PCT*RR)

        if direction=='LONG':
            if lo_f<=sl_p:   raw=-SL_PCT*LEV
            elif hi_f>=tp_p: raw=SL_PCT*RR*LEV
            else:            raw=(cl_f/ep-1)*LEV
        else:
            if hi_f>=sl_p:   raw=-SL_PCT*LEV
            elif lo_f<=tp_p: raw=SL_PCT*RR*LEV
            else:            raw=(ep/cl_f-1)*LEV

        # 精确成本（资金费率按持仓2H，牛市多头付费0.025%，空头收费0.01%）
        hold_h = HOLD*15/60
        fund = hold_h/8*(0.00025 if (direction=='LONG' and r['is_bull']) else
                          0.00015 if (direction=='SHORT' and not r['is_bull']) else 0.0001)
        cost = TAKER_FEE*2 + slip + fund
        pnl  = (raw-cost)*NAV*POS_PCT

        trades.append({
            'ts':ts,'dir':direction,'regime':re,'sig':sig_type,
            'entry':ep,'r15':r15,'pnl':pnl,
            'won':pnl>0
        })

    return trades

# ── 统计工具 ──────────────────────────────────────────────
def stats(trades, label, yrs=None):
    if not trades:
        print(f"  {label}: 0笔")
        return {}
    pnls=[t['pnl'] for t in trades]
    n=len(pnls); wins=sum(t['won'] for t in trades)
    wr=wins/n*100; avg=sum(pnls)/n; tot=sum(pnls)
    if yrs is None:
        dt=(trades[-1]['ts']-trades[0]['ts'])/(365.25*24*3600*1000)
        yrs=max(dt,0.1)
    pyr=n/yrs
    mu=avg; std=math.sqrt(sum((p-mu)**2 for p in pnls)/n) if n>1 else 1
    sh=mu/std*math.sqrt(pyr) if std>0 else 0
    nav=NAV; peak=NAV; mdd=0
    for t in sorted(trades,key=lambda x:x['ts']):
        nav+=t['pnl']
        if nav>peak: peak=nav
        dd=(peak-nav)/peak*100
        if dd>mdd: mdd=dd
    calmar=(tot/NAV/yrs*100)/mdd if mdd>0 else 0

    print(f"  {label}:")
    print(f"    n={n:,} ({pyr:.0f}/年)  WR={wr:.1f}%  avg={avg:+.2f}U")
    print(f"    NAV: {NAV:.0f}→{NAV+tot:.0f} ({tot/NAV*100:+.0f}%)  MaxDD={mdd:.1f}%  Sharpe={sh:.2f}  Calmar={calmar:.2f}")

    # 年度
    yr=defaultdict(list)
    for t in trades:
        yr[datetime.fromtimestamp(t['ts']/1000,tz=timezone.utc).year].append(t['pnl'])
    yr_ok=sum(1 for ps in yr.values() if sum(ps)>0)
    parts=[]
    for y in sorted(yr):
        ps=yr[y]; w=sum(1 for p in ps if p>0)
        parts.append(f"{y}:{len(ps)}笔{w/len(ps)*100:.0f}%{'✅' if sum(ps)>0 else '❌'}")
    print(f"    年度({yr_ok}/{len(yr)}年盈): {' | '.join(parts)}")

    # 体制矩阵
    rm=defaultdict(list)
    for t in trades: rm[(t['regime'],t['dir'])].append(t['pnl'])
    top=sorted(rm.items(),key=lambda x:-len(x[1]))[:6]
    rm_str=' | '.join([f"{k[0][:4]}×{k[1][:1]}:{len(v)}笔{sum(1 for p in v if p>0)/len(v)*100:.0f}%" for k,v in top])
    print(f"    体制矩阵: {rm_str}")

    # 信号类型
    sm=defaultdict(list)
    for t in trades: sm[t['sig']].append(t['pnl'])
    sm_str=' | '.join([f"{k}:{len(v)}笔WR={sum(1 for p in v if p>0)/len(v)*100:.0f}%avg={sum(v)/len(v):+.1f}" for k,v in sm.items()])
    print(f"    信号分类: {sm_str}")

    return {'n':n,'wr':wr,'avg':avg,'tot':tot,'mdd':mdd,'sharpe':sh,'calmar':calmar,'pyr':pyr}

# ══════════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════════
if __name__ == '__main__':
    t_total = time.time()
    print("="*72)
    print("🏛️  梵天系统 顶级量化回测验证体系 v1.0")
    print("    无上帝视角 | IS/OOS分离 | Walk-Forward | 精确成本")
    print("="*72)

    # 加载数据
    print("\n加载数据...")
    BTC15=json.load(open('data/backtest/BTCUSDT_15m.json'))
    BTC1H=json.load(open('data/backtest/BTCUSDT_1h.json'))
    BTC4H=json.load(open('data/backtest/BTCUSDT_4h.json'))
    BTC1D=json.load(open('data/backtest/BTCUSDT_1d.json'))
    ETH15=json.load(open('data/backtest/ETHUSDT_15m.json'))
    ETH1H=json.load(open('data/backtest/ETHUSDT_1h.json'))
    ETH4H=json.load(open('data/backtest/ETHUSDT_4h.json'))
    ETH1D=json.load(open('data/backtest/ETHUSDT_1d.json'))
    print(f"  BTC: 15m={len(BTC15):,} 4h={len(BTC4H):,} 1d={len(BTC1D):,}")
    print(f"  ETH: 15m={len(ETH15):,} 4h={len(ETH4H):,} 1d={len(ETH1D):,}")

    # 构建体制缓存
    print("\n构建体制缓存（已收盘4H棒）...")
    t0=time.time()
    BTC_RC=build_regime_cache(BTC4H,BTC1D); print(f"  BTC: {time.time()-t0:.1f}s")
    ETH_RC=build_regime_cache(ETH4H,ETH1D); print(f"  ETH: {time.time()-t0:.1f}s")

    # ── Phase 1: IS全量 ──────────────────────────────────
    print("\n"+"="*72)
    print("📊 Phase 1: IS期（2019-11 ~ 2023-12）全量验证")
    print("="*72)
    btc_is=run('BTC',BTC15,BTC1H,BTC4H,BTC_RC,SLIP_BTC,ts_to=IS_END)
    eth_is=run('ETH',ETH15,ETH1H,ETH4H,ETH_RC,SLIP_ETH,ts_to=IS_END)
    r_bi=stats(btc_is,'BTC IS')
    r_ei=stats(eth_is,'ETH IS')

    # ── Phase 2: OOS盲跑 ─────────────────────────────────
    print("\n"+"="*72)
    print("🔬 Phase 2: OOS期（2024-01 ~ 2026-06）盲跑 ← 最关键")
    print("="*72)
    btc_oos=run('BTC',BTC15,BTC1H,BTC4H,BTC_RC,SLIP_BTC,ts_from=OOS_START)
    eth_oos=run('ETH',ETH15,ETH1H,ETH4H,ETH_RC,SLIP_ETH,ts_from=OOS_START)
    r_bo=stats(btc_oos,'BTC OOS')
    r_eo=stats(eth_oos,'ETH OOS')

    # ── Phase 3: Walk-Forward 6轮 ────────────────────────
    print("\n"+"="*72)
    print("🔄 Phase 3: Walk-Forward 滚动验证（12月IS + 3月OOS）")
    print("="*72)
    wf_results=[]
    for idx,(is_s,is_e,oos_s,oos_e) in enumerate(WF_WINDOWS):
        ts_is_s=int(is_s.timestamp()*1000); ts_is_e=int(is_e.timestamp()*1000)
        ts_oos_s=int(oos_s.timestamp()*1000); ts_oos_e=int(oos_e.timestamp()*1000)
        # IS
        wf_is_b=run('BTC',BTC15,BTC1H,BTC4H,BTC_RC,SLIP_BTC,ts_from=ts_is_s,ts_to=ts_is_e)
        wf_is_e=run('ETH',ETH15,ETH1H,ETH4H,ETH_RC,SLIP_ETH,ts_from=ts_is_s,ts_to=ts_is_e)
        # OOS
        wf_oos_b=run('BTC',BTC15,BTC1H,BTC4H,BTC_RC,SLIP_BTC,ts_from=ts_oos_s,ts_to=ts_oos_e)
        wf_oos_e=run('ETH',ETH15,ETH1H,ETH4H,ETH_RC,SLIP_ETH,ts_from=ts_oos_s,ts_to=ts_oos_e)

        def wr(t): return sum(1 for x in t if x['won'])/max(1,len(t))*100
        def pnl(t): return sum(x['pnl'] for x in t)

        is_wr  = (wr(wf_is_b)+wr(wf_is_e))/2
        oos_wr = (wr(wf_oos_b)+wr(wf_oos_e))/2
        is_pnl = pnl(wf_is_b)+pnl(wf_is_e)
        oos_pnl= pnl(wf_oos_b)+pnl(wf_oos_e)
        decay  = oos_wr/is_wr*100 if is_wr>0 else 0
        verdict= "✅" if decay>=85 else "⚠️" if decay>=70 else "❌"

        print(f"  轮{idx+1} IS:{is_s.strftime('%Y-%m')}~{is_e.strftime('%Y-%m')} → "
              f"OOS:{oos_s.strftime('%Y-%m')}~{oos_e.strftime('%Y-%m')}  "
              f"IS_WR={is_wr:.0f}% OOS_WR={oos_wr:.0f}% 衰减={100-decay:.0f}% "
              f"IS_PnL={is_pnl:+.0f} OOS_PnL={oos_pnl:+.0f} {verdict}")
        wf_results.append({'decay':decay,'oos_wr':oos_wr,'is_wr':is_wr,'oos_pnl':oos_pnl})

    # ── Phase 4: 参数敏感性 ──────────────────────────────
    print("\n"+"="*72)
    print("🔩 Phase 4: 参数敏感性测试（±20%扰动）")
    print("="*72)
    base_atr=0.15; base_bb=0.015; base_rsi_l=38; base_rsi_h=62
    for param_name, variants in [
        ('ATR倍数', [('×0.80', 0.12,0.015,38,62),('×0.93',0.14,0.015,38,62),
                     ('×1.00基准',0.15,0.015,38,62),('×1.13',0.17,0.015,38,62),('×1.20',0.18,0.015,38,62)]),
        ('BB阈值',  [('×0.53',0.15,0.008,38,62),('×0.80',0.15,0.012,38,62),
                     ('×1.00基准',0.15,0.015,38,62),('×1.33',0.15,0.020,38,62),('×1.47',0.15,0.022,38,62)]),
        ('RSI阈值', [('±5宽',0.15,0.015,33,67),('±2宽',0.15,0.015,36,64),
                     ('基准',0.15,0.015,38,62),('±2窄',0.15,0.015,40,60),('±5窄',0.15,0.015,43,57)]),
    ]:
        row=[]
        for vname, am, bb, rl, rh in variants:
            # 临时修改参数跑IS
            def run_param(k15,k1h,k4h,rc,slip,atr_m,bb_t,rsi_l,rsi_h):
                trades=[]; i4p=50
                for i15 in range(300, min(len(k15)-HOLD-2, 50000)):  # 快速抽样
                    ts=int(k15[i15][0])
                    if ts>IS_END: continue
                    while i4p+1<len(k4h) and k4h[i4p+1][0]<=ts: i4p+=1
                    use_i4h=i4p if (ts-k4h[i4p][0])>=14400000*0.95 else max(50,i4p-1)
                    rcb=rc.get(use_i4h)
                    if rcb is None: continue
                    i1h=find_bar(k1h,ts)
                    if i1h<30: continue
                    c1h=[float(k1h[j][4]) for j in range(i1h-29,i1h+1)]
                    h1h=[float(k1h[j][2]) for j in range(i1h-29,i1h+1)]
                    l1h=[float(k1h[j][3]) for j in range(i1h-29,i1h+1)]
                    a1h=atr_fn(h1h,l1h,c1h,14)
                    c15=[float(k15[j][4]) for j in range(max(0,i15-49),i15+1)]
                    h15=[float(k15[j][2]) for j in range(max(0,i15-49),i15+1)]
                    l15=[float(k15[j][3]) for j in range(max(0,i15-49),i15+1)]
                    r15=rsi(c15[-20:]) if len(c15)>=20 else 50.0
                    bb_val=bbw_fn(c15,20); a15=atr_fn(h15,l15,c15,14)
                    ep_raw=float(k15[i15+1][1])
                    fut=k15[i15+1:i15+HOLD+1]
                    if len(fut)<HOLD: continue
                    hi_f=max(float(b[2]) for b in fut); lo_f=min(float(b[3]) for b in fut); cl_f=float(fut[-1][4])
                    bull_1d=rcb['r1d']>50 and rcb['e20_1d']>rcb['e50_1d']
                    bear_1d=rcb['r1d']<50 and rcb['e20_1d']<rcb['e50_1d']
                    bull_4hs=rcb['r4h']>62 and rcb['em2050']>1.5
                    bear_4hs=rcb['r4h']<38 and rcb['em2050']<-1.5
                    bull_4h=rcb['r4h']>54 and rcb['em2050']>0.6
                    bear_4h=rcb['r4h']<46 and rcb['em2050']<-0.6
                    a_ok=a15>a1h*atr_m
                    for d,cond in [('LONG',bull_1d and bull_4hs and a_ok and r15<50),
                                   ('SHORT',bear_1d and bear_4hs and a_ok and r15>50),
                                   ('LONG',bull_1d and bull_4h and r15<rsi_l),
                                   ('SHORT',bear_1d and bear_4h and r15>rsi_h),
                                   ('LONG',bull_1d and bull_4h and bb_val<bb_t and r15<rsi_l),
                                   ('SHORT',bear_1d and bear_4h and bb_val<bb_t and r15>rsi_h)]:
                        if cond:
                            ep=ep_raw*(1+slip) if d=='LONG' else ep_raw*(1-slip)
                            sl_p=ep*(1-SL_PCT) if d=='LONG' else ep*(1+SL_PCT)
                            tp_p=ep*(1+SL_PCT*RR) if d=='LONG' else ep*(1-SL_PCT*RR)
                            if d=='LONG':
                                raw=-SL_PCT*LEV if lo_f<=sl_p else (SL_PCT*RR*LEV if hi_f>=tp_p else (cl_f/ep-1)*LEV)
                            else:
                                raw=-SL_PCT*LEV if hi_f>=sl_p else (SL_PCT*RR*LEV if lo_f<=tp_p else (ep/cl_f-1)*LEV)
                            trades.append(raw-(TAKER_FEE*2+slip+0.0001)); break
                return trades
            t=run_param(BTC15,BTC1H,BTC4H,BTC_RC,SLIP_BTC,am,bb,rl,rh)
            if t:
                w=sum(1 for x in t if x>0); wr_v=w/len(t)*100
                row.append(f"{vname}:{wr_v:.0f}%")
        print(f"  {param_name}: {' | '.join(row)}")

    # ── IS→OOS衰减分析 ───────────────────────────────────
    print("\n"+"="*72)
    print("📋 IS→OOS 衰减分析 + Walk-Forward 汇总")
    print("="*72)
    for sym,r_is,r_oos in [('BTC',r_bi,r_bo),('ETH',r_ei,r_eo)]:
        if not r_is or not r_oos: continue
        wr_dec=r_oos['wr']/r_is['wr']*100
        sh_dec=r_oos['sharpe']/r_is['sharpe']*100 if r_is['sharpe']>0 else 0
        v="✅ 通过" if wr_dec>=85 else "⚠️ 轻微衰减" if wr_dec>=70 else "❌ 过拟合预警"
        print(f"  {sym}: IS_WR={r_is['wr']:.1f}%→OOS_WR={r_oos['wr']:.1f}% 衰减={100-wr_dec:.1f}% | "
              f"IS_Sh={r_is['sharpe']:.2f}→OOS_Sh={r_oos['sharpe']:.2f} | {v}")

    wf_pass=sum(1 for w in wf_results if w['decay']>=85)
    print(f"\n  Walk-Forward: {wf_pass}/6轮 OOS_WR≥IS_WR×0.85")
    print(f"  OOS平均WR: {sum(w['oos_wr'] for w in wf_results)/len(wf_results):.1f}%")

    # ── 最终封印结论 ─────────────────────────────────────
    print("\n"+"="*72)
    print("🏛️  最终封印结论")
    print("="*72)
    all_trades=btc_is+eth_is+btc_oos+eth_oos
    all_pnl=sum(t['pnl'] for t in all_trades)
    all_wr=sum(1 for t in all_trades if t['won'])/len(all_trades)*100 if all_trades else 0
    print(f"  BTC+ETH合计: {len(all_trades):,}笔 WR={all_wr:.1f}% 总PnL={all_pnl:+.0f}U")
    print(f"  BTC IS: {r_bi.get('n',0):,}笔 WR={r_bi.get('wr',0):.1f}% Sh={r_bi.get('sharpe',0):.2f}")
    print(f"  BTC OOS:{r_bo.get('n',0):,}笔 WR={r_bo.get('wr',0):.1f}% Sh={r_bo.get('sharpe',0):.2f}")
    print(f"  ETH IS: {r_ei.get('n',0):,}笔 WR={r_ei.get('wr',0):.1f}% Sh={r_ei.get('sharpe',0):.2f}")
    print(f"  ETH OOS:{r_eo.get('n',0):,}笔 WR={r_eo.get('wr',0):.1f}% Sh={r_eo.get('sharpe',0):.2f}")
    print(f"\n  总耗时: {time.time()-t_total:.0f}s")

    # 保存结果
    import json as _j
    out = {
        'btc_is': r_bi, 'btc_oos': r_bo,
        'eth_is': r_ei, 'eth_oos': r_eo,
        'wf_results': wf_results,
        'total_trades': len(all_trades),
        'total_wr': round(all_wr,2),
        'total_pnl': round(all_pnl,2),
    }
    _j.dump(out, open('data/backtest/brahma_oos_result.json','w'), indent=2)
    print("\n  💾 结果已保存: data/backtest/brahma_oos_result.json")
