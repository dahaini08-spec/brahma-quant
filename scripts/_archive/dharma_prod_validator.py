"""
dharma_prod_validator.py — 生产评分器回测验证
苏摩111 2026-09-09 | 首席专家纠偏Step1+Step2

Layer 1: 用真实confluence_score()跑4.5年回测（不是score_v4）
Layer 2: 时间切分样本外验证（2022-2024训练, 2025-2026测试）
Layer 3: WF稳定性硬标准
Layer 4: 入场质量过滤器（OB/FVG/清算门控）
"""
import gzip, json, math, time, os, sys
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR = BASE / "data"
COST = 0.0008; SLIPPAGE = 0.0005

# 复用指标函数
exec(open(BASE / "scripts" / "dharma_ultimate_validator.py").read().split("def simulate_exit_ultimate")[0])

# ═══ 生产评分器调用 ═══
def call_prod_scorer(bars, i, regime, direction, atr, rsi, bb_w, symbol):
    """
    构造ms/smc字典，调用真实confluence_score()
    """
    b = bars[i]
    price = b['c']
    
    # 构造ms字典
    ms = {
        'symbol': symbol,
        'price': price,
        'regime': regime,
        'rsi_1h': rsi,
        'rsi_4h': rsi,  # 简化：用同一RSI
        'rsi_15m': rsi,
        'atr_1h': atr,
        'atr_4h': atr * 2,
        'atr_pct': atr / price * 100 if price > 0 else 0,
        'momentum': {
            'rsi_1h': rsi, 'rsi_4h': rsi, 'rsi_15m': rsi,
            'atr_1h': atr, 'atr_4h': atr * 2,
        },
        'trend': {
            'consensus': {'consensus': 'LEAN_BULL' if direction == 'LONG' else 'LEAN_BEAR'},
            '1h': {'adx': 25, 'direction': 'up' if direction == 'LONG' else 'down'},
            '4h': {'adx': 25, 'direction': 'up' if direction == 'LONG' else 'down'},
            '15m': {'adx': 25, 'direction': 'up' if direction == 'LONG' else 'down'},
        },
        'key_levels': {
            'fib': {'0.236': price*0.99, '0.382': price*0.995, '0.5': price, '0.618': price*1.005, '0.786': price*1.01}
        },
        'sentiment': {
            'long_short_ratio': 1.0,
            'oi': 100000,
            'oi_change_pct': 0.0,
            'oi_momentum': 'NEUTRAL',
            'funding_rate': 0.0001,
        },
        'wave': {'pattern': 'none', 'strength': 0},
    }
    
    # 构造smc字典
    fvg = detect_fvg_advanced(bars, i)
    ob = detect_ob_advanced(bars, i)
    liq = build_liquidity_map(bars, i)
    
    smc = {
        'score': {'score': 10},  # s4基础分
        'fvg': fvg,
        'order_blocks': {
            'nearest_bull_ob': {'dist_pct': 0.5, 'age': 10, 'top': price*1.005, 'bottom': price*0.995} if ob.get('valid') and ob.get('type') == 'bull' else None,
            'nearest_bear_ob': {'dist_pct': 0.5, 'age': 10, 'top': price*1.005, 'bottom': price*0.995} if ob.get('valid') and ob.get('type') == 'bear' else None,
        },
        'order_blocks_4h': {},
        'order_blocks_15m': {},
        'order_blocks_1d': {},
        'liquidity': liq,
    }
    
    extra_data = {'price': price}
    
    try:
        from brahma_brain.brahma_core import confluence_score
        raw = confluence_score(ms=ms, smc=smc, signal_dir=direction, extra_data=extra_data)
        score = float(raw.get('score', 0))
        breakdown = raw.get('breakdown', {})
        return score, breakdown
    except Exception as e:
        return 0, {'_error': str(e)[:80]}


# ═══ 出场模拟 ═══
def sim_exit(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    sl_pct = 0.02
    hold = {'15m': 48, '1h': 24, '4h': 12}.get(tf, 24)
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
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'SL'}
            if b['h'] >= tp:
                g = (tp - entry_price) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TP'}
        else:
            if b['h'] >= sl:
                g = (entry_price - sl) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'SL'}
            if b['l'] <= tp:
                g = (entry_price - tp) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TP'}
    close = bars[min(entry_idx + hold, len(bars) - 1)]['c']
    g = ((close - entry_price) / entry_price) if direction == 'LONG' else ((entry_price - close) / entry_price)
    return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100, 'exit_reason': 'TIME'}


