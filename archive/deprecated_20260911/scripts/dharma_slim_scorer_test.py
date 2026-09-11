"""
dharma_slim_scorer_test.py — 精简评分器回测验证
苏摩111 2026-09-09

盲测IC结论：
  regime_direction  IC=+0.0146 ← 保留
  rsi_extreme       IC=+0.0143 ← 保留
  bb_squeeze        IC=+0.0137 ← 保留
  resonance         IC=+0.0024 ← 保留(弱正)
  fvg_match         IC=-0.0030 ← 砍
  ob_valid          IC=-0.0056 ← 砍
  vol_surge         IC=-0.0064 ← 砍
  liq_density       IC=-0.0131 ← 砍

从94维砍到4维。用同样的回测管线验证。
"""
import gzip, json, math, time, os, sys
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR = BASE / "data"
COST = 0.0008; SLIPPAGE = 0.0005

# 复用v3引擎的指标函数
exec(open(BASE / "scripts" / "dharma_ultimate_validator.py").read().split("def simulate_exit_ultimate")[0])

# ═══ 精简评分器 v5_slim ═══
def score_v5_slim(regime, direction, rsi, resonance, bb_w):
    """
    4维精简评分器，只用盲测IC>0的维度。
    
    维度1: regime_direction (IC=+0.0146) — 最大权重
    维度2: rsi_extreme       (IC=+0.0143) — 次大权重
    维度3: bb_squeeze        (IC=+0.0137) — 第三
    维度4: resonance         (IC=+0.0024) — 弱正，小权重
    
    总分范围: 0~150（和原版同量级）
    """
    s = 50.0  # 基准分
    
    # 维度1: regime×direction (+30/-25/0)
    bonus = {
        ('BULL_TREND','LONG'): 30,  ('BULL_TREND','SHORT'): -20,
        ('BEAR_TREND','SHORT'): 30, ('BEAR_TREND','LONG'): -20,
        ('BULL_EARLY','LONG'): 25,  ('BULL_EARLY','SHORT'): -15,
        ('BEAR_RECOVERY','LONG'): 15, ('BEAR_RECOVERY','SHORT'): -25,
        ('CHOP_MID','LONG'): 0,     ('CHOP_MID','SHORT'): 0,
        ('CHOP_HIGH','LONG'): 5,    ('CHOP_HIGH','SHORT'): 5,
    }
    s += bonus.get((regime, direction), 0)
    
    # 维度2: RSI极端 (+15/-15)
    if direction == 'LONG':
        if rsi and rsi < 30: s += 15
        elif rsi and rsi > 70: s -= 15
    else:
        if rsi and rsi > 70: s += 15
        elif rsi and rsi < 30: s -= 15
    
    # 维度3: BB压缩 (+12/-8)
    if bb_w < 1.0: s += 12
    elif bb_w > 5.0: s -= 8
    
    # 维度4: 共振 (+10/-3，弱权重)
    if resonance['score'] >= 3: s += 10
    elif resonance['score'] == 2: s += 5
    elif resonance['score'] == 0: s -= 3
    
    return max(0, min(150, s))


# ═══ 精简门控 ═══
def apply_gates_slim(regime, direction, score, resonance):
    """精简门控：只保留死穴+死区+最低分"""
    if regime == 'BEAR_TREND' and direction == 'LONG':
        return True, 'BEAR_TREND_LONG'
    if regime in DEAD_ZONE and 130 <= score < 145:
        return True, 'DEAD_ZONE'
    if score < 85:
        return True, 'LOW_SCORE'
    if regime == 'BEAR_RECOVERY' and direction == 'SHORT':
        return True, 'BEAR_REC_SHORT'
    # 去掉共振门控（IC太弱，不强制要求）
    return False, ''


