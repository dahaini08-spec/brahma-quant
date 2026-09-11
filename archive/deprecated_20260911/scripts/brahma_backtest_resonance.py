"""
brahma_backtest_resonance.py — 三方联合4步验证引擎
苏摩111 2026-09-08 封印

Step1: 多周期共振（1H确认15M）
Step2: 策略过滤（只做已证明赚钱的策略）
Step3: RR=1.5真实出场模拟
Step4: walk-forward样本外验证
"""

import gzip, json, os, sys, math, time, argparse
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR = BASE / "data"

# ── 数据加载 ─────────────────────────────────────────────────

def load_klines(symbol, tf, start_ts=0):
    fname = DATA_DIR / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists(): return []
    with gzip.open(fname, 'rt') as f:
        return [json.loads(l) for l in f if l.strip() and json.loads(l).get('ts',0) >= start_ts]

def load_regime_labels(symbol):
    fname = DATA_DIR / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return {}
    with gzip.open(fname, 'rt') as f:
        labels = [json.loads(l) for l in f if l.strip()]
    return {l['ts']: l['regime'] for l in labels}

# ── 指标 ──────────────────────────────────────────────────────

def calc_atr(bars, period=14):
    atrs = [None]*len(bars)
    trs = []
    for i, b in enumerate(bars):
        if i == 0: tr = b['h'] - b['l']
        else:
            pc = bars[i-1]['c']
            tr = max(b['h']-b['l'], abs(b['h']-pc), abs(b['l']-pc))
        trs.append(tr)
        if i >= period-1:
            if i == period-1: atrs[i] = sum(trs)/period
            else: atrs[i] = (atrs[i-1]*(period-1)+tr)/period
    return atrs

def calc_rsi(bars, period=14):
    rsis = [None]*len(bars)
    gains, losses = [], []
    for i in range(1, len(bars)):
        d = bars[i]['c'] - bars[i-1]['c']
        gains.append(max(d,0)); losses.append(max(-d,0))
        if i >= period:
            ag = sum(gains[-period:])/period
            al = sum(losses[-period:])/period
            rsis[i] = 100 if al == 0 else 100 - 100/(1+ag/al)
    return rsis

def calc_ema(bars, period, key='c'):
    emas = [None]*len(bars)
    k = 2/(period+1)
    for i, b in enumerate(bars):
        if i == 0: emas[i] = b[key]
        elif emas[i-1] is None: emas[i] = b[key]
        else: emas[i] = b[key]*k + emas[i-1]*(1-k)
    return emas

def detect_fvg(bars, idx):
    if idx < 2: return {'type':'none','top':0,'bottom':0,'mid':0}
    b0, b2 = bars[idx-2], bars[idx]
    if b0['h'] < b2['l']:
        top, bot = b2['l'], b0['h']
        return {'type':'bull','top':top,'bottom':bot,'mid':(top+bot)/2}
    if b0['l'] > b2['h']:
        top, bot = b0['l'], b2['h']
        return {'type':'bear','top':top,'bottom':bot,'mid':(top+bot)/2}
    return {'type':'none','top':0,'bottom':0,'mid':0}

def detect_ob(bars, idx, lookback=5):
    if idx < lookback+2: return {'type':'none','top':0,'bottom':0,'age':0}
    for j in range(idx-1, max(idx-lookback-1,1), -1):
        bc = bars[j]
        if bc['c'] > bc['o'] and bars[idx]['c'] < bc['l']:
            return {'type':'bear','top':bc['h'],'bottom':bc['l'],'age':idx-j}
        if bc['c'] < bc['o'] and bars[idx]['c'] > bc['h']:
            return {'type':'bull','top':bc['h'],'bottom':bc['l'],'age':idx-j}
    return {'type':'none','top':0,'bottom':0,'age':0}

