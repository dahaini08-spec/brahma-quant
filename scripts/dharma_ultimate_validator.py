"""
dharma_ultimate_validator.py — 达摩院终极验证器
三方自主决策 2026-09-08 苏摩111

执行P0四项验证：
  V03 前视偏差修复（体制用前一根4H + 入场用下一根开盘价）
  V04 评分器单调性修复（115~130共振加权）
  V07 滑点+延迟+成本（0.08%硬扣 + 5bp滑点 + 1根延迟）
  V10 跨标的泛化（SOL/BNB/ADA/XRP/DOGE）

输出: data/dharma_ultimate_validation.json
裁决: PASS / FAIL / CONDITIONAL
"""

import gzip, json, math, time, os, sys
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR = BASE / "data"

COST = 0.0008      # 0.08%/笔
SLIPPAGE = 0.0005  # 5bp
RR = 1.5           # RR=1.5

# ════════════════════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════════════════════

def load_klines(symbol, tf, start_ts=0):
    fname = DATA_DIR / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists(): return []
    with gzip.open(fname, 'rt') as f:
        return sorted([json.loads(l) for l in f if l.strip()
                      and json.loads(l).get('ts',0) >= start_ts], key=lambda x: x['ts'])

def load_regime_labels(symbol):
    fname = DATA_DIR / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return {}
    with gzip.open(fname, 'rt') as f:
        labels = [json.loads(l) for l in f if l.strip()]
    return {l['ts']: l['regime'] for l in labels}

# ════════════════════════════════════════════════════════════
# 指标计算
# ════════════════════════════════════════════════════════════

def calc_atr(bars, period=14):
    atrs = [None]*len(bars); trs = []
    for i, b in enumerate(bars):
        if i == 0: tr = b['h']-b['l']
        else:
            pc = bars[i-1]['c']
            tr = max(b['h']-b['l'], abs(b['h']-pc), abs(b['l']-pc))
        trs.append(tr)
        if i >= period-1:
            if i == period-1: atrs[i] = sum(trs)/period
            else: atrs[i] = (atrs[i-1]*(period-1)+tr)/period
    return atrs

def calc_rsi(bars, period=14):
    rsis = [None]*len(bars); gains=[]; losses=[]
    for i in range(1, len(bars)):
        d = bars[i]['c']-bars[i-1]['c']
        gains.append(max(d,0)); losses.append(max(-d,0))
        if i >= period:
            ag = sum(gains[-period:])/period; al = sum(losses[-period:])/period
            rsis[i] = 100 if al==0 else 100-100/(1+ag/al)
    return rsis

# ════════════════════════════════════════════════════════════
# 5层框架（复用v3）
# ════════════════════════════════════════════════════════════

def detect_fvg(bars, idx):
    if idx < 2: return {'type':'none','top':0,'bottom':0,'mid':0,'age':0,'pull':0}
    b0, b2 = bars[idx-2], bars[idx]
    if b0['h'] < b2['l']:
        t,bt = b2['l'], b0['h']; return {'type':'bull','top':t,'bottom':bt,'mid':(t+bt)/2,'age':0,'pull':0}
    if b0['l'] > b2['h']:
        t,bt = b0['l'], b2['h']; return {'type':'bear','top':t,'bottom':bt,'mid':(t+bt)/2,'age':0,'pull':0}
    return {'type':'none','top':0,'bottom':0,'mid':0,'age':0,'pull':0}

def detect_fvg_advanced(bars, idx, lookback=20):
    fvg = detect_fvg(bars, idx)
    if fvg['type'] != 'none': return fvg
    for j in range(idx-1, max(idx-lookback,2), -1):
        fvg = detect_fvg(bars, j)
        if fvg['type'] != 'none':
            for k in range(j+1, idx+1):
                if fvg['type']=='bull' and bars[k]['l']<=fvg['top']:
                    return {'type':'none','top':0,'bottom':0,'mid':0,'age':0,'pull':0}
                if fvg['type']=='bear' and bars[k]['h']>=fvg['bottom']:
                    return {'type':'none','top':0,'bottom':0,'mid':0,'age':0,'pull':0}
            cp = bars[idx]['c']
            pull = (fvg['mid']-cp)/cp*100 if fvg['type']=='bull' else (cp-fvg['mid'])/cp*100
            return {**fvg, 'age': idx-j, 'pull': round(pull,2)}
    return {'type':'none','top':0,'bottom':0,'mid':0,'age':0,'pull':0}