# ═══ 出场：两种模式对比 ═══
def sim_exit_with_be(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    """原版出场：有浮盈保护"""
    sl_pct = 0.02
    hold = {'15m': 48, '1h': 24, '4h': 12, '1d': 5}.get(tf, 24)
    min_sl = 1.5 * atr if atr else entry_price * 0.01
    sl_dist = max(entry_price * sl_pct, min_sl)
    
    if direction == 'LONG':
        sl = entry_price - sl_dist; tp = entry_price + sl_dist * rr
    else:
        sl = entry_price + sl_dist; tp = entry_price - sl_dist * rr
    
    be_trigger = 2.5 * atr if atr else sl_dist * 1.5
    sl_moved = False
    
    for i in range(entry_idx + 1, min(entry_idx + hold + 1, len(bars))):
        b = bars[i]
        if not sl_moved:
            if direction == 'LONG' and b['h'] - entry_price >= be_trigger:
                sl = entry_price; sl_moved = True
            elif direction == 'SHORT' and entry_price - b['l'] >= be_trigger:
                sl = entry_price; sl_moved = True
        
        if direction == 'LONG':
            if b['l'] <= sl:
                g = (sl - entry_price) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'SL' if not sl_moved else 'BE', 'bars_held': i - entry_idx}
            if b['h'] >= tp:
                g = (tp - entry_price) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'TP', 'bars_held': i - entry_idx}
        else:
            if b['h'] >= sl:
                g = (entry_price - sl) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'SL' if not sl_moved else 'BE', 'bars_held': i - entry_idx}
            if b['l'] <= tp:
                g = (entry_price - tp) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'TP', 'bars_held': i - entry_idx}
    
    close = bars[min(entry_idx + hold, len(bars) - 1)]['c']
    g = ((close - entry_price) / entry_price) if direction == 'LONG' else ((entry_price - close) / entry_price)
    return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
            'exit_reason': 'TIME', 'bars_held': hold}


def sim_exit_natural(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    """优化出场：无浮盈保护，自然SL/TP，RR=1.5"""
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
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'SL', 'bars_held': i - entry_idx}
            if b['h'] >= tp:
                g = (tp - entry_price) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'TP', 'bars_held': i - entry_idx}
        else:
            if b['h'] >= sl:
                g = (entry_price - sl) / entry_price
                return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'SL', 'bars_held': i - entry_idx}
            if b['l'] <= tp:
                g = (entry_price - tp) / entry_price
                return {'is_win': True, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
                        'exit_reason': 'TP', 'bars_held': i - entry_idx}
    
    close = bars[min(entry_idx + hold, len(bars) - 1)]['c']
    g = ((close - entry_price) / entry_price) if direction == 'LONG' else ((entry_price - close) / entry_price)
    return {'is_win': g > 0, 'pnl_gross': g * 100, 'pnl_net': (g - COST - SLIPPAGE) * 100,
            'exit_reason': 'TIME', 'bars_held': hold}