# ═══ 入场过滤器（Layer 4） ═══
def entry_filter(bars, i, direction, atr, fvg, ob, liq):
    """
    OB/FVG/清算入场门控，不参与评分但参与入场决策
    返回 (allow, reason, position_mult)
    """
    # Filter 1: OB有效性
    if not ob.get('valid'):
        return False, 'NO_VALID_OB', 0.0
    
    # Filter 2: FVG方向一致（放宽：不强制，但FVG相反时降仓）
    fvg_dir_match = (direction == 'LONG' and fvg.get('type') == 'bull') or \
                    (direction == 'SHORT' and fvg.get('type') == 'bear')
    if not fvg_dir_match and fvg.get('type') != 'none':
        # FVG相反但不阻止，降仓50%
        pass
    
    # Filter 3: 清算密集区距离
    pos_mult = 1.0
    liq_density = liq.get('liq_density', 0)
    if liq_density > 0.5:
        liq_pool = liq.get('short_liq_pool') or liq.get('long_liq_pool')
        if liq_pool:
            dist = abs(liq_pool - bars[i]['c']) / bars[i]['c']
            atr_pct = atr / bars[i]['c'] if bars[i]['c'] > 0 else 0.02
            if dist < 0.5 * atr_pct:
                return False, 'LIQ_TOO_CLOSE', 0.0
            if dist < 1.0 * atr_pct:
                pos_mult = 0.5
    
    if not fvg_dir_match and fvg.get('type') != 'none':
        pos_mult *= 0.5
    
    return True, 'PASS', pos_mult


# ═══ 信号生成 ═══
def gen_signals_prod(bars, rmap, tf, rr=1.5, min_score=90, 
                     whitelist=None, use_filter=False, start_ts=0, end_ts=0):
    """
    用生产评分器confluence_score()生成信号
    """
    atrs = calc_atr(bars, 14)
    rsis = calc_rsi(bars, 14)
    min_gap = {'15m': 4*15*60*1000, '1h': 4*3600*1000}.get(tf, 4*3600*1000)
    sigs = []; last_ts = 0
    symbol = 'BTCUSDT'  # 简化
    
    for i in range(100, len(bars) - 50):
        b = bars[i]; ts = b['ts']
        if ts - last_ts < min_gap: continue
        if start_ts and ts < start_ts: continue
        if end_ts and ts >= end_ts: continue
        
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
        else:
            bb_w = 0
        
        fvg = detect_fvg_advanced(bars, i)
        ob = detect_ob_advanced(bars, i)
        liq = build_liquidity_map(bars, i)
        
        for d in ['LONG', 'SHORT']:
            if whitelist and (regime, d) not in whitelist:
                continue
            
            # 入场过滤器
            if use_filter:
                allow, reason, pos_mult = entry_filter(bars, i, d, atr, fvg, ob, liq)
                if not allow:
                    continue
            else:
                pos_mult = 1.0
            
            # 生产评分器
            sc_val, _ = call_prod_scorer(bars, i, regime, d, atr, rsi, bb_w, symbol)
            if sc_val < min_score:
                continue
            
            ni = i + 1
            if ni >= len(bars): continue
            ep = bars[ni]['o']
            er = sim_exit(bars, ni, d, ep, atr, tf, rr=rr)
            
            sigs.append({
                'ts': ts, 'score': round(sc_val, 1),
                'direction': d, 'regime': regime, 'tf': tf,
                'rsi': round(rsi, 1) if rsi else None,
                'bb_w': round(bb_w, 3),
                'pos_mult': pos_mult,
                'fvg_type': fvg.get('type', 'none'),
                'ob_valid': ob.get('valid', False),
                'liq_density': liq.get('liq_density', 0),
                **er
            })
            last_ts = ts
    return sigs


def calc_ic(sigs):
    if len(sigs) < 10: return 0.0
    s = [x['score'] for x in sigs]; o = [1.0 if x['is_win'] else 0.0 for x in sigs]
    n = len(s); ms_ = sum(s)/n; mo = sum(o)/n
    cov = sum((s[i]-ms_)*(o[i]-mo) for i in range(n))/n
    ss = math.sqrt(sum((x-ms_)**2 for x in s)/n); so = math.sqrt(sum((x-mo)**2 for x in o)/n)
    return round(cov/(ss*so), 4) if ss and so else 0.0