def calc_bb_width(bars, idx, period=20):
    if idx < period: return 0.0
    closes = [b['c'] for b in bars[idx-period:idx]]
    mean = sum(closes)/period
    std = math.sqrt(sum((c-mean)**2 for c in closes)/period)
    return std*2/mean*100 if mean else 0.0

# ── 评分 ──────────────────────────────────────────────────────

def score_signal(regime, direction, rsi, fvg, ob, bb_w, vol_ratio):
    s = 60.0
    bonus = {
        ('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,
        ('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
        ('BULL_EARLY','LONG'):20,('BULL_EARLY','SHORT'):-10,
        ('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,
        ('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0,
        ('CHOP_HIGH','LONG'):5,('CHOP_HIGH','SHORT'):5,
    }
    s += bonus.get((regime,direction),0)
    if direction=='LONG' and fvg['type']=='bull': s+=15
    elif direction=='SHORT' and fvg['type']=='bear': s+=15
    elif fvg['type']!='none': s-=5
    age = ob.get('age',99)
    if direction=='LONG' and ob['type']=='bull' and age<20: s+=10
    elif direction=='SHORT' and ob['type']=='bear' and age<20: s+=10
    elif age>=50: s-=5
    if direction=='LONG':
        if rsi and rsi<30: s+=10
        elif rsi and rsi>70: s-=10
    else:
        if rsi and rsi>70: s+=10
        elif rsi and rsi<30: s-=10
    if bb_w<1.0: s+=8
    elif bb_w>5.0: s-=5
    if vol_ratio>1.5: s+=7
    elif vol_ratio<0.7: s-=5
    return max(0, min(200, s))

# ── 门控 ──────────────────────────────────────────────────────

DEAD_ZONE = {'CHOP_MID','BEAR_TREND','BEAR_EARLY'}

def apply_gates(regime, direction, score):
    if regime=='BEAR_TREND' and direction=='LONG': return True
    if regime in DEAD_ZONE and 130<=score<145: return True
    if regime=='CHOP_MID' and score<110: return True
    if score<85: return True
    if regime=='BEAR_RECOVERY' and direction=='SHORT': return True
    return False

# ── 出场模拟 ──────────────────────────────────────────────────

def simulate_exit(bars, entry_idx, direction, entry_price, atr, regime, tf, rr=1.0):
    sl_pct = 0.02  # 2% SL（统一）
    hold = {'15m':48,'1h':24,'4h':12,'1d':5}.get(tf,12)
    min_sl = 1.5*atr if atr else entry_price*0.01
    sl_dist = max(entry_price*sl_pct, min_sl)
    
    if direction=='LONG':
        sl = entry_price - sl_dist
        tp = entry_price + sl_dist*rr
    else:
        sl = entry_price + sl_dist
        tp = entry_price - sl_dist*rr
    
    for i in range(entry_idx+1, min(entry_idx+hold+1, len(bars))):
        b = bars[i]
        if direction=='LONG':
            if b['l']<=sl: return {'is_win':False,'pnl_pct':-sl_dist/entry_price*100,'exit_reason':'SL','bars_held':i-entry_idx}
            if b['h']>=tp: return {'is_win':True,'pnl_pct':(sl_dist*rr)/entry_price*100,'exit_reason':'TP','bars_held':i-entry_idx}
        else:
            if b['h']>=sl: return {'is_win':False,'pnl_pct':-sl_dist/entry_price*100,'exit_reason':'SL','bars_held':i-entry_idx}
            if b['l']<=tp: return {'is_win':True,'pnl_pct':(sl_dist*rr)/entry_price*100,'exit_reason':'TP','bars_held':i-entry_idx}
    
    close = bars[min(entry_idx+hold,len(bars)-1)]['c']
    pnl = ((close-entry_price)/entry_price*100) if direction=='LONG' else ((entry_price-close)/entry_price*100)
    return {'is_win':pnl>0,'pnl_pct':pnl,'exit_reason':'TIME','bars_held':hold}

# ── 信号生成器 ────────────────────────────────────────────────

def generate_signals(bars, regime_map, tf, min_score=90, start_ts=0):
    """生成单周期信号列表"""
    atrs = calc_atr(bars, 14)
    rsis = calc_rsi(bars, 14)
    signals = []
    min_gap = {'15m':4*15*60*1000,'1h':4*3600*1000,'4h':2*4*3600*1000,'1d':2*24*3600*1000}.get(tf,4*3600*1000)
    last_ts = 0
    
    for i in range(50, len(bars)-50):
        b = bars[i]
        ts = b['ts']
        if ts - last_ts < min_gap: continue
        if ts < start_ts: continue
        
        ts_4h = (ts // (4*3600*1000)) * (4*3600*1000)
        regime = regime_map.get(ts_4h)
        if not regime:
            for off in range(-3,4):
                regime = regime_map.get(ts_4h + off*4*3600*1000)
                if regime: break
        if not regime: continue
        
        atr = atrs[i]
        rsi = rsis[i]
        if not atr or not rsi: continue
        
        vw = 20
        if i >= vw:
            avg_v = sum(bars[j]['v'] for j in range(i-vw,i))/vw
            vol_r = b['v']/avg_v if avg_v>0 else 1.0
        else: vol_r = 1.0
        
        fvg = detect_fvg(bars, i)
        ob = detect_ob(bars, i, 5)
        bb_w = calc_bb_width(bars, i)
        
        for direction in ['LONG','SHORT']:
            sc = score_signal(regime, direction, rsi, fvg, ob, bb_w, vol_r)
            if apply_gates(regime, direction, sc): continue
            if sc < min_score: continue
            
            signals.append({
                'ts': ts, 'idx': i, 'dt': datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                'regime': regime, 'direction': direction, 'score': round(sc,1),
                'entry': b['c'], 'fvg_type': fvg['type'], 'atr': atr,
                'tf': tf
            })
            last_ts = ts
    
    return signals

# ── Step1: 多周期共振 ────────────────────────────────────────

def step1_resonance(symbols, start_date='2022-01-01'):
    """1H信号 ± 15M方向确认 = 双周期共振"""
    print("\n" + "="*60)
    print("Step1: 多周期共振验证（1H确认15M）")
    print("="*60)
    
    start_ts = int(datetime.strptime(start_date,'%Y-%m-%d').timestamp()*1000)
    all_resonance = []
    all_single = []
    
    for sym in symbols:
        SYM = sym.upper()+'USDT'
        print(f"\n▶ {SYM}")
        
        bars_1h = load_klines(SYM, '1h', start_ts)
        bars_15m = load_klines(SYM, '15m', start_ts)
        regime_map = load_regime_labels(SYM)
        
        if not bars_1h or not bars_15m: continue
        
        # 1H信号
        sigs_1h = generate_signals(bars_1h, regime_map, '1h', min_score=100, start_ts=start_ts)
        print(f"  1H信号: {len(sigs_1h)} 笔")
        
        # 15m信号索引（按ts索引）
        sigs_15m = generate_signals(bars_15m, regime_map, '15m', min_score=90, start_ts=start_ts)
        print(f"  15M信号: {len(sigs_15m)} 笔")
        
        # 15m信号按时间索引
        sig15m_by_ts = defaultdict(list)
        for s in sigs_15m:
            sig15m_by_ts[s['ts']].append(s)
        
        # 共振匹配：在1H信号 ±4根15m K线（±1小时）内，找方向一致的15m信号
        resonance_count = 0
        for s1h in sigs_1h:
            ts_1h = s1h['ts']
            # ±4根15m = ±60分钟
            for offset in range(-4, 5):
                ts_15m = ts_1h + offset * 15 * 60 * 1000
                for s15m in sig15m_by_ts.get(ts_15m, []):
                    if s15m['direction'] == s1h['direction']:
                        # 共振信号！用1H的入场点
                        exit_1h = simulate_exit(bars_1h, s1h['idx'], s1h['direction'],
                                                s1h['entry'], s1h['atr'], s1h['regime'], '1h', rr=1.0)
                        all_resonance.append({
                            **s1h, **exit_1h,
                            'resonance': True, 'confirm_tf': '15m'
                        })
                        resonance_count += 1
                        break
                else: continue
                break
        
        # 非共振1H信号（用于对比）
        for s1h in sigs_1h:
            has_resonance = any(r['ts'] == s1h['ts'] for r in all_resonance if r.get('symbol','') == SYM)
            if not has_resonance:
                exit_1h = simulate_exit(bars_1h, s1h['idx'], s1h['direction'],
                                        s1h['entry'], s1h['atr'], s1h['regime'], '1h', rr=1.0)
                all_single.append({**s1h, **exit_1h, 'resonance': False})
        
        print(f"  共振信号: {resonance_count} 笔 (过滤率: {(1-resonance_count/max(len(sigs_1h),1))*100:.0f}%)")
    
    # 统计
    if all_resonance:
        wr_r = sum(1 for s in all_resonance if s['is_win'])/len(all_resonance)*100
        pnl_r = sum(s['pnl_pct'] for s in all_resonance)/len(all_resonance)
        ic_r = calc_ic(all_resonance)
    else:
        wr_r = pnl_r = ic_r = 0
    
    if all_single:
        wr_s = sum(1 for s in all_single if s['is_win'])/len(all_single)*100
        pnl_s = sum(s['pnl_pct'] for s in all_single)/len(all_single)
        ic_s = calc_ic(all_single)
    else:
        wr_s = pnl_s = ic_s = 0
    
    print(f"\n📊 Step1 结果:")
    print(f"  共振信号: n={len(all_resonance)} WR={wr_r:.1f}% avg_pnl={pnl_r:.3f}% IC={ic_r:.4f}")
    print(f"  非共振:   n={len(all_single)} WR={wr_s:.1f}% avg_pnl={pnl_s:.3f}% IC={ic_s:.4f}")
    print(f"  WR提升: +{wr_r-wr_s:.1f}%" if all_resonance and all_single else "")
    print(f"  {'✅ 通过(≥55%)' if wr_r>=55 else '⚠️ 未通过(<55%)'}")
    
    return {'resonance': all_resonance, 'single': all_single,
            'wr_r': wr_r, 'wr_s': wr_s, 'ic_r': ic_r, 'ic_s': ic_s}

# ── Step2: 策略过滤 ──────────────────────────────────────────

def step2_strategy_filter(symbols, start_date='2022-01-01'):
    """只做已证明赚钱的策略：BEAR_EARLY:LONG + BULL_TREND:LONG"""
    print("\n" + "="*60)
    print("Step2: 策略过滤验证（只做赚钱策略）")
    print("="*60)
    
    start_ts = int(datetime.strptime(start_date,'%Y-%m-%d').timestamp()*1000)
    GOOD = {('BEAR_EARLY','LONG'), ('BULL_TREND','LONG')}
    BAD = {('BEAR_EARLY','SHORT'), ('BEAR_RECOVERY','LONG'), ('BEAR_RECOVERY','SHORT')}
    
    all_good = []
    all_bad = []
    all_neutral = []
    
    for sym in symbols:
        SYM = sym.upper()+'USDT'
        for tf in ['15m','1h']:
            bars = load_klines(SYM, tf, start_ts)
            regime_map = load_regime_labels(SYM)
            if not bars: continue
            sigs = generate_signals(bars, regime_map, tf, min_score=90, start_ts=start_ts)
            
            for s in sigs:
                exit_r = simulate_exit(bars, s['idx'], s['direction'], s['entry'], s['atr'], s['regime'], tf, rr=1.0)
                s.update(exit_r)
                key = (s['regime'], s['direction'])
                if key in GOOD: all_good.append(s)
                elif key in BAD: all_bad.append(s)
                else: all_neutral.append(s)
    
    def stats(lst):
        if not lst: return 0,0,0
        wr = sum(1 for s in lst if s['is_win'])/len(lst)*100
        pnl = sum(s['pnl_pct'] for s in lst)/len(lst)
        ic = calc_ic(lst)
        return wr, pnl, ic
    
    wr_g, pnl_g, ic_g = stats(all_good)
    wr_b, pnl_b, ic_b = stats(all_bad)
    wr_n, pnl_n, ic_n = stats(all_neutral)
    
    print(f"\n  🟢 赚钱策略(BEAR_EARLY:LONG+BULL_TREND:LONG): n={len(all_good)} WR={wr_g:.1f}% pnl={pnl_g:.3f}% IC={ic_g:.4f}")
    print(f"  ❌ 亏钱策略(BEAR_EARLY:SHORT+BEAR_RECOVERY):   n={len(all_bad)} WR={wr_b:.1f}% pnl={pnl_b:.3f}% IC={ic_b:.4f}")
    print(f"  🟡 中性策略(其他):                             n={len(all_neutral)} WR={wr_n:.1f}% pnl={pnl_n:.3f}% IC={ic_n:.4f}")
    print(f"\n  过滤后WR: {wr_g:.1f}% {'✅ 通过(≥55%)' if wr_g>=55 else '⚠️ 未通过'}")
    
    return {'good': all_good, 'bad': all_bad, 'neutral': all_neutral,
            'wr_g': wr_g, 'wr_b': wr_b, 'wr_n': wr_n}

# ── Step3: RR=1.5真实出场 ────────────────────────────────────

def step3_rr15(symbols, start_date='2022-01-01'):
    """RR=1.5真实出场模拟：TP=3%, SL=2%"""
    print("\n" + "="*60)
    print("Step3: RR=1.5真实出场模拟（TP=3%, SL=2%）")
    print("="*60)
    
    start_ts = int(datetime.strptime(start_date,'%Y-%m-%d').timestamp()*1000)
    all_rr10 = []
    all_rr15 = []
    all_rr20 = []
    
    for sym in symbols:
        SYM = sym.upper()+'USDT'
        for tf in ['15m','1h']:
            bars = load_klines(SYM, tf, start_ts)
            regime_map = load_regime_labels(SYM)
            if not bars: continue
            sigs = generate_signals(bars, regime_map, tf, min_score=100, start_ts=start_ts)
            
            for s in sigs:
                e10 = simulate_exit(bars, s['idx'], s['direction'], s['entry'], s['atr'], s['regime'], tf, rr=1.0)
                e15 = simulate_exit(bars, s['idx'], s['direction'], s['entry'], s['atr'], s['regime'], tf, rr=1.5)
                e20 = simulate_exit(bars, s['idx'], s['direction'], s['entry'], s['atr'], s['regime'], tf, rr=2.0)
                all_rr10.append({**s, **e10})
                all_rr15.append({**s, **e15})
                all_rr20.append({**s, **e20})
    
    def stats(lst, rr):
        if not lst: return 0,0,0,0
        wr = sum(1 for s in lst if s['is_win'])/len(lst)*100
        pnl = sum(s['pnl_pct'] for s in lst)/len(lst)
        ic = calc_ic(lst)
        # 盈亏平衡WR
        nav = 100000
        pos = nav * 0.05 * 5
        fee = pos * 0.0008 * 2
        sl = 0.02
        be_wr = (sl*pos + fee) / (sl*pos*(rr+1)) * 100
        return wr, pnl, ic, be_wr
    
    wr10, p10, i10, be10 = stats(all_rr10, 1.0)
    wr15, p15, i15, be15 = stats(all_rr15, 1.5)
    wr20, p20, i20, be20 = stats(all_rr20, 2.0)
    
    print(f"\n  RR=1.0: n={len(all_rr10)} WR={wr10:.1f}% 盈亏平衡={be10:.1f}% 安全垫={wr10-be10:+.1f}%")
    print(f"  RR=1.5: n={len(all_rr15)} WR={wr15:.1f}% 盈亏平衡={be15:.1f}% 安全垫={wr15-be15:+.1f}%")
    print(f"  RR=2.0: n={len(all_rr20)} WR={wr20:.1f}% 盈亏平衡={be20:.1f}% 安全垫={wr20-be20:+.1f}%")
    
    # TP触达率分析
    tp_hits = sum(1 for s in all_rr15 if s['exit_reason']=='TP')
    sl_hits = sum(1 for s in all_rr15 if s['exit_reason']=='SL')
    time_exits = sum(1 for s in all_rr15 if s['exit_reason']=='TIME')
    print(f"\n  RR=1.5出场分析: TP={tp_hits} SL={sl_hits} TIME={time_exits}")
    print(f"  TP触达率: {tp_hits/len(all_rr15)*100:.1f}%" if all_rr15 else "")
    
    passed = wr15 >= be15
    print(f"\n  {'✅ 通过(TP触达率≥盈亏平衡)' if passed else '❌ 未通过(TP触达率<盈亏平衡)'}")
    
    return {'rr10': all_rr10, 'rr15': all_rr15, 'rr20': all_rr20,
            'wr10': wr10, 'wr15': wr15, 'wr20': wr20,
            'be10': be10, 'be15': be15, 'be20': be20, 'passed': passed}

# ── Step4: walk-forward ───────────────────────────────────────

def step4_walk_forward(symbols):
    """训练2022-2024，测试2025-2026"""
    print("\n" + "="*60)
    print("Step4: walk-forward样本外验证")
    print("="*60)
    
    train_ts = int(datetime.strptime('2022-01-01','%Y-%m-%d').timestamp()*1000)
    test_ts = int(datetime.strptime('2025-01-01','%Y-%m-%d').timestamp()*1000)
    
    train_sigs = []
    test_sigs = []
    
    for sym in symbols:
        SYM = sym.upper()+'USDT'
        for tf in ['15m','1h']:
            bars = load_klines(SYM, tf, train_ts)
            regime_map = load_regime_labels(SYM)
            if not bars: continue
            sigs = generate_signals(bars, regime_map, tf, min_score=100, start_ts=train_ts)
            
            for s in sigs:
                exit_r = simulate_exit(bars, s['idx'], s['direction'], s['entry'], s['atr'], s['regime'], tf, rr=1.0)
                s.update(exit_r)
                if s['ts'] < test_ts:
                    train_sigs.append(s)
                else:
                    test_sigs.append(s)
    
    def stats(lst):
        if not lst: return 0,0,0
        wr = sum(1 for s in lst if s['is_win'])/len(lst)*100
        pnl = sum(s['pnl_pct'] for s in lst)/len(lst)
        ic = calc_ic(lst)
        return wr, pnl, ic
    
    # 按体制×方向分层比较
    train_by = defaultdict(list)
    test_by = defaultdict(list)
    for s in train_sigs: train_by[f"{s['regime']}:{s['direction']}"].append(s)
    for s in test_sigs: test_by[f"{s['regime']}:{s['direction']}"].append(s)
    
    wr_tr, p_tr, ic_tr = stats(train_sigs)
    wr_te, p_te, ic_te = stats(test_sigs)
    
    print(f"\n  训练集(2022-2024): n={len(train_sigs)} WR={wr_tr:.1f}% pnl={p_tr:.3f}% IC={ic_tr:.4f}")
    print(f"  测试集(2025-2026): n={len(test_sigs)} WR={wr_te:.1f}% pnl={p_te:.3f}% IC={ic_te:.4f}")
    print(f"  WR差异: {wr_te-wr_tr:+.1f}%")
    
    # 分层比较
    print(f"\n  分层WR比较:")
    print(f"  {'策略':<30} {'训练WR':>7} {'测试WR':>7} {'差异':>7}")
    all_keys = set(train_by.keys()) | set(test_by.keys())
    for key in sorted(all_keys):
        tr = train_by.get(key, [])
        te = test_by.get(key, [])
        wtr = sum(1 for s in tr if s['is_win'])/len(tr)*100 if tr else 0
        wte = sum(1 for s in te if s['is_win'])/len(te)*100 if te else 0
        diff = wte - wtr
        flag = '✅' if abs(diff) < 5 else '⚠️'
        print(f"  {flag} {key:<28} {wtr:>6.1f}% {wte:>6.1f}% {diff:>+6.1f}%")
    
    passed = wr_te >= 52.0
    print(f"\n  {'✅ 通过(样本外WR≥52%)' if passed else '❌ 未过(样本外WR<52%,可能过拟合)'}")
    
    return {'train': train_sigs, 'test': test_sigs,
            'wr_tr': wr_tr, 'wr_te': wr_te, 'ic_tr': ic_tr, 'ic_te': ic_te, 'passed': passed}

# ── IC计算 ────────────────────────────────────────────────────

def calc_ic(signals):
    if len(signals) < 10: return 0.0
    scores = [s['score'] for s in signals]
    outcomes = [1.0 if s['is_win'] else 0.0 for s in signals]
    n = len(scores)
    ms = sum(scores)/n
    mo = sum(outcomes)/n
    cov = sum((scores[i]-ms)*(outcomes[i]-mo) for i in range(n))/n
    ss = math.sqrt(sum((s-ms)**2 for s in scores)/n)
    so = math.sqrt(sum((o-mo)**2 for o in outcomes)/n)
    return round(cov/(ss*so),4) if ss and so else 0.0

# ── 主入口 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTC','ETH'])
    parser.add_argument('--start', default='2022-01-01')
    args = parser.parse_args()
    
    t0 = time.time()
    print("="*60)
    print("🏛️ 三方联合4步验证 | 苏摩111 2026-09-08")
    print(f"标的: {args.symbols} | 起始: {args.start}")
    print("="*60)
    
    r1 = step1_resonance(args.symbols, args.start)
    r2 = step2_strategy_filter(args.symbols, args.start)
    r3 = step3_rr15(args.symbols, args.start)
    r4 = step4_walk_forward(args.symbols)
    
    elapsed = time.time() - t0
    
    # 最终汇总
    print("\n" + "="*60)
    print("📊 三方联合4步验证 | 最终汇总")
    print("="*60)
    
    results = {
        'Step1_共振': {'WR': r1['wr_r'], '基准WR': r1['wr_s'], '通过': r1['wr_r']>=55},
        'Step2_过滤': {'WR': r2['wr_g'], '排除WR': r2['wr_b'], '通过': r2['wr_g']>=55},
        'Step3_RR1.5': {'WR': r3['wr15'], '盈亏平衡': r3['be15'], '通过': r3['passed']},
        'Step4_WF': {'训练WR': r4['wr_tr'], '测试WR': r4['wr_te'], '通过': r4['passed']},
    }
    
    pass_count = sum(1 for v in results.values() if v.get('通过'))
    
    for step, v in results.items():
        p = '✅' if v.get('通过') else '❌'
        print(f"  {p} {step}: {v}")
    
    print(f"\n  通过: {pass_count}/4")
    if pass_count == 4:
        print("  🟢 4步全过 = 系统真能赚钱")
    elif pass_count >= 2:
        print(f"  🟡 {pass_count}步通过 = 部分有效，需调整")
    else:
        print("  🔴 大部分未通过 = 需重新设计")
    
    print(f"\n  总耗时: {elapsed:.1f}s")
    
    # 写出
    output = {'ts': datetime.utcnow().isoformat()+'Z', 'results': {
        k: {kk: vv for kk, vv in v.items()} for k, v in results.items()
    }, 'pass_count': pass_count}
    
    with open(OUT_DIR / 'backtest_4step_validation.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果: {OUT_DIR}/backtest_4step_validation.json")

if __name__ == '__main__':
    main()
