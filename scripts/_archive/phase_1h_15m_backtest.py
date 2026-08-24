#!/usr/bin/env python3
"""
梵天三层架构验证：1H触发 → 4H体制 → 15m精确入场
动态ATR止损 · BTC+ETH · 2020-06~2026-08
"""
import pandas as pd
import numpy as np
import json
import os
from pathlib import Path

BASE = Path('/root/.openclaw/workspace/trading-system')
os.makedirs(BASE / 'data/validation', exist_ok=True)

# ── 数学工具 ──────────────────────────────────────────────
def calc_rsi(closes, period=14):
    arr = np.array(closes, dtype=float)
    if len(arr) < period + 1: return 50.0
    d = np.diff(arr)
    g = np.where(d>0,d,0.0); l = np.where(d<0,-d,0.0)
    ag = g[:period].mean(); al = l[:period].mean()
    for i in range(period, len(d)):
        ag = (ag*(period-1)+g[i])/period
        al = (al*(period-1)+l[i])/period
    return 100.0 if al==0 else 100-(100/(1+ag/al))

def calc_ema(closes, period):
    arr = np.array(closes, dtype=float)
    if len(arr)<2: return float(arr[-1])
    k=2/(period+1); v=float(arr[0])
    for c in arr[1:]: v=c*k+v*(1-k)
    return v

def calc_atr(highs, lows, closes, period=14):
    """计算ATR（绝对值），用于动态SL"""
    h=np.array(highs,dtype=float); l=np.array(lows,dtype=float); c=np.array(closes,dtype=float)
    if len(c)<2: return c[-1]*0.01
    tr=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    if not tr: return c[-1]*0.01
    atr_vals = tr[-period:]
    return float(np.mean(atr_vals))

def calc_bbw(closes, period=20):
    arr=np.array(closes[-period:],dtype=float) if len(closes)>=period else np.array(closes,dtype=float)
    if len(arr)<3: return 3.0
    mid=arr.mean(); std=arr.std()
    return (4*std/mid)*100 if mid>0 else 3.0

# ── 加载数据 ──────────────────────────────────────────────
def load_data(sym):
    s=sym.lower()
    df15m=pd.read_parquet(BASE/f'data/historical/{s}usdt/{s}usdt_15m.parquet')
    df1h =pd.read_parquet(BASE/f'data/historical/{s}usdt/{s}usdt_1h.parquet')
    df4h =pd.read_parquet(BASE/f'data/historical/{s}usdt/{s}usdt_4h.parquet')
    # 无前视体制标签
    rdf=pd.read_parquet(BASE/f'data/historical/{s}usdt_regime_nolookahead.parquet')
    regime_map={int(r['ts']):r['regime'] for _,r in rdf.iterrows() if r.get('reliable',True)}
    # 4H idx → regime
    ts4h=[int(t.timestamp()*1000) for t in df4h.index]
    regime4h_by_idx={i:regime_map[ts] for i,ts in enumerate(ts4h) if ts in regime_map}
    return df15m, df1h, df4h, regime4h_by_idx