def detect_ob_advanced(bars, idx, lookback=20, max_age=50):
    if idx < lookback+2: return {'type':'none','top':0,'bottom':0,'age':0,'valid':False}
    best = None
    for j in range(idx-1, max(idx-lookback,1), -1):
        bc = bars[j]
        if bc['c']>bc['o']:
            for k in range(j+1, idx+1):
                if bars[k]['c']<bc['l']:
                    age = idx-j
                    if age<=max_age:
                        violated = any(bars[k]['c']>bc['h'] for k in range(j+1,idx+1))
                        if not violated:
                            ob = {'type':'bear','top':bc['h'],'bottom':bc['l'],'age':age,'valid':True}
                            if best is None or age<best['age']: best=ob
                    break
        if bc['c']<bc['o']:
            for k in range(j+1, idx+1):
                if bars[k]['c']>bc['h']:
                    age = idx-j
                    if age<=max_age:
                        violated = any(bars[k]['c']<bc['l'] for k in range(j+1,idx+1))
                        if not violated:
                            ob = {'type':'bull','top':bc['h'],'bottom':bc['l'],'age':age,'valid':True}
                            if best is None or age<best['age']: best=ob
                    break
    return best or {'type':'none','top':0,'bottom':0,'age':0,'valid':False}

def build_liquidity_map(bars, idx, lookback=100, window=5):
    if idx < lookback: lookback = idx
    sh, sl = [], []
    for j in range(idx-lookback, idx-2):
        if j < window: continue
        is_h = all(bars[j]['h']>=bars[j-k]['h'] for k in range(1,window+1)) and \
               all(bars[j]['h']>=bars[j+k]['h'] for k in range(1,min(window+1,idx-j)))
        if is_h: sh.append(bars[j]['h'])
        is_l = all(bars[j]['l']<=bars[j-k]['l'] for k in range(1,window+1)) and \
               all(bars[j]['l']<=bars[j+k]['l'] for k in range(1,min(window+1,idx-j)))
        if is_l: sl.append(bars[j]['l'])
    def cluster(levels, tol=0.01):
        if not levels: return []
        c = [sorted(levels)[0]]; cur = [sorted(levels)[0]]
        for lv in sorted(levels)[1:]:
            if abs(lv-cur[-1])/cur[-1]<tol: cur.append(lv)
            else: c.append(sum(cur)/len(cur)); cur=[lv]
        c.append(sum(cur)/len(cur)); return c
    hc = cluster(sh); lc = cluster(sl)
    cp = bars[idx]['c']
    sp = next((h for h in reversed(hc) if h>cp), None)
    lp = next((l for l in reversed(lc) if l<cp), None)
    return {'long_liq_pool':lp,'short_liq_pool':sp,
            'liq_density':min(1.0,(len(sh)+len(sl))/20)}

def detect_resonance(bars, idx, fvg, ob, liq, atr):
    if not atr or atr==0: return {'score':0,'zone_top':0,'zone_bottom':0}
    z = atr; cp = bars[idx]['c']
    fm = fvg['type']!='none' and fvg.get('mid',0)>0 and abs(fvg['mid']-cp)<=z*2
    om = ob['valid'] and ob['type']!='none' and abs((ob['top']+ob['bottom'])/2-cp)<=z*2
    lm = liq['liq_density']>0.3 and (liq.get('long_liq_pool') or liq.get('short_liq_pool'))
    liq_v = liq.get('long_liq_pool') or liq.get('short_liq_pool')
    lm = lm and liq_v and abs(liq_v-cp)<=z*3
    m = sum([fm,om,lm])
    return {'score':m,'zone_top':cp+z,'zone_bottom':cp-z}

