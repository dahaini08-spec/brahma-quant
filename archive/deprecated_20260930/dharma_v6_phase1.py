"""
dharma_v6_phase1.py — Phase 1 单变量验证
苏摩111 2026-09-09

按dharma_v6_master_plan.md执行：
  P1-1: min_score阈值搜索（90/95/100/105/110）
  P2-1: 只做最强组合（BULL_TREND:LONG + BEAR_TREND:SHORT）
  P2-2: 加入次强组合
  P3-1: RR搜索（1.2/1.5/1.8/2.0/2.5）
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

# ═══ 精简评分器 v5_slim（复用） ═══
def score_v5_slim(regime, direction, rsi, resonance, bb_w):
    s = 50.0
    bonus = {
        ('BULL_TREND','LONG'): 30,  ('BULL_TREND','SHORT'): -20,
        ('BEAR_TREND','SHORT'): 30, ('BEAR_TREND','LONG'): -20,
        ('BULL_EARLY','LONG'): 25,  ('BULL_EARLY','SHORT'): -15,
        ('BEAR_RECOVERY','LONG'): 15, ('BEAR_RECOVERY','SHORT'): -25,
        ('CHOP_MID','LONG'): 0,     ('CHOP_MID','SHORT'): 0,
        ('CHOP_HIGH','LONG'): 5,    ('CHOP_HIGH','SHORT'): 5,
    }
    s += bonus.get((regime, direction), 0)
    if direction == 'LONG':
        if rsi and rsi < 30: s += 15
        elif rsi and rsi > 70: s -= 15
    else:
        if rsi and rsi > 70: s += 15
        elif rsi and rsi < 30: s -= 15
    if bb_w < 1.0: s += 12
    elif bb_w > 5.0: s -= 8
    if resonance['score'] >= 3: s += 10
    elif resonance['score'] == 2: s += 5
    elif resonance['score'] == 0: s -= 3
    return max(0, min(150, s))


def sim_exit_natural(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    sl_pct = 0.02
    hold = {'15m': 48, '1h': 24, '4h': 12, '1d': 5}.get(tf, 24)
    min_sl = 1.5 * atr if atr else entry_price * 0.01
    sl_dist = max(entry_price * sl_pct, min_sl)
    if direction == 'LONG':
        sl = entry_price - sl_dist; tp = entry_price + sl_dist * rr
    else:
        sl = entry_price + sl_dist; tp = entry_price - sl_dist * rr
    for i in range(entry_idx + 1, min(entry_idx + hold + 1, len(bars))):
        b = bars[i]
        if direction == 'LONG':
            if b['l'] <= sl:
                g = (sl - entry_price) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'SL', 'bars_held': i - entry_idx}
            if b['h'] >= tp:
                g = (tp - entry_price) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TP', 'bars_held': i - entry_idx}
        else:
            if b['h'] >= sl:
                g = (entry_price - sl) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'SL', 'bars_held': i - entry_idx}
            if b['l'] <= tp:
                g = (entry_price - tp) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TP', 'bars_held': i - entry_idx}
    close = bars[min(entry_idx + hold, len(bars) - 1)]['c']
    g = ((close - entry_price) / entry_price) if direction == 'LONG' else ((entry_price - close) / entry_price)
    return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TIME', 'bars_held': hold}


def sim_exit_trailing(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    """trailing SL: 浮盈>2ATR保本, >4ATR锁1ATR, >6ATR锁3ATR"""
    sl_pct = 0.02
    hold = {'15m': 48, '1h': 24, '4h': 12, '1d': 5}.get(tf, 24)
    min_sl = 1.5 * atr if atr else entry_price * 0.01
    sl_dist = max(entry_price * sl_pct, min_sl)
    if direction == 'LONG':
        sl = entry_price - sl_dist; tp = entry_price + sl_dist * rr
    else:
        sl = entry_price + sl_dist; tp = entry_price - sl_dist * rr
    
    trailing_sl = sl  # 初始SL
    for i in range(entry_idx + 1, min(entry_idx + hold + 1, len(bars))):
        b = bars[i]
        # 更新trailing SL
        if atr:
            if direction == 'LONG':
                profit = b['h'] - entry_price
                if profit > 6 * atr: trailing_sl = max(trailing_sl, entry_price + 3 * atr)
                elif profit > 4 * atr: trailing_sl = max(trailing_sl, entry_price + 1 * atr)
                elif profit > 2 * atr: trailing_sl = max(trailing_sl, entry_price)
            else:
                profit = entry_price - b['l']
                if profit > 6 * atr: trailing_sl = min(trailing_sl, entry_price - 3 * atr)
                elif profit > 4 * atr: trailing_sl = min(trailing_sl, entry_price - 1 * atr)
                elif profit > 2 * atr: trailing_sl = min(trailing_sl, entry_price)
        
        if direction == 'LONG':
            if b['l'] <= trailing_sl:
                g = (trailing_sl - entry_price) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TSL' if trailing_sl > sl else 'SL', 'bars_held': i - entry_idx}
            if b['h'] >= tp:
                g = (tp - entry_price) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TP', 'bars_held': i - entry_idx}
        else:
            if b['h'] >= trailing_sl:
                g = (entry_price - trailing_sl) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TSL' if trailing_sl < sl else 'SL', 'bars_held': i - entry_idx}
            if b['l'] <= tp:
                g = (entry_price - tp) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TP', 'bars_held': i - entry_idx}
    close = bars[min(entry_idx + hold, len(bars) - 1)]['c']
    g = ((close - entry_price) / entry_price) if direction == 'LONG' else ((entry_price - close) / entry_price)
    return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TIME', 'bars_held': hold}


# ═══ 信号生成（可配置白名单+min_score+RR+出场模式） ═══
def gen_signals_v6(bars, rmap, tf, rr=1.5, min_score=90, 
                   whitelist=None, exit_mode='natural', start_ts=0):
    atrs = calc_atr(bars, 14)
    rsis = calc_rsi(bars, 14)
    min_gap = {'15m': 4*15*60*1000, '1h': 4*3600*1000}.get(tf, 4*3600*1000)
    sigs = []; last_ts = 0
    
    for i in range(100, len(bars) - 50):
        b = bars[i]; ts = b['ts']
        if ts - last_ts < min_gap: continue
        if ts < start_ts: continue
        
        ts_4h = (ts // (4*3600*1000)) * (4*3600*1000) - 4*3600*1000
        regime = rmap.get(ts_4h)
        if not regime:
            for off in range(-3, 4):
                regime = rmap.get(ts_4h + off * 4 * 3600 * 1000)
                if regime: break
        if not regime: continue
        
        atr = atrs[i]; rsi = rsis[i]
        if not atr or not rsi: continue
        
        if i >= 20:
            cl = [bars[j]['c'] for j in range(i-20, i)]
            mc = sum(cl)/20; sc = math.sqrt(sum((c-mc)**2 for c in cl)/20)
            bb_w = sc*2/mc*100 if mc else 0
        else: bb_w = 0
        
        fvg = detect_fvg_advanced(bars, i)
        ob = detect_ob_advanced(bars, i)
        liq = build_liquidity_map(bars, i)
        res = detect_resonance(bars, i, fvg, ob, liq, atr)
        
        for d in ['LONG', 'SHORT']:
            # 白名单过滤
            if whitelist and (regime, d) not in whitelist:
                continue
            
            sc_val = score_v5_slim(regime, d, rsi, res, bb_w)
            if sc_val < min_score: continue
            
            ni = i + 1
            if ni >= len(bars): continue
            ep = bars[ni]['o']
            
            if exit_mode == 'trailing':
                er = sim_exit_trailing(bars, ni, d, ep, atr, tf, rr=rr)
            else:
                er = sim_exit_natural(bars, ni, d, ep, atr, tf, rr=rr)
            
            sigs.append({
                'ts': ts, 'score': round(sc_val, 1), 'resonance': res['score'],
                'direction': d, 'regime': regime, 'tf': tf,
                'rsi': round(rsi, 1) if rsi else None,
                'bb_w': round(bb_w, 3), 'atr': round(atr, 2),
                'entry': round(ep, 2), **er
            })
            last_ts = ts
    return sigs


def calc_ic(sigs):
    if len(sigs) < 10: return 0.0
    s = [s['score'] for s in sigs]; o = [1.0 if s['is_win'] else 0.0 for s in sigs]
    n = len(s); ms = sum(s)/n; mo = sum(o)/n
    cov = sum((s[i]-ms)*(o[i]-mo) for i in range(n))/n
    ss = math.sqrt(sum((x-ms)**2 for x in s)/n); so = math.sqrt(sum((x-mo)**2 for x in o)/n)
    return round(cov/(ss*so), 4) if ss and so else 0.0


def run_backtest(label, rr=1.5, min_score=90, whitelist=None, exit_mode='natural'):
    start_ts = int(datetime.strptime('2022-01-01', '%Y-%m-%d').timestamp()*1000)
    all_sigs = []
    
    for sym in ['BTC', 'ETH']:
        SYM = sym.upper() + 'USDT'
        for tf in ['15m', '1h']:
            bars = load_klines(SYM, tf, start_ts)
            rmap = load_regime_labels(SYM)
            if not bars: continue
            sigs = gen_signals_v6(bars, rmap, tf, rr=rr, min_score=min_score,
                                  whitelist=whitelist, exit_mode=exit_mode, start_ts=start_ts)
            all_sigs.extend(sigs)
    
    n = len(all_sigs)
    if n == 0:
        print(f"  {label}: ❌ 无信号")
        return None
    
    wr = sum(1 for s in all_sigs if s['is_win'])/n*100
    ic = calc_ic(all_sigs)
    pnl_gross = sum(s['pnl_gross'] for s in all_sigs)/n
    pnl_net = sum(s['pnl_net'] for s in all_sigs)/n
    
    # 出场分布
    exit_dist = defaultdict(int)
    for s in all_sigs: exit_dist[s['exit_reason']] += 1
    
    # PnL模拟
    nav = 100000; pos = nav*0.05*5; fee = pos*(COST+SLIPPAGE)*2
    sl_dist = 0.02; total_pnl = 0; cum = 0; peak = 0; max_dd = 0; pnl_list = []
    for s in all_sigs:
        if s['is_win']: p = sl_dist*rr*pos - fee
        else: p = -sl_dist*pos - fee
        total_pnl += p; cum += p
        if cum > peak: peak = cum
        if peak-cum > max_dd: max_dd = peak-cum
        pnl_list.append(p)
    mean_p = sum(pnl_list)/len(pnl_list) if pnl_list else 0
    std_p = math.sqrt(sum((p-mean_p)**2 for p in pnl_list)/len(pnl_list)) if len(pnl_list) > 1 else 0
    tpy = min(n/4.5, 5*365)
    sharpe = mean_p/std_p*math.sqrt(tpy) if std_p > 0 else 0
    
    # Walk-Forward
    ts_min = min(s['ts'] for s in all_sigs); ts_max = max(s['ts'] for s in all_sigs)
    total_m = (ts_max-ts_min)/(86400*30*1000); bpm = n/total_m
    n_folds = min(9, max(3, int((total_m-24-6)/3)+1)) if total_m > 30 else 3
    folds = []
    for f in range(n_folds):
        te = int((f*3+24)*bpm); ed = int((f*3+30)*bpm)
        fs = all_sigs[te:ed]
        if len(fs) < 10: continue
        wr_f = sum(1 for s in fs if s['is_win'])/len(fs)*100
        ev_f = sum(s['pnl_net'] for s in fs)/len(fs)
        folds.append({'fold': f+1, 'n': len(fs), 'wr': round(wr_f, 1), 'ev_net': round(ev_f, 3)})
    pf = sum(1 for f in folds if f['wr'] >= 52)
    ff = sum(1 for f in folds if f['wr'] < 50)
    stab = pf/len(folds) if folds else 0
    
    # 体制分布
    regime_dist = defaultdict(int)
    for s in all_sigs: regime_dist[f"{s['regime']}:{s['direction']}"] += 1
    
    flag = '✅' if pnl_net > 0 else '❌'
    print(f"\n  📊 {label}")
    print(f"  {flag} n={n} WR={wr:.1f}% IC={ic:+.4f} gross={pnl_gross:+.3f}% net={pnl_net:+.3f}%")
    print(f"  出场: {dict(exit_dist)}")
    print(f"  PnL=${total_pnl:,.0f} Sharpe={sharpe:.2f} DD=${max_dd:,.0f}")
    print(f"  WF: {len(folds)}折 稳定性={stab:.1%} 通过={pf} 失败={ff}")
    for f in folds:
        fl = '✅' if f['wr']>=52 else ('⚠️' if f['wr']>=50 else '❌')
        print(f"    {fl} F{f['fold']}: n={f['n']:>5} WR={f['wr']:>5.1f}% EV={f['ev_net']:>+.3f}%")
    print(f"  体制分布: {dict(regime_dist)}")
    
    return {
        'label': label, 'n': n, 'wr': round(wr, 1), 'ic': ic,
        'pnl_gross': round(pnl_gross, 3), 'pnl_net': round(pnl_net, 3),
        'sharpe': round(sharpe, 2), 'max_dd': round(max_dd, 0),
        'total_pnl': round(total_pnl, 0), 'folds': folds,
        'wf_stability': round(stab, 3), 'wf_pass': pf, 'wf_fail': ff,
        'exit_dist': dict(exit_dist), 'regime_dist': dict(regime_dist),
        'rr': rr, 'min_score': min_score, 'exit_mode': exit_mode,
    }


def main():
    t0 = time.time()
    results = {}
    
    # ═══════════════════════════════════════════════════════
    # P2-1: 只做最强组合 BULL_TREND:LONG + BEAR_TREND:SHORT
    # ═══════════════════════════════════════════════════════
    print("="*70)
    print("🔥 P2-1: 只做最强组合 (BULL_TREND:LONG + BEAR_TREND:SHORT)")
    print("="*70)
    
    strong_whitelist = {('BULL_TREND','LONG'), ('BEAR_TREND','SHORT')}
    r = run_backtest('最强组合 only', rr=1.5, min_score=90, whitelist=strong_whitelist, exit_mode='natural')
    results['P2_1_strong_only'] = r
    
    # ═══════════════════════════════════════════════════════
    # P2-2: 加入次强组合
    # ═══════════════════════════════════════════════════════
    print("\n" + "="*70)
    print("🔥 P2-2: 加入次强组合 (+BULL_EARLY:LONG + BEAR_RECOVERY:LONG)")
    print("="*70)
    
    medium_whitelist = strong_whitelist | {('BULL_EARLY','LONG'), ('BEAR_RECOVERY','LONG')}
    r = run_backtest('强+次强组合', rr=1.5, min_score=90, whitelist=medium_whitelist, exit_mode='natural')
    results['P2_2_with_medium'] = r
    
    # ═══════════════════════════════════════════════════════
    # P1-1: min_score阈值搜索（在最强组合上）
    # ═══════════════════════════════════════════════════════
    print("\n" + "="*70)
    print("🔍 P1-1: min_score阈值搜索（最强组合上）")
    print("="*70)
    
    for ms in [90, 95, 100, 105, 110]:
        r = run_backtest(f'min_score={ms}', rr=1.5, min_score=ms, whitelist=strong_whitelist, exit_mode='natural')
        results[f'P1_1_ms{ms}'] = r
    
    # ═══════════════════════════════════════════════════════
    # P3-1: RR搜索（在最强组合+最优min_score上）
    # ═══════════════════════════════════════════════════════
    print("\n" + "="*70)
    print("🔍 P3-1: RR搜索（最强组合上）")
    print("="*70)
    
    for rr in [1.2, 1.5, 1.8, 2.0, 2.5]:
        r = run_backtest(f'RR={rr}', rr=rr, min_score=90, whitelist=strong_whitelist, exit_mode='natural')
        results[f'P3_1_rr{rr}'] = r
    
    # ═══════════════════════════════════════════════════════
    # P3-3: 出场对比（trailing SL vs 自然出场）
    # ═══════════════════════════════════════════════════════
    print("\n" + "="*70)
    print("🔍 P3-3: 出场对比（trailing SL vs 自然）")
    print("="*70)
    
    r = run_backtest('trailing SL', rr=1.5, min_score=90, whitelist=strong_whitelist, exit_mode='trailing')
    results['P3_3_trailing'] = r
    
    # ═══════════════════════════════════════════════════════
    # P5-1: 代理regime跨标的（BTC regime跑山寨）
    # ═══════════════════════════════════════════════════════
    print("\n" + "="*70)
    print("🔍 P5-1: 代理regime跨标的（BTC regime → 山寨）")
    print("="*70)
    
    start_ts = int(datetime.strptime('2022-01-01', '%Y-%m-%d').timestamp()*1000)
    btc_rmap_15m = load_regime_labels('BTCUSDT')
    btc_rmap_1h = load_regime_labels('BTCUSDT')
    
    for sym in ['SOL', 'BNB', 'ADA']:
        SYM = sym.upper() + 'USDT'
        sym_sigs = []
        for tf in ['15m', '1h']:
            bars = load_klines(SYM, tf, start_ts)
            if not bars: continue
            sigs = gen_signals_v6(bars, btc_rmap_1h if tf == '1h' else btc_rmap_15m, tf,
                                  rr=1.5, min_score=90, whitelist=strong_whitelist, 
                                  exit_mode='natural', start_ts=start_ts)
            sym_sigs.extend(sigs)
        n_s = len(sym_sigs)
        if n_s > 0:
            wr_s = sum(1 for s in sym_sigs if s['is_win'])/n_s*100
            ev_s = sum(s['pnl_net'] for s in sym_sigs)/n_s
            print(f"  {sym}: n={n_s} WR={wr_s:.1f}% EV(net)={ev_s:+.3f}%")
            results[f'P5_1_{sym}'] = {'n': n_s, 'wr': round(wr_s, 1), 'ev_net': round(ev_s, 3)}
        else:
            print(f"  {sym}: n=0 无信号")
            results[f'P5_1_{sym}'] = {'n': 0, 'wr': 0, 'ev_net': 0}
    
    elapsed = time.time() - t0
    
    # ═══ 汇总 ═══
    print("\n" + "="*70)
    print("📊 Phase 1 全量汇总")
    print("="*70)
    
    print(f"\n{'配置':<35} {'n':>6} {'WR':>6} {'IC':>8} {'net EV':>8} {'Sharpe':>7} {'WF stab':>8}")
    print("-" * 80)
    
    for key, r in results.items():
        if r is None: continue
        if isinstance(r, dict) and 'n' in r and 'wr' in r and 'pnl_net' in r:
            wf = r.get('wf_stability', 0)
            print(f"{r.get('label', key):<35} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['pnl_net']:>+7.3f}% {r.get('sharpe',0):>7.2f} {wf:>7.1%}")
        elif isinstance(r, dict) and 'n' in r:
            # 跨标的简化结果
            print(f"  {key:<33} {r['n']:>6} {r['wr']:>5.1f}% {'':>8} {r['ev_net']:>+7.3f}%")
    
    # 找最优
    valid = [r for r in results.values() if r and isinstance(r, dict) and r.get('pnl_net', -999) > 0]
    if valid:
        best = max(valid, key=lambda x: x['pnl_net'])
        print(f"\n  🟢 最优配置: {best['label']} net EV={best['pnl_net']:+.3f}%/笔")
        print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f} Sharpe={best['sharpe']:.2f}")
        print(f"     WF稳定性={best['wf_stability']:.1%}")
    else:
        all_r = [r for r in results.values() if r and isinstance(r, dict) and 'pnl_net' in r]
        if all_r:
            best = max(all_r, key=lambda x: x['pnl_net'])
            print(f"\n  ⚠️ 仍无正EV配置，最优: {best['label']} net EV={best['pnl_net']:+.3f}%/笔")
            print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f}")
    
    print(f"\n  耗时: {elapsed:.1f}s")
    
    # 写出
    output = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'phase': 'v6_phase1',
        'results': {k: v for k, v in results.items() if v is not None},
    }
    with open(OUT_DIR / 'dharma_v6_phase1_result.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果: data/dharma_v6_phase1_result.json")


if __name__ == '__main__':
    main()