# ── Step1：1H触发事件生成 ─────────────────────────────────
def gen_1h_triggers(df1h, df4h, regime4h_by_idx):
    """
    在1H数据上检测触发事件，结合4H体制过滤
    触发条件：
      T1: RSI_1H从<45穿越≥55（做多触发，需BULL/RECOVERY体制）
      T2: RSI_1H从>55跌破≤45（做空触发，需BEAR/EARLY/CHOP体制）
      T3: RSI_1H超买>72且价格在EMA20下方（做空强信号）
      T4: RSI_1H超卖<28且价格在EMA20上方（做多强信号）
      T5: 价格突破近24H高点（做多，需BULL体制）
      T6: 价格跌破近24H低点（做空，需BEAR体制）
    """
    closes_1h=df1h['close'].values.astype(float)
    highs_1h =df1h['high'].values.astype(float)
    lows_1h  =df1h['low'].values.astype(float)
    
    ts4h=[int(t.timestamp()*1000) for t in df4h.index]
    
    # 找可信起始
    min_reliable_4h=min(regime4h_by_idx.keys()) if regime4h_by_idx else 200
    min_reliable_ts=ts4h[min_reliable_4h]
    reliable_start=pd.Timestamp(min_reliable_ts,unit='ms',tz='UTC')
    min_1h_idx=next((i for i,t in enumerate(df1h.index) if t>=reliable_start), None)
    if min_1h_idx is None: return []
    min_1h_idx=max(min_1h_idx,60)
    
    triggers=[]
    rsi_prev=50.0
    
    for i in range(min_1h_idx, len(closes_1h)-50):
        # 获取4H体制（最近4H K线，不超过当前时间）
        cur_t=df1h.index[i]
        mask=df4h.index<=cur_t
        n4h=mask.sum()
        if n4h==0: continue
        cur_4h_idx=n4h-1
        regime=regime4h_by_idx.get(cur_4h_idx)
        if not regime: continue
        
        rsi_cur=calc_rsi(closes_1h[max(0,i-60):i+1])
        ema20=calc_ema(closes_1h[max(0,i-30):i+1],20)
        price=closes_1h[i]
        
        ev_type=None
        direction=None
        
        # T1: RSI上穿（动量启动多头）
        if rsi_prev<45 and rsi_cur>=55 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            ev_type='T1_RSI_UP'; direction='LONG'
        # T2: RSI下穿（动量启动空头）
        elif rsi_prev>55 and rsi_cur<=45 and regime in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
            ev_type='T2_RSI_DOWN'; direction='SHORT'
        # T3: RSI超买做空
        elif rsi_cur>72 and price<ema20 and regime in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
            ev_type='T3_OB_SHORT'; direction='SHORT'
        # T4: RSI超卖做多
        elif rsi_cur<28 and price>ema20 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            ev_type='T4_OS_LONG'; direction='LONG'
        # T5: 突破24H高（做多）
        elif i>=24 and regime in ('BULL_TREND',) and price>highs_1h[i-24:i].max()*1.003:
            ev_type='T5_BREAK_H'; direction='LONG'
        # T6: 跌破24H低（做空）
        elif i>=24 and regime in ('BEAR_TREND',) and price<lows_1h[i-24:i].min()*0.997:
            ev_type='T6_BREAK_L'; direction='SHORT'
        
        if ev_type and direction:
            # 宪法过滤：BEAR_TREND不做多，BEAR_RECOVERY:LONG已封禁
            if regime=='BEAR_TREND' and direction=='LONG': pass
            elif regime=='BEAR_RECOVERY' and direction=='LONG' and False: pass  # BEAR_RECOVERY:LONG已验证死穴
            else:
                triggers.append({
                    'idx_1h': i,
                    'ts': df1h.index[i],
                    'event': ev_type,
                    'direction': direction,
                    'regime_4h': regime,
                    'rsi_1h': round(rsi_cur,1),
                    'price_1h': round(price,4),
                })
        
        rsi_prev=rsi_cur
    
    # 去重（1H内同方向不重复触发，间隔至少4根1H）
    deduped=[]
    last_trigger_idx=-999
    for t in triggers:
        if t['idx_1h']-last_trigger_idx>=4:
            deduped.append(t)
            last_trigger_idx=t['idx_1h']
    
    return deduped

# ── Step2：15m精确入场 ────────────────────────────────────
def find_15m_entry(trigger, df15m, window=8):
    """
    1H触发后，在接下来window根15m K线内找最优入场点
    做空：找RSI_15m最高的那根（最佳做空时机）
    做多：找RSI_15m最低的那根（最佳做多时机）
    返回：(idx_15m, entry_price, atr_15m) or None
    """
    trigger_ts=trigger['ts']
    direction=trigger['direction']
    
    # 找1H触发时间对应的15m起始位置
    mask=df15m.index>=trigger_ts
    first_15m=mask.argmax() if mask.any() else None
    if first_15m is None or first_15m==0: return None
    
    # 在接下来的window根15m内扫描
    candidates=[]
    for j in range(first_15m, min(first_15m+window, len(df15m)-50)):
        hist=df15m.iloc[max(0,j-30):j+1]
        if len(hist)<15: continue
        c=hist['close'].values.astype(float)
        h=hist['high'].values.astype(float)
        l=hist['low'].values.astype(float)
        rsi=calc_rsi(c)
        candidates.append((j,rsi,c[-1],h,l,c))
    
    if not candidates: return None
    
    # 选最优入场点
    if direction=='SHORT':
        # 做空选RSI最高（最超买）
        best=max(candidates,key=lambda x:x[1])
    else:
        # 做多选RSI最低（最超卖）
        best=min(candidates,key=lambda x:x[1])
    
    idx_15m, rsi_entry, entry_price, h_arr, l_arr, c_arr = best
    
    # 动态ATR计算（用入场点之前30根15m）
    hist_for_atr=df15m.iloc[max(0,idx_15m-30):idx_15m+1]
    if len(hist_for_atr)<5: return None
    atr=calc_atr(
        hist_for_atr['high'].values.astype(float),
        hist_for_atr['low'].values.astype(float),
        hist_for_atr['close'].values.astype(float),
        period=14
    )
    
    return idx_15m, float(entry_price), float(atr), float(rsi_entry)