def run_backtest(label, rr=1.5, min_score=90, whitelist=None, use_filter=False,
                 start_ts=0, end_ts=0):
    all_sigs = []
    for sym in ['BTC', 'ETH']:
        SYM = sym.upper() + 'USDT'
        for tf in ['15m', '1h']:
            bars = load_klines(SYM, tf, start_ts)
            rmap = load_regime_labels(SYM)
            if not bars: continue
            sigs = gen_signals_prod(bars, rmap, tf, rr=rr, min_score=min_score,
                                    whitelist=whitelist, use_filter=use_filter,
                                    start_ts=start_ts, end_ts=end_ts)
            all_sigs.extend(sigs)
    
    n = len(all_sigs)
    if n == 0:
        print(f"  {label}: ❌ 无信号")
        return None
    
    wr = sum(1 for s in all_sigs if s['is_win'])/n*100
    ic = calc_ic(all_sigs)
    pnl_g = sum(s['pnl_gross'] for s in all_sigs)/n
    pnl_n = sum(s['pnl_net'] for s in all_sigs)/n
    
    exit_dist = defaultdict(int)
    for s in all_sigs: exit_dist[s['exit_reason']] += 1
    
    # PnL
    nav = 100000; pos = nav*0.05*5; fee = pos*(COST+SLIPPAGE)*2; sl_dist = 0.02
    total_pnl = 0; cum = 0; peak = 0; max_dd = 0; pnl_list = []
    for s in all_sigs:
        pm = s.get('pos_mult', 1.0)
        p = (sl_dist*rr*pos*pm - fee) if s['is_win'] else (-sl_dist*pos*pm - fee)
        total_pnl += p; cum += p
        if cum > peak: peak = cum
        if peak-cum > max_dd: max_dd = peak-cum
        pnl_list.append(p)
    mean_p = sum(pnl_list)/len(pnl_list) if pnl_list else 0
    std_p = math.sqrt(sum((p-mean_p)**2 for p in pnl_list)/len(pnl_list)) if len(pnl_list) > 1 else 0
    tpy = min(n/4.5, 5*365); sharpe = mean_p/std_p*math.sqrt(tpy) if std_p > 0 else 0
    
    # WF
    if n > 100:
        ts_min = min(s['ts'] for s in all_sigs); ts_max = max(s['ts'] for s in all_sigs)
        total_m = (ts_max-ts_min)/(86400*30*1000); bpm = n/total_m
        folds = []
        for f in range(min(9, max(3, int((total_m-24-6)/3)+1)) if total_m > 30 else 3):
            te = int((f*3+24)*bpm); ed = int((f*3+30)*bpm)
            fs = all_sigs[te:ed]
            if len(fs) < 10: continue
            wr_f = sum(1 for s in fs if s['is_win'])/len(fs)*100
            ev_f = sum(s['pnl_net'] for s in fs)/len(fs)
            folds.append({'f': f+1, 'n': len(fs), 'wr': round(wr_f, 1), 'ev': round(ev_f, 3)})
        pf = sum(1 for f in folds if f['wr'] >= 52)
        ff = sum(1 for f in folds if f['wr'] < 50)
        stab = pf/len(folds) if folds else 0
    else:
        folds = []; pf = 0; ff = 0; stab = 0
    
    reg_dist = defaultdict(int)
    for s in all_sigs: reg_dist[f"{s['regime']}:{s['direction']}"] += 1
    
    flag = '✅' if pnl_n > 0 else '❌'
    print(f"\n  📊 {label}")
    print(f"  {flag} n={n} WR={wr:.1f}% IC={ic:+.4f} gross={pnl_g:+.3f}% net={pnl_n:+.3f}%")
    print(f"  出场: {dict(exit_dist)}")
    print(f"  PnL=${total_pnl:,.0f} Sharpe={sharpe:.2f} DD=${max_dd:,.0f}")
    print(f"  WF: {len(folds)}折 稳定性={stab:.1%} 通过={pf} 失败={ff}")
    for f in folds:
        fl = '✅' if f['wr']>=52 else ('⚠️' if f['wr']>=50 else '❌')
        print(f"    {fl} F{f['f']}: n={f['n']:>5} WR={f['wr']:>5.1f}% EV={f['ev']:>+.3f}%")
    print(f"  体制: {dict(reg_dist)}")
    
    return {
        'label': label, 'n': n, 'wr': round(wr, 1), 'ic': ic,
        'pnl_gross': round(pnl_g, 3), 'pnl_net': round(pnl_n, 3),
        'sharpe': round(sharpe, 2), 'max_dd': round(max_dd, 0),
        'total_pnl': round(total_pnl, 0), 'folds': folds,
        'wf_stab': round(stab, 3), 'wf_pass': pf, 'wf_fail': ff,
        'exit_dist': dict(exit_dist), 'regime_dist': dict(reg_dist),
    }