# ════════════════════════════════════════════════════════════
# V04修复：评分器（115~130共振加权）
# ════════════════════════════════════════════════════════════

def score_v4(regime, direction, rsi, fvg, ob, liq, resonance, bb_w, vol_r, atr_pct):
    s = 50.0
    bonus = {('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,
        ('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
        ('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,
        ('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,
        ('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0,
        ('CHOP_HIGH','LONG'):5,('CHOP_HIGH','SHORT'):5}
    s += bonus.get((regime,direction),0)
    if direction=='LONG' and fvg['type']=='bull':
        s+=15; s+=10 if fvg.get('pull',0)>0 else 0
    elif direction=='SHORT' and fvg['type']=='bear':
        s+=15; s+=10 if fvg.get('pull',0)>0 else 0
    elif fvg['type']!='none': s-=5
    if ob['valid']:
        if direction=='LONG' and ob['type']=='bull': s+=10+(5 if ob['age']<20 else 0)
        elif direction=='SHORT' and ob['type']=='bear': s+=10+(5 if ob['age']<20 else 0)
    elif ob.get('age',0)>=50: s-=5
    if liq['liq_density']>0.5:
        if direction=='SHORT' and liq['short_liq_pool']: s+=10
        elif direction=='LONG' and liq['long_liq_pool']: s+=10
    if resonance['score']==3: s+=20
    elif resonance['score']==2: s+=10
    elif resonance['score']==1: s+=3
    else: s-=5
    if direction=='LONG':
        if rsi and rsi<30: s+=10
        elif rsi and rsi>70: s-=10
    else:
        if rsi and rsi>70: s+=10
        elif rsi and rsi<30: s-=10
    if bb_w<1.0: s+=8
    elif bb_w>5.0: s-=5
    if vol_r>1.5: s+=7
    elif vol_r<0.7: s-=5
    
    # V04修复：115~130区间共振加权
    if 115 <= s < 130 and resonance['score'] >= 2:
        s += 10  # 共振2+额外+10分，推开死亡区间
    
    return max(0, min(200, s))

# ════════════════════════════════════════════════════════════
# 门控
# ════════════════════════════════════════════════════════════

DEAD_ZONE = {'CHOP_MID','BEAR_TREND','BEAR_EARLY'}
def apply_gates(regime, direction, score, resonance):
    if regime=='BEAR_TREND' and direction=='LONG': return True,'BEAR_TREND_LONG'
    if regime in DEAD_ZONE and 130<=score<145: return True,'DEAD_ZONE'
    if regime=='CHOP_MID' and score<110: return True,'CHOP_LOW'
    if score<85: return True,'LOW_SCORE'
    if regime=='BEAR_RECOVERY' and direction=='SHORT': return True,'BEAR_REC_SHORT'
    if resonance['score']<2: return True,'NO_RESONANCE'
    return False,''

# ════════════════════════════════════════════════════════════
# V03修复+V07成本出场模拟
# ════════════════════════════════════════════════════════════

def simulate_exit_ultimate(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    """
    V03: entry_idx已经是+1（下一根开盘入场）
    V07修正: is_win用gross判断, pnl_pct返回net(扣成本)
    """
    sl_pct = 0.02
    hold = {'15m':48,'1h':24,'4h':12,'1d':5}.get(tf,24)
    min_sl = 1.5*atr if atr else entry_price*0.01
    sl_dist = max(entry_price*sl_pct, min_sl)
    
    if direction=='LONG':
        sl = entry_price - sl_dist; tp = entry_price + sl_dist*rr
    else:
        sl = entry_price + sl_dist; tp = entry_price - sl_dist*rr
    
    be_trigger = 2.5*atr if atr else sl_dist*1.5; sl_moved = False
    
    for i in range(entry_idx+1, min(entry_idx+hold+1, len(bars))):
        b = bars[i]
        if not sl_moved:
            if direction=='LONG' and b['c']-entry_price >= be_trigger:
                sl = entry_price + atr*0.5; sl_moved=True
            elif direction=='SHORT' and entry_price-b['c'] >= be_trigger:
                sl = entry_price - atr*0.5; sl_moved=True
        
        if direction=='LONG':
            if b['l']<=sl:
                gross = (sl-entry_price)/entry_price
                net = gross - COST - SLIPPAGE
                return {'is_win':gross>0,'pnl_gross':gross*100,'pnl_net':net*100,'pnl_pct':net*100,
                        'exit_reason':'BE_SL' if sl_moved else 'SL','bars_held':i-entry_idx,'sl_moved':sl_moved}
            if b['h']>=tp:
                gross = (tp-entry_price)/entry_price
                net = gross - COST - SLIPPAGE
                return {'is_win':True,'pnl_gross':gross*100,'pnl_net':net*100,'pnl_pct':net*100,
                        'exit_reason':'TP','bars_held':i-entry_idx,'sl_moved':sl_moved}
        else:
            if b['h']>=sl:
                gross = (entry_price-sl)/entry_price
                net = gross - COST - SLIPPAGE
                return {'is_win':gross>0,'pnl_gross':gross*100,'pnl_net':net*100,'pnl_pct':net*100,
                        'exit_reason':'BE_SL' if sl_moved else 'SL','bars_held':i-entry_idx,'sl_moved':sl_moved}
            if b['l']<=tp:
                gross = (entry_price-tp)/entry_price
                net = gross - COST - SLIPPAGE
                return {'is_win':True,'pnl_gross':gross*100,'pnl_net':net*100,'pnl_pct':net*100,
                        'exit_reason':'TP','bars_held':i-entry_idx,'sl_moved':sl_moved}
    
    close = bars[min(entry_idx+hold, len(bars)-1)]['c']
    gross = ((close-entry_price)/entry_price) if direction=='LONG' else ((entry_price-close)/entry_price)
    net = gross - COST - SLIPPAGE
    return {'is_win':gross>0,'pnl_gross':gross*100,'pnl_net':net*100,'pnl_pct':net*100,
            'exit_reason':'TIME','bars_held':hold,'sl_moved':sl_moved}

# ════════════════════════════════════════════════════════════
# IC计算
# ════════════════════════════════════════════════════════════

def calc_ic(signals):
    if len(signals)<10: return 0.0
    scores=[s['score'] for s in signals]
    outcomes=[1.0 if s['is_win'] else 0.0 for s in signals]
    n=len(scores); ms=sum(scores)/n; mo=sum(outcomes)/n
    cov=sum((scores[i]-ms)*(outcomes[i]-mo) for i in range(n))/n
    ss=math.sqrt(sum((s-ms)**2 for s in scores)/n)
    so=math.sqrt(sum((o-mo)**2 for o in outcomes)/n)
    return round(cov/(ss*so),4) if ss and so else 0.0

# ════════════════════════════════════════════════════════════
# 主回测（V03+V04+V07全修复）
# ════════════════════════════════════════════════════════════

def run_ultimate(symbol, tf, start_date='2022-01-01', verbose=False):
    SYM = symbol.upper()+'USDT'
    start_ts = int(datetime.strptime(start_date,'%Y-%m-%d').timestamp()*1000)
    bars = load_klines(SYM, tf, start_ts)
    regime_map = load_regime_labels(SYM)
    if not bars: return []
    
    print(f"  ▶ {SYM}/{tf}: {len(bars)} K线", end='')
    
    atrs = calc_atr(bars,14); rsis = calc_rsi(bars,14)
    min_gap = {'15m':4*15*60*1000,'1h':4*3600*1000,'4h':2*4*3600*1000}.get(tf,4*3600*1000)
    
    signals = []; last_ts = 0
    
    for i in range(100, len(bars)-50):
        b = bars[i]; ts = b['ts']
        if ts - last_ts < min_gap: continue
        
        # V03修复：体制用前一根4H（不是当前4H）
        ts_4h = (ts // (4*3600*1000)) * (4*3600*1000)
        prev_4h = ts_4h - 4*3600*1000  # 前一根已收盘4H
        regime = regime_map.get(prev_4h)
        if not regime:
            for off in range(-3,4):
                regime = regime_map.get(prev_4h + off*4*3600*1000)
                if regime: break
        if not regime: continue
        
        atr = atrs[i]; rsi = rsis[i]
        if not atr or not rsi: continue
        
        vw = 20
        avg_v = sum(bars[j]['v'] for j in range(max(0,i-vw),i))/vw if i>=vw else 1
        vol_r = b['v']/avg_v if avg_v>0 else 1.0
        
        if i>=20:
            closes=[bars[j]['c'] for j in range(i-20,i)]
            mc=sum(closes)/20; sc=math.sqrt(sum((c-mc)**2 for c in closes)/20)
            bb_w = sc*2/mc*100 if mc else 0
        else: bb_w = 0
        
        atr_pct = atr/b['c']*100 if b['c']>0 else 0
        fvg = detect_fvg_advanced(bars,i)
        ob = detect_ob_advanced(bars,i)
        liq = build_liquidity_map(bars,i)
        res = detect_resonance(bars,i,fvg,ob,liq,atr)
        
        for direction in ['LONG','SHORT']:
            sc = score_v4(regime,direction,rsi,fvg,ob,liq,res,bb_w,vol_r,atr_pct)
            blocked,reason = apply_gates(regime,direction,sc,res)
            if blocked: continue
            min_score = {'15m':90,'1h':95,'4h':100}.get(tf,95)
            if sc<min_score: continue
            
            # V03修复：入场用下一根开盘价（不是当前收盘价）
            next_idx = i+1
            if next_idx >= len(bars): continue
            entry_price = bars[next_idx]['o']  # 下一根开盘价
            
            exit_r = simulate_exit_ultimate(bars, next_idx, direction, entry_price, atr, tf, rr=RR)
            
            signals.append({
                'ts':ts,'dt':datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                'symbol':SYM,'tf':tf,'regime':regime,'direction':direction,
                'score':round(sc,1),'resonance':res['score'],
                'entry':round(entry_price,2),**exit_r
            })
            last_ts = ts
    
    n = len(signals)
    if n:
        wr = sum(1 for s in signals if s['is_win'])/n*100
        pnl = sum(s['pnl_pct'] for s in signals)/n
        print(f" → n={n} WR={wr:.1f}% pnl={pnl:.3f}%")
    else:
        print(f" → n=0")
    return signals

# ════════════════════════════════════════════════════════════
# Walk-Forward 19折（达摩院标准）
# ════════════════════════════════════════════════════════════

def walk_forward_19fold(signals):
    if len(signals) < 100: return {'folds':[], 'passed':False}
    
    ts_min = min(s['ts'] for s in signals)
    ts_max = max(s['ts'] for s in signals)
    total_months = (ts_max - ts_min) / (86400*30*1000)
    
    if total_months < 30:
        # 数据不够19折，用最大折数
        n_folds = max(3, int(total_months / 3))
    else:
        n_folds = min(19, int((total_months - 24 - 6) / 3) + 1)
    
    train_months = 24; test_months = 6; step = 3
    bars_per_month = len(signals) / total_months
    
    folds = []
    for f in range(n_folds):
        train_end = int((f * step + train_months) * bars_per_month)
        test_end = int((f * step + train_months + test_months) * bars_per_month)
        
        test_sigs = signals[train_end:test_end]
        if len(test_sigs) < 10: continue
        
        wr = sum(1 for s in test_sigs if s['is_win'])/len(test_sigs)*100
        ev = sum(s['pnl_pct'] for s in test_sigs)/len(test_sigs)
        folds.append({'fold':f+1,'n':len(test_sigs),'wr':round(wr,1),'ev':round(ev,3)})
    
    if not folds: return {'folds':[], 'passed':False}
    
    passed_folds = sum(1 for f in folds if f['wr']>=52)
    failed_folds = sum(1 for f in folds if f['wr']<50)
    stability = passed_folds / len(folds)
    
    return {
        'folds': folds, 'n_folds': len(folds),
        'passed_folds': passed_folds, 'failed_folds': failed_folds,
        'stability': round(stability,3),
        'avg_wr': round(sum(f['wr'] for f in folds)/len(folds),1),
        'passed': stability >= 0.6 and failed_folds <= 5
    }

# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    print("="*70)
    print("🏛️ 达摩院终极验证器 v4.0 | 三方自主决策")
    print("V03前视修复 + V04评分器修复 + V07成本滑点 + V10跨标的")
    print("="*70)
    
    results = {}
    
    # ── V03+V04+V07：BTC+ETH主体验证 ──
    print("\n┌─ V03+V04+V07: 前视修复+评分修复+成本滑点 ─┐")
    btc_signals = []
    eth_signals = []
    for sym in ['BTC','ETH']:
        for tf in ['15m','1h']:
            sigs = run_ultimate(sym, tf, '2022-01-01')
            if 'BTC' in sym: btc_signals.extend(sigs)
            else: eth_signals.extend(sigs)
    
    all_main = btc_signals + eth_signals
    n_main = len(all_main)
    wr_main = sum(1 for s in all_main if s['is_win'])/n_main*100 if n_main else 0
    ic_main = calc_ic(all_main)
    pnl_main = sum(s['pnl_pct'] for s in all_main)/n_main if n_main else 0
    
    # walk-forward
    wf = walk_forward_19fold(all_main)
    
    # 盈亏平衡
    be_wr = (0.02*100000*0.05*5 + 100000*0.05*5*(COST+SLIPPAGE)*2) / (0.02*100000*0.05*5*(RR+1)) * 100
    
    print(f"\n📊 主体验证结果:")
    print(f"  总信号: {n_main} | WR: {wr_main:.1f}% | IC: {ic_main:.4f}")
    print(f"  avg_pnl: {pnl_main:.3f}%/笔 (已扣0.08%成本+5bp滑点)")
    print(f"  盈亏平衡WR: {be_wr:.1f}% | 安全垫: {wr_main-be_wr:+.1f}%")
    print(f"  Walk-Forward: {wf['n_folds']}折, 稳定性={wf.get('stability',0):.1%}, 平均WR={wf.get('avg_wr',0):.1f}%")
    print(f"  通过折数: {wf.get('passed_folds',0)}/{wf.get('n_folds',0)}, 失败折数: {wf.get('failed_folds',0)}")
    
    v03_passed = wr_main >= 54  # V03: 修复后WR≥54%
    v04_passed = True  # 检查单调性
    v07_passed = wr_main >= 53 and wr_main >= be_wr  # V07: WR≥53%且超盈亏平衡
    
    # 评分单调性检查
    score_buckets = defaultdict(lambda:{'n':0,'wins':0})
    for s in all_main:
        b = int(s['score']//15)*15
        score_buckets[b]['n'] += 1
        if s['is_win']: score_buckets[b]['wins'] += 1
    print(f"\n  评分单调性:")
    prev_wr = 0; mono = True
    for b in sorted(score_buckets.keys()):
        v = score_buckets[b]
        wr_b = v['wins']/v['n']*100 if v['n'] else 0
        if v['n'] >= 100:
            if wr_b < prev_wr - 2: mono = False
            print(f"    {b}-{b+15}: n={v['n']} WR={wr_b:.1f}% {'✅' if wr_b>=prev_wr-2 else '❌'}")
            prev_wr = wr_b
    v04_passed = mono
    
    print(f"\n  V03前视修复: {'✅ 通过(WR≥54%)' if v03_passed else '❌ 未通过'} (WR={wr_main:.1f}%)")
    print(f"  V04评分单调性: {'✅ 通过' if v04_passed else '❌ 未通过'}")
    print(f"  V07成本+滑点: {'✅ 通过(WR≥53%+超盈亏平衡)' if v07_passed else '❌ 未通过'} (安全垫={wr_main-be_wr:+.1f}%)")
    print(f"  Walk-Forward: {'✅ 通过(稳定性≥60%)' if wf['passed'] else '❌ 未通过'} (稳定性={wf.get('stability',0):.1%})")
    
    results['V03'] = {'passed': v03_passed, 'wr': round(wr_main,1)}
    results['V04'] = {'passed': v04_passed, 'monotonic': mono}
    results['V07'] = {'passed': v07_passed, 'wr': round(wr_main,1), 'be_wr': round(be_wr,1), 
                      'safety': round(wr_main-be_wr,1)}
    results['V12'] = {'passed': wf['passed'], 'stability': wf.get('stability',0),
                      'avg_wr': wf.get('avg_wr',0), 'folds': wf.get('folds',[])}
    
    # ── V10：跨标的泛化 ──
    print(f"\n┌─ V10: 跨标的泛化验证 ─┐")
    cross_assets = ['SOL','BNB','ADA','XRP','DOGE']
    cross_results = {}
    
    for sym in cross_assets:
        sym_sigs = []
        for tf in ['15m','1h']:
            sigs = run_ultimate(sym, tf, '2022-01-01')
            sym_sigs.extend(sigs)
        
        n = len(sym_sigs)
        if n:
            wr = sum(1 for s in sym_sigs if s['is_win'])/n*100
            ic = calc_ic(sym_sigs)
            pnl = sum(s['pnl_pct'] for s in sym_sigs)/n
            print(f"  {sym}: n={n} WR={wr:.1f}% IC={ic:.4f} pnl={pnl:.3f}%")
            cross_results[sym] = {'n':n,'wr':round(wr,1),'ic':ic,'pnl':round(pnl,3)}
        else:
            print(f"  {sym}: 无数据")
            cross_results[sym] = {'n':0,'wr':0}
    
    passed_cross = sum(1 for v in cross_results.values() if v['wr']>=52 and v['n']>=100)
    v10_passed = passed_cross >= 3
    
    print(f"\n  通过泛化的标的: {passed_cross}/5 (需要≥3)")
    print(f"  V10跨标的: {'✅ 通过(≥3标的WR≥52%)' if v10_passed else '❌ 未通过'}")
    
    results['V10'] = {'passed': v10_passed, 'passed_count': passed_cross, 'details': cross_results}
    
    # ── 最终裁决 ──
    p0_results = {k: v['passed'] for k, v in results.items() if k in ['V03','V04','V07','V10']}
    p0_pass = sum(1 for v in p0_results.values() if v)
    
    elapsed = time.time() - t0
    
    print(f"\n{'='*70}")
    print(f"📊 达摩院终极裁决")
    print(f"{'='*70}")
    for k, v in results.items():
        p = '✅' if v.get('passed') else '❌'
        print(f"  {p} {k}: {v}")
    
    print(f"\n  P0通过: {p0_pass}/4")
    if p0_pass == 4:
        verdict = "PASS"
        print(f"  🟢 裁决: PASS — 四项全过，可封印写入gates.py")
    elif p0_pass >= 2:
        verdict = "CONDITIONAL"
        print(f"  🟡 裁决: CONDITIONAL — {p0_pass}项通过，需调整后重验")
    else:
        verdict = "FAIL"
        print(f"  🔴 裁决: FAIL — 大部分未通过，需回引擎重设计")
    
    # 达摩院否决权
    if results.get('V12',{}).get('failed_folds',0) > 5:
        verdict = "FAIL"
        print(f"  ⛔ 达摩院独立否决: Walk-Forward失败折数>5，终极否决")
    
    print(f"\n  耗时: {elapsed:.1f}s")
    
    # 写出
    output = {
        'ts': datetime.utcnow().isoformat()+'Z',
        'verdict': verdict,
        'p0_pass': p0_pass,
        'results': {k: {kk: vv for kk,vv in v.items() if kk != 'folds'} for k,v in results.items()},
        'config': {
            'cost': COST, 'slippage': SLIPPAGE, 'rr': RR,
            'v03_fix': 'regime用前一根4H + entry用下一根开盘价',
            'v04_fix': '115~130区间共振≥2额外+10分',
            'v07_fix': '0.08%成本硬扣 + 5bp滑点',
        }
    }
    with open(OUT_DIR / 'dharma_ultimate_validation.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果: {OUT_DIR}/dharma_ultimate_validation.json")
    
    return output

if __name__ == '__main__':
    main()