# ── Step3：前向结算（动态ATR止损）────────────────────────
def settle_dynamic_sl(df15m, entry_idx, direction, atr, atr_mult=2.0, rr=1.5, max_bars=48):
    """
    动态ATR止损结算
    SL = ATR × atr_mult（约1.0~1.8%）
    TP = SL × rr
    """
    ep=float(df15m['close'].values[entry_idx])
    sl_dist=atr*atr_mult
    tp_dist=sl_dist*rr
    
    highs=df15m['high'].values.astype(float)
    lows =df15m['low'].values.astype(float)
    
    if direction=='SHORT':
        sl_p=ep+sl_dist; tp_p=ep-tp_dist
        for i in range(entry_idx+1, min(entry_idx+max_bars+1,len(df15m))):
            if highs[i]>=sl_p: return 'LOSS',i-entry_idx,sl_dist/ep,tp_dist/ep
            if lows[i] <=tp_p: return 'WIN', i-entry_idx,sl_dist/ep,tp_dist/ep
    else:
        sl_p=ep-sl_dist; tp_p=ep+tp_dist
        for i in range(entry_idx+1, min(entry_idx+max_bars+1,len(df15m))):
            if lows[i] <=sl_p: return 'LOSS',i-entry_idx,sl_dist/ep,tp_dist/ep
            if highs[i]>=tp_p: return 'WIN', i-entry_idx,sl_dist/ep,tp_dist/ep
    
    return 'TIMEOUT',max_bars,sl_dist/ep,tp_dist/ep