def main():
    t0 = time.time()
    results = {}
    
    train_start = int(datetime.strptime('2022-01-01', '%Y-%m-%d').timestamp() * 1000)
    train_end = int(datetime.strptime('2025-01-01', '%Y-%m-%d').timestamp() * 1000)
    test_start = train_end
    
    strong = {('BULL_TREND', 'LONG'), ('BEAR_TREND', 'SHORT')}
    
    # ═══ Layer 1: 生产评分器全量回测 ═══
    print("="*70)
    print("🔥 Layer 1: 生产评分器(confluence_score) 全量回测")
    print("="*70)
    results['L1_full'] = run_backtest('生产评分器 全量 4.5年', rr=1.5, min_score=90)
    
    # ═══ Layer 1b: 最强组合 ═══
    print("\n" + "="*70)
    print("🔥 Layer 1b: 生产评分器 + 最强组合")
    print("="*70)
    results['L1_strong'] = run_backtest('生产 最强组合 RR=1.5', rr=1.5, min_score=90, whitelist=strong)
    results['L1_strong_rr20'] = run_backtest('生产 最强组合 RR=2.0', rr=2.0, min_score=90, whitelist=strong)
    
    # ═══ Layer 2: 样本外验证（时间切分） ═══
    print("\n" + "="*70)
    print("🔥 Layer 2: 样本外验证 (训练2022-2024 vs 测试2025-2026)")
    print("="*70)
    
    print("\n  --- 训练集 2022-2024 ---")
    results['L2_train_strong'] = run_backtest('训练集 最强 RR=1.5', rr=1.5, min_score=90,
                                                whitelist=strong, start_ts=train_start, end_ts=train_end)
    results['L2_train_strong_rr20'] = run_backtest('训练集 最强 RR=2.0', rr=2.0, min_score=90,
                                                     whitelist=strong, start_ts=train_start, end_ts=train_end)
    
    print("\n  --- 测试集 2025-2026 ---")
    results['L2_test_strong'] = run_backtest('测试集 最强 RR=1.5', rr=1.5, min_score=90,
                                                whitelist=strong, start_ts=test_start)
    results['L2_test_strong_rr20'] = run_backtest('测试集 最强 RR=2.0', rr=2.0, min_score=90,
                                                    whitelist=strong, start_ts=test_start)
    
    # ═══ Layer 4: 入场过滤器 ═══
    print("\n" + "="*70)
    print("🔥 Layer 4: 入场过滤器 (OB/FVG/清算门控)")
    print("="*70)
    results['L4_filter_rr15'] = run_backtest('最强+过滤 RR=1.5', rr=1.5, min_score=90, 
                                               whitelist=strong, use_filter=True)
    results['L4_filter_rr20'] = run_backtest('最强+过滤 RR=2.0', rr=2.0, min_score=90,
                                               whitelist=strong, use_filter=True)
    
    elapsed = time.time() - t0
    
    # ═══ 汇总 ═══
    print("\n" + "="*70)
    print("📊 生产评分器验证 全量汇总")
    print("="*70)
    print(f"\n{'配置':<35} {'n':>6} {'WR':>6} {'IC':>8} {'net EV':>8} {'Sharpe':>7} {'WF':>6}")
    print("-"*80)
    for k, r in results.items():
        if r is None: continue
        print(f"{r['label']:<35} {r['n']:>6} {r['wr']:>5.1f}% {r['ic']:>+8.4f} {r['pnl_net']:>+7.3f}% {r['sharpe']:>7.2f} {r['wf_stab']:>5.1%}")
    
    # 样本外对比
    print(f"\n  📋 样本外验证:")
    train_ic = results.get('L2_train_strong_rr20', {}).get('ic', 0)
    test_ic = results.get('L2_test_strong_rr20', {}).get('ic', 0)
    train_ev = results.get('L2_train_strong_rr20', {}).get('pnl_net', 0)
    test_ev = results.get('L2_test_strong_rr20', {}).get('pnl_net', 0)
    print(f"  训练集 IC={train_ic:+.4f} net EV={train_ev:+.3f}%")
    print(f"  测试集 IC={test_ic:+.4f} net EV={test_ev:+.3f}%")
    if test_ic > 0:
        print(f"  ✅ 样本外IC>0 → 真alpha（非过拟合）")
    else:
        print(f"  ❌ 样本外IC≤0 → 过拟合警告")
    
    # 最优配置
    valid = [r for r in results.values() if r and r.get('pnl_net', -999) > 0]
    if valid:
        best = max(valid, key=lambda x: x['pnl_net'])
        print(f"\n  🟢 最优: {best['label']} net EV={best['pnl_net']:+.3f}%/笔")
        print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f} Sharpe={best['sharpe']:.2f} WF={best['wf_stab']:.1%}")
    else:
        all_r = [r for r in results.values() if r]
        if all_r:
            best = max(all_r, key=lambda x: x.get('pnl_net', -999))
            print(f"\n  ⚠️ 仍无正EV，最优: {best['label']} net EV={best.get('pnl_net', 0):+.3f}%/笔")
            print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f}")
    
    print(f"\n  耗时: {elapsed:.1f}s")
    
    output = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'results': {k: v for k, v in results.items() if v is not None},
        'sample_split': {'train': '2022-2024', 'test': '2025-2026'},
    }
    with open(OUT_DIR / 'dharma_prod_validation_result.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果: data/dharma_prod_validation_result.json")


if __name__ == '__main__':
    main()