# ═══ 信号生成 ═══
def gen_signals_slim(bars, rmap, tf, exit_mode='natural', rr=1.5, start_ts=0):
    """用精简评分器生成信号"""
    atrs = calc_atr(bars, 14)
    rsis = calc_rsi(bars, 14)
    min_gap = {'15m': 4 * 15 * 60 * 1000, '1h': 4 * 3600 * 1000, '4h': 2 * 4 * 3600 * 1000}.get(tf, 4 * 3600 * 1000)
    sigs = []; last_ts = 0
    
    for i in range(100, len(bars) - 50):
        b = bars[i]; ts = b['ts']
        if ts - last_ts < min_gap: continue
        if ts < start_ts: continue
        
        # V03: 体制用前一根4H
        ts_4h = (ts // (4 * 3600 * 1000)) * (4 * 3600 * 1000) - 4 * 3600 * 1000
        regime = rmap.get(ts_4h)
        if not regime:
            for off in range(-3, 4):
                regime = rmap.get(ts_4h + off * 4 * 3600 * 1000)
                if regime: break
        if not regime: continue
        
        atr = atrs[i]; rsi = rsis[i]
        if not atr or not rsi: continue
        
        # BB宽度
        if i >= 20:
            cl = [bars[j]['c'] for j in range(i - 20, i)]
            mc = sum(cl) / 20; sc = math.sqrt(sum((c - mc) ** 2 for c in cl) / 20)
            bb_w = sc * 2 / mc * 100 if mc else 0
        else:
            bb_w = 0
        
        # 共振（保留，但权重小）
        fvg = detect_fvg_advanced(bars, i)
        ob = detect_ob_advanced(bars, i)
        liq = build_liquidity_map(bars, i)
        res = detect_resonance(bars, i, fvg, ob, liq, atr)
        
        for d in ['LONG', 'SHORT']:
            # 精简评分
            sc_val = score_v5_slim(regime, d, rsi, res, bb_w)
            gated, reason = apply_gates_slim(regime, d, sc_val, res)
            if gated: continue
            
            ms = {'15m': 90, '1h': 95, '4h': 100}.get(tf, 95)
            if sc_val < ms: continue
            
            # V03: 入场用下一根开盘价
            ni = i + 1
            if ni >= len(bars): continue
            ep = bars[ni]['o']
            
            if exit_mode == 'be':
                er = sim_exit_with_be(bars, ni, d, ep, atr, tf, rr=rr)
            else:
                er = sim_exit_natural(bars, ni, d, ep, atr, tf, rr=rr)
            
            sigs.append({
                'ts': ts, 'score': round(sc_val, 1), 'resonance': res['score'],
                'direction': d, 'regime': regime, 'tf': tf,
                'rsi': round(rsi, 1) if rsi else None,
                'bb_w': round(bb_w, 3),
                'entry': round(ep, 2), **er
            })
            last_ts = ts
    return sigs


def calc_ic(sigs):
    if len(sigs) < 10: return 0.0
    s = [s['score'] for s in sigs]; o = [1.0 if s['is_win'] else 0.0 for s in sigs]
    n = len(s); ms = sum(s) / n; mo = sum(o) / n
    cov = sum((s[i] - ms) * (o[i] - mo) for i in range(n)) / n
    ss = math.sqrt(sum((x - ms) ** 2 for x in s) / n); so = math.sqrt(sum((x - mo) ** 2 for x in o) / n)
    return round(cov / (ss * so), 4) if ss and so else 0.0


def run_backtest(exit_mode='natural', rr=1.5, label=''):
    """跑完整回测"""
    start_ts = int(datetime.strptime('2022-01-01', '%Y-%m-%d').timestamp() * 1000)
    all_sigs = []
    
    for sym in ['BTC', 'ETH']:
        SYM = sym.upper() + 'USDT'
        for tf in ['15m', '1h']:
            bars = load_klines(SYM, tf, start_ts)
            rmap = load_regime_labels(SYM)
            if not bars: continue
            sigs = gen_signals_slim(bars, rmap, tf, exit_mode=exit_mode, rr=rr, start_ts=start_ts)
            n = len(sigs)
            wr = sum(1 for s in sigs if s['is_win']) / n * 100 if n else 0
            print(f"  {SYM}/{tf}: n={n} WR(gross)={wr:.1f}%")
            all_sigs.extend(sigs)
    
    n = len(all_sigs)
    if n == 0:
        print(f"  ❌ 无信号生成")
        return None
    
    wr_gross = sum(1 for s in all_sigs if s['is_win']) / n * 100
    ic = calc_ic(all_sigs)
    pnl_gross = sum(s['pnl_gross'] for s in all_sigs) / n
    pnl_net = sum(s['pnl_net'] for s in all_sigs) / n
    
    exit_dist = defaultdict(int)
    for s in all_sigs: exit_dist[s['exit_reason']] += 1
    
    # 盈亏平衡
    be_wr = (0.02 + COST + SLIPPAGE) / (0.02 * (rr + 1)) * 100
    
    # PnL模拟
    nav = 100000; pos = nav * 0.05 * 5; fee = pos * (COST + SLIPPAGE) * 2
    sl_dist = 0.02
    total_pnl = 0; cum = 0; peak = 0; max_dd = 0
    pnl_list = []
    for s in all_sigs:
        if s['is_win']: p = sl_dist * rr * pos - fee
        else: p = -sl_dist * pos - fee
        total_pnl += p; cum += p
        if cum > peak: peak = cum
        if peak - cum > max_dd: max_dd = peak - cum
        pnl_list.append(p)
    mean_p = sum(pnl_list) / len(pnl_list) if pnl_list else 0
    std_p = math.sqrt(sum((p - mean_p) ** 2 for p in pnl_list) / len(pnl_list)) if len(pnl_list) > 1 else 0
    tpy = min(n / 4.5, 5 * 365)
    sharpe = mean_p / std_p * math.sqrt(tpy) if std_p > 0 else 0
    
    # Walk-Forward
    ts_min = min(s['ts'] for s in all_sigs)
    ts_max = max(s['ts'] for s in all_sigs)
    total_m = (ts_max - ts_min) / (86400 * 30 * 1000)
    bpm = n / total_m
    n_folds = min(19, max(3, int((total_m - 24 - 6) / 3) + 1)) if total_m > 30 else 3
    folds = []
    for f in range(n_folds):
        te = int((f * 3 + 24) * bpm); ed = int((f * 3 + 30) * bpm)
        fs = all_sigs[te:ed]
        if len(fs) < 10: continue
        wr_f = sum(1 for s in fs if s['is_win']) / len(fs) * 100
        ev_f = sum(s['pnl_net'] for s in fs) / len(fs)
        folds.append({'fold': f + 1, 'n': len(fs), 'wr': round(wr_f, 1), 'ev_net': round(ev_f, 3)})
    pf = sum(1 for f in folds if f['wr'] >= 52)
    ff = sum(1 for f in folds if f['wr'] < 50)
    stab = pf / len(folds) if folds else 0
    
    # 跨标的
    cross = {}
    for sym in ['SOL', 'BNB', 'ADA', 'XRP', 'DOGE']:
        SYM = sym.upper() + 'USDT'
        sym_s = []
        for tf in ['15m', '1h']:
            bars = load_klines(SYM, tf, start_ts)
            rmap = load_regime_labels(SYM)
            if not bars: continue
            sigs = gen_signals_slim(bars, rmap, tf, exit_mode=exit_mode, rr=rr, start_ts=start_ts)
            sym_s.extend(sigs)
        n_s = len(sym_s)
        wr_s = sum(1 for s in sym_s if s['is_win']) / n_s * 100 if n_s else 0
        ev_s = sum(s['pnl_net'] for s in sym_s) / n_s if n_s else 0
        cross[sym] = {'n': n_s, 'wr': round(wr_s, 1), 'ev_net': round(ev_s, 3)}
        print(f"    {sym}: n={n_s} WR={wr_s:.1f}% EV(net)={ev_s:+.3f}%")
    
    pass_cross = sum(1 for v in cross.values() if v['wr'] >= 52 and v['n'] >= 100)
    
    print(f"\n  📊 {label}")
    print(f"  总信号: {n}")
    print(f"  WR(gross): {wr_gross:.1f}%")
    print(f"  IC: {ic:+.4f}")
    print(f"  每笔gross: {pnl_gross:+.3f}%")
    print(f"  每笔net: {pnl_net:+.3f}% ← {'✅ 正EV=赚钱' if pnl_net > 0 else '❌ 负EV=亏损'}")
    print(f"  出场分布: {dict(exit_dist)}")
    print(f"  盈亏平衡WR: {be_wr:.1f}% | 安全垫: {wr_gross - be_wr:+.1f}%")
    print(f"  4.5年PnL: ${total_pnl:,.0f} | Sharpe: {sharpe:.2f} | 回撤: ${max_dd:,.0f}")
    print(f"  Walk-Forward: {len(folds)}折, 稳定性={stab:.1%}, 通过={pf}, 失败={ff}")
    for f in folds:
        flag = '✅' if f['wr'] >= 52 else ('⚠️' if f['wr'] >= 50 else '❌')
        print(f"    {flag} Fold{f['fold']:>2}: n={f['n']:>5} WR={f['wr']:>5.1f}% EV(net)={f['ev_net']:>+.3f}%")
    print(f"  跨标的: {pass_cross}/5 WR≥52%")
    
    return {
        'n': n, 'wr': round(wr_gross, 1), 'ic': ic,
        'pnl_gross': round(pnl_gross, 3), 'pnl_net': round(pnl_net, 3),
        'sharpe': round(sharpe, 2), 'max_dd': round(max_dd, 0),
        'total_pnl': round(total_pnl, 0), 'be_wr': round(be_wr, 1),
        'folds': folds, 'cross': cross,
        'exit_mode': exit_mode, 'rr': rr,
        'pass_cross': pass_cross, 'wf_stability': round(stab, 3),
        'wf_pass': pf, 'wf_fail': ff,
    }


def main():
    t0 = time.time()
    
    # ═══ 配置1: 精简评分 + 浮盈保护 + RR=1.5（原版出场） ═══
    print("=" * 70)
    print("🔧 配置1: 精简评分(4维) + 浮盈保护 + RR=1.5")
    print("=" * 70)
    r1 = run_backtest(exit_mode='be', rr=1.5, label='精简4维 + 浮盈 + RR=1.5')
    
    # ═══ 配置2: 精简评分 + 自然出场 + RR=1.5 ═══
    print("\n" + "=" * 70)
    print("🔧 配置2: 精简评分(4维) + 自然出场 + RR=1.5")
    print("=" * 70)
    r2 = run_backtest(exit_mode='natural', rr=1.5, label='精简4维 + 自然 + RR=1.5')
    
    # ═══ 配置3: 精简评分 + 自然出场 + RR=1.2 ═══
    print("\n" + "=" * 70)
    print("🔧 配置3: 精简评分(4维) + 自然出场 + RR=1.2")
    print("=" * 70)
    r3 = run_backtest(exit_mode='natural', rr=1.2, label='精简4维 + 自然 + RR=1.2')
    
    # ═══ 配置4: 精简评分 + 浮盈保护 + RR=1.2 ═══
    print("\n" + "=" * 70)
    print("🔧 配置4: 精简评分(4维) + 浮盈保护 + RR=1.2")
    print("=" * 70)
    r4 = run_backtest(exit_mode='be', rr=1.2, label='精简4维 + 浮盈 + RR=1.2')
    
    elapsed = time.time() - t0
    
    # ═══ 对比汇总 ═══
    print("\n" + "=" * 70)
    print("📊 精简评分器 vs 原版对比汇总")
    print("=" * 70)
    
    configs = [
        ('精简+浮盈+RR1.5', r1),
        ('精简+自然+RR1.5', r2),
        ('精简+自然+RR1.2', r3),
        ('精简+浮盈+RR1.2', r4),
        ('原版+浮盈+RR1.5(基线)', {'n': 63958, 'wr': 55.2, 'ic': 0.0197, 'pnl_gross': 0.128, 'pnl_net': -0.137, 'sharpe': -0.7, 'max_dd': 0, 'total_pnl': 0}),
        ('原版+自然+RR1.2(修复D)', {'n': 63958, 'wr': 49.7, 'ic': -0.0027, 'pnl_gross': 0.061, 'pnl_net': -0.069, 'sharpe': -1.4, 'max_dd': 1225790, 'total_pnl': -1155170}),
    ]
    
    print(f"{'配置':<28} {'n':>7} {'WR':>6} {'IC':>8} {'gross':>8} {'net':>8} {'Sharpe':>7}")
    print("-" * 75)
    for name, r in configs:
        if r is None:
            print(f"{name:<28} {'N/A':>7}")
            continue
        print(f"{name:<28} {r.get('n',0):>7} {r.get('wr',0):>5.1f}% {r.get('ic',0):>+8.4f} {r.get('pnl_gross',0):>+7.3f}% {r.get('pnl_net',0):>+7.3f}% {r.get('sharpe',0):>7.2f}")
    
    # 找最优
    valid = [r for r in [r1, r2, r3, r4] if r and r['pnl_net'] > 0]
    if valid:
        best = max(valid, key=lambda x: x['pnl_net'])
        print(f"\n  🟢 最优配置: net EV={best['pnl_net']:+.3f}%/笔 → 能赚钱！")
        print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f} Sharpe={best['sharpe']:.2f}")
    else:
        all_neg = [r for r in [r1, r2, r3, r4] if r]
        if all_neg:
            best = max(all_neg, key=lambda x: x['pnl_net'])
            print(f"\n  ⚠️ 所有配置仍亏损，最优: net EV={best['pnl_net']:+.3f}%/笔")
            print(f"     WR={best['wr']:.1f}% IC={best['ic']:+.4f}")
            print(f"     → 需要进一步调优（SL/TP参数、门控阈值等）")
    
    print(f"\n  耗时: {elapsed:.1f}s")
    
    # 写出
    output = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'configs': {
            'slim_be_rr15': r1,
            'slim_natural_rr15': r2,
            'slim_natural_rr12': r3,
            'slim_be_rr12': r4,
        },
        'baseline': {
            'original_be_rr15': {'wr': 55.2, 'ic': 0.0197, 'pnl_net': -0.137},
            'original_natural_rr12': {'wr': 49.7, 'ic': -0.0027, 'pnl_net': -0.069},
        },
        'scorer': 'v5_slim: 4维 (regime_direction + rsi_extreme + bb_squeeze + resonance)',
    }
    with open(OUT_DIR / 'dharma_slim_scorer_result.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果: data/dharma_slim_scorer_result.json")


if __name__ == '__main__':
    main()