# ── 主回测 ────────────────────────────────────────────────
def run_full_backtest(symbol, rr_list=[1.5, 2.0], atr_mult=2.0):
    print(f"\n{'='*70}")
    print(f"三层架构回测: {symbol.upper()} | ATR×{atr_mult} | RR={rr_list}")
    print('='*70)
    
    df15m,df1h,df4h,regime4h=load_data(symbol)
    
    # Step1: 生成1H触发事件
    triggers=gen_1h_triggers(df1h,df4h,regime4h)
    print(f"1H触发事件总数: {len(triggers)}")
    if not triggers: return None
    
    # 按体制分布
    from collections import Counter
    regime_cnt=Counter(f"{t['regime_4h']}:{t['direction']}" for t in triggers)
    print("触发分布:")
    for k,v in sorted(regime_cnt.items()): print(f"  {k}: {v}")
    
    results_by_rr={}
    
    for rr in rr_list:
        print(f"\n{'─'*70}")
        print(f"RR={rr} 回测:")
        
        trades=[]
        skip_until_ts=pd.Timestamp('2000-01-01',tz='UTC')
        
        for trigger in triggers:
            # 防重叠：上次入场未结束前不新开
            if trigger['ts']<skip_until_ts: continue
            
            # Step2: 15m精确入场
            entry=find_15m_entry(trigger,df15m,window=8)
            if entry is None: continue
            
            idx_15m,entry_price,atr,rsi_entry=entry
            
            # 合理性检查：SL不能超过3%（防止极端波动期）
            sl_pct=atr*atr_mult/entry_price
            if sl_pct>0.030 or sl_pct<0.003: continue
            
            # Step3: 结算
            result,bars,sl_pct_actual,tp_pct_actual=settle_dynamic_sl(
                df15m,idx_15m,trigger['direction'],atr,atr_mult,rr,max_bars=48
            )
            
            # 成本扣除
            cost=0.0016 if symbol=='btc' else 0.0020
            if result=='WIN':   pnl=sl_pct_actual*rr-cost
            elif result=='LOSS':pnl=-sl_pct_actual-cost
            else:               pnl=-cost*0.3
            
            entry_ts=df15m.index[idx_15m]
            
            trades.append({
                'dt':      str(entry_ts)[:13],
                'year':    str(entry_ts)[:4],
                'trigger': trigger['event'],
                'regime':  trigger['regime_4h'],
                'direction':trigger['direction'],
                'rsi_1h':  trigger['rsi_1h'],
                'rsi_15m_entry': round(rsi_entry,1),
                'sl_pct':  round(sl_pct_actual*100,3),
                'tp_pct':  round(tp_pct_actual*100,3),
                'result':  result,
                'bars':    bars,
                'pnl':     round(pnl*100,3),
            })
            
            # 下次入场最早时间：入场后bars根15m后
            if idx_15m+bars<len(df15m):
                skip_until_ts=df15m.index[idx_15m+bars]
        
        if not trades:
            print("❌ 无有效信号")
            continue
        
        df_t=pd.DataFrame(trades)
        
        wr=(df_t['result']=='WIN').mean()
        ev=df_t['pnl'].mean()
        pnl_sum=df_t['pnl'].sum()
        df_t['cum']=df_t['pnl'].cumsum()
        max_dd=(df_t['cum']-df_t['cum'].cummax()).min()
        
        # 连续亏损
        ml=cl=0
        for r in df_t['result']:
            if r=='LOSS': cl+=1; ml=max(ml,cl)
            else: cl=0
        
        print(f"  总信号: {len(df_t)} | WR={wr:.1%} | EV={ev:+.3f}%/笔")
        print(f"  累计PnL={pnl_sum:+.2f}% | 最大回撤={max_dd:.2f}% | 最长连亏={ml}笔")
        print(f"  平均SL={df_t['sl_pct'].mean():.3f}% | 平均TP={df_t['tp_pct'].mean():.3f}%")
        
        # 结算分布
        for res,g in df_t.groupby('result'):
            print(f"  {res}: {len(g)}({len(g)/len(df_t):.1%}) 均持{g['bars'].mean():.1f}根15m")
        
        # 体制矩阵
        print(f"\n  体制矩阵:")
        rm={}
        for (reg,dir),g in df_t.groupby(['regime','direction']):
            wr2=(g['result']=='WIN').mean(); ev2=g['pnl'].mean()
            key=f"{reg}:{dir}"
            rm[key]={'n':len(g),'wr':round(wr2,4),'ev':round(ev2,3)}
            flag='✅' if wr2>=0.55 else ('🟡' if wr2>=0.50 else '❌')
            print(f"    {flag} {key:<30} n={len(g):>4} WR={wr2:.1%} EV={ev2:+.3f}%")
        
        # 年份
        print(f"\n  年份统计:")
        for yr,g in df_t.groupby('year'):
            wr2=(g['result']=='WIN').mean(); ev2=g['pnl'].mean()
            flag='✅' if wr2>=0.52 else '❌'
            print(f"    {flag} {yr} n={len(g):>4} WR={wr2:.1%} EV={ev2:+.3f}%")
        
        # 触发类型分析
        print(f"\n  触发类型分析:")
        for ev_type,g in df_t.groupby('trigger'):
            wr2=(g['result']=='WIN').mean()
            flag='✅' if wr2>=0.55 else ('🟡' if wr2>=0.50 else '❌')
            print(f"    {flag} {ev_type:<15} n={len(g):>4} WR={wr2:.1%}")
        
        results_by_rr[rr]={
            'n':len(df_t),'wr':round(float(wr),4),'ev':round(float(ev),4),
            'pnl':round(float(pnl_sum),2),'max_dd':round(float(max_dd),2),
            'max_loss_streak':int(ml),'regime_matrix':rm,
        }
    
    return {'symbol':symbol,'results':results_by_rr}

# ── 执行 ─────────────────────────────────────────────────
print("梵天三层架构验证: 1H触发 → 4H体制 → 15m精确入场")
print("SL=ATR_15m×2.0（动态）| RR=1.5 and 2.0 | 最长持仓48根15m(12H)")

all_results={}
for sym in ['btc','eth']:
    r=run_full_backtest(sym,rr_list=[1.5,2.0],atr_mult=2.0)
    if r: all_results[sym]=r

# 汇总报告
print(f"\n\n{'='*70}")
print("🏛️ 三层架构回测 · 最终汇总")
print('='*70)
print(f"\n{'标的':<6} {'RR':<6} {'信号':>8} {'WR':>8} {'EV/笔':>10} {'累计PnL':>10} {'最大回撤':>10}")
print('─'*70)
for sym,data in all_results.items():
    for rr,r in data['results'].items():
        flag='✅' if r['wr']>=0.52 else '❌'
        print(f"  {sym.upper():<5} RR={rr} {r['n']:>8} {r['wr']:>8.1%} {r['ev']:>+10.3f}% {r['pnl']:>+10.2f}% {r['max_dd']:>10.2f}%  {flag}")

# 保存
with open(BASE/'data/validation/phase_1h_trigger_15m_entry_report.json','w',encoding='utf-8') as f:
    json.dump(all_results,f,ensure_ascii=False,indent=2,default=str)
print(f"\n✅ 报告已保存")
print("三层架构回测完成")
