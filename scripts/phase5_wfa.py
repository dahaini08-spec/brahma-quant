#!/usr/bin/env python3
"""
梵天阶段5：Walk-Forward 体制分层滚动验证
"""
import pandas as pd
import numpy as np
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

BASE = Path('/root/.openclaw/workspace/trading-system')
os.makedirs(BASE / 'data/validation', exist_ok=True)

# ── 复用阶段3/4的数学工具 ────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(np.array(closes, dtype=float))
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:period].mean()
    avg_l = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0: return 100.0
    return 100 - (100 / (1 + avg_g / avg_l))

def calc_ema(closes, period):
    if len(closes) < 2: return float(closes[-1])
    k = 2 / (period + 1)
    ema = float(closes[0])
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_bbw(closes, period=20):
    if len(closes) < period: return 3.0
    arr = np.array(closes[-period:], dtype=float)
    mid = arr.mean(); std = arr.std()
    return (4 * std / mid) * 100 if mid != 0 else 3.0

def forward_settle(highs, lows, entry_idx, direction, sl_pct=0.020, rr=1.0, max_bars=48):
    tp_pct = sl_pct * rr
    ep = float(highs[entry_idx] + lows[entry_idx]) / 2  # 用中间价作为入场价代理
    # 实际用收盘价
    if direction == 'SHORT':
        sl_p = ep * (1 + sl_pct); tp_p = ep * (1 - tp_pct)
        for i in range(entry_idx+1, min(entry_idx+max_bars+1, len(highs))):
            if highs[i] >= sl_p: return 'LOSS'
            if lows[i]  <= tp_p: return 'WIN'
    else:
        sl_p = ep * (1 - sl_pct); tp_p = ep * (1 + tp_pct)
        for i in range(entry_idx+1, min(entry_idx+max_bars+1, len(highs))):
            if lows[i]  <= sl_p: return 'LOSS'
            if highs[i] >= tp_p: return 'WIN'
    return 'TIMEOUT'

def simplified_score_direction(closes, highs, lows, regime):
    if len(closes) < 30: return 0, 'NONE'
    price = closes[-1]
    rsi = calc_rsi(closes[-50:])
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, min(50, len(closes)))
    bbw = calc_bbw(closes)
    recent = closes[-100:] if len(closes) >= 100 else closes
    prange = max(recent) - min(recent)
    price_pos = (price - min(recent)) / prange if prange > 0 else 0.5
    
    score = 0; direction = 'NONE'
    
    if regime in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
        direction = 'SHORT'
        if rsi > 70: score += 30
        elif rsi > 60: score += 20
        elif rsi > 50: score += 10
        elif rsi < 35: score -= 20
        if price < ema20 < ema50: score += 25
        elif price < ema20: score += 15
        elif price > ema50: score -= 15
        if 1.0 <= bbw <= 2.5: score += 20
        elif bbw < 0.5: score -= 10
        elif bbw > 5: score -= 5
        if price_pos > 0.75: score += 15
        elif price_pos < 0.25: score -= 15
        if regime == 'BEAR_TREND': score += 20
        elif regime == 'BEAR_EARLY': score += 10
    elif regime in ('BULL_TREND', 'BEAR_RECOVERY'):
        direction = 'LONG'
        if rsi < 30: score += 30
        elif rsi < 40: score += 20
        elif rsi < 50: score += 10
        elif rsi > 70: score -= 20
        if price > ema20 > ema50: score += 25
        elif price > ema20: score += 15
        elif price < ema50: score -= 15
        if 1.0 <= bbw <= 2.5: score += 20
        elif bbw < 0.5: score -= 10
        elif bbw > 5: score -= 5
        if price_pos < 0.25: score += 15
        elif price_pos > 0.75: score -= 15
        if regime == 'BULL_TREND': score += 20
        elif regime == 'BEAR_RECOVERY': score += 10
    return score, direction

# ── WFA主函数 ─────────────────────────────────────────────
def run_wfa(symbol):
    print(f"\n{'='*60}")
    print(f"WFA滚动验证: {symbol.upper()}")
    print('='*60)
    
    sym = symbol.lower()
    df4h = pd.read_parquet(BASE / f'data/historical/{sym}usdt/{sym}usdt_4h.parquet')
    regime_file = BASE / f'data/historical/{sym}usdt_regime_nolookahead.parquet'
    if not regime_file.exists():
        print(f"❌ 缺少体制标签文件"); return None
    
    df_regime = pd.read_parquet(regime_file)
    regime_by_ts = {int(r['ts']): r['regime'] for _, r in df_regime.iterrows() if r.get('reliable', True)}
    ts_list = [int(t.timestamp()*1000) for t in df4h.index]
    regime_map = {}
    for i, ts in enumerate(ts_list):
        if ts in regime_by_ts:
            regime_map[i] = regime_by_ts[ts]
    
    closes_all = df4h['close'].values.astype(float)
    highs_all  = df4h['high'].values.astype(float)
    lows_all   = df4h['low'].values.astype(float)
    dates = df4h.index
    
    # 定义WFA窗口（12月训练+3月测试）
    # 可信起始：约2020-05，以月为单位划分
    reliable_start = dates[min(regime_map.keys())]
    
    # 生成测试窗口列表（3月一组）
    test_windows = []
    cur = reliable_start + pd.DateOffset(months=12)
    end = dates[-1] - pd.DateOffset(months=3)
    while cur <= end:
        test_start = cur
        test_end   = cur + pd.DateOffset(months=3)
        train_start = test_start - pd.DateOffset(months=12)
        test_windows.append((train_start, test_start, test_end))
        cur += pd.DateOffset(months=3)
    
    print(f"WFA窗口数: {len(test_windows)}")
    
    wfa_results = []
    
    for win_idx, (train_s, test_s, test_e) in enumerate(test_windows):
        # 测试窗口内的信号
        test_mask = (dates >= test_s) & (dates < test_e)
        test_indices = np.where(test_mask)[0]
        
        if len(test_indices) < 10:
            continue
        
        # 在测试窗口内运行策略
        window_trades = []
        rsi_prev = 50.0
        
        for i in test_indices:
            regime = regime_map.get(i)
            if not regime: continue
            if i < 50: continue
            
            rsi_cur = calc_rsi(closes_all[max(0,i-50):i+1])
            triggered = False
            if rsi_prev < 50 and rsi_cur >= 55 and regime in ('BULL_TREND','BEAR_RECOVERY'):
                triggered = True
            if rsi_prev > 60 and rsi_cur <= 55 and regime in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
                triggered = True
            if i >= 12:
                h48 = highs_all[i-12:i].max()
                l48 = lows_all[i-12:i].min()
                if closes_all[i] > h48*1.005 and regime in ('BULL_TREND','BEAR_RECOVERY'):
                    triggered = True
                if closes_all[i] < l48*0.995 and regime in ('BEAR_TREND','BEAR_EARLY'):
                    triggered = True
            
            rsi_prev = rsi_cur
            if not triggered: continue
            
            score, direction = simplified_score_direction(
                closes_all[max(0,i-100):i+1],
                highs_all[max(0,i-100):i+1],
                lows_all[max(0,i-100):i+1],
                regime
            )
            if score < 30 or direction == 'NONE': continue
            if regime == 'BEAR_TREND' and direction == 'LONG': continue
            
            result = forward_settle(highs_all, lows_all, i, direction)
            window_trades.append({
                'regime': regime,
                'direction': direction,
                'result': result,
                'win': result == 'WIN',
            })
        
        if len(window_trades) < 5:
            continue
        
        oos_wr = np.mean([t['win'] for t in window_trades])
        
        # 按体制分层
        regime_stats = {}
        for t in window_trades:
            key = f"{t['regime']}:{t['direction']}"
            if key not in regime_stats:
                regime_stats[key] = {'wins': 0, 'total': 0}
            regime_stats[key]['total'] += 1
            if t['win']:
                regime_stats[key]['wins'] += 1
        
        # 主要体制
        dominant = max(regime_stats, key=lambda k: regime_stats[k]['total']) if regime_stats else 'N/A'
        
        wfa_results.append({
            'window': win_idx + 1,
            'test_start': str(test_s)[:10],
            'test_end': str(test_e)[:10],
            'n_trades': len(window_trades),
            'oos_wr': round(float(oos_wr), 4),
            'pass': oos_wr >= 0.52,
            'regime_stats': regime_stats,
            'dominant_regime': dominant,
        })
    
    # ── 统计汇总 ─────────────────────────────────────────
    pass_count = sum(1 for w in wfa_results if w['pass'])
    total_windows = len(wfa_results)
    all_wrs = [w['oos_wr'] for w in wfa_results]
    
    print(f"\nWFA总窗口数: {total_windows}")
    print(f"通过数（OOS WR≥52%）: {pass_count}/{total_windows} ({pass_count/total_windows:.1%})")
    print(f"OOS WR均值: {np.mean(all_wrs):.1%} | 中位数: {np.median(all_wrs):.1%}")
    print(f"OOS WR范围: {min(all_wrs):.1%} ~ {max(all_wrs):.1%}")
    
    print(f"\n{'─'*70}")
    print(f"{'窗口':>4} {'测试期':>22} {'n':>5} {'OOS WR':>8} {'通过':>6}")
    print('─'*70)
    for w in wfa_results:
        flag = '✅' if w['pass'] else '❌'
        print(f"  W{w['window']:02d} {w['test_start']}~{w['test_end']} {w['n_trades']:>5} {w['oos_wr']:>7.1%}  {flag}")
    
    # 体制专项统计
    print(f"\n{'─'*50}")
    print("体制专项WFA（各体制在所有窗口的累计表现）")
    print('─'*50)
    
    regime_agg = {}
    for w in wfa_results:
        for key, stats in w['regime_stats'].items():
            if key not in regime_agg:
                regime_agg[key] = {'wins': 0, 'total': 0}
            regime_agg[key]['wins'] += stats['wins']
            regime_agg[key]['total'] += stats['total']
    
    for key in sorted(regime_agg.keys()):
        s = regime_agg[key]
        if s['total'] < 5: continue
        wr = s['wins'] / s['total']
        flag = '✅' if wr >= 0.52 else ('⚠️' if wr >= 0.48 else '❌')
        print(f"  {flag} {key}: n={s['total']} WR={wr:.1%}")
    
    # 牛熊周期分析
    print(f"\n{'─'*50}")
    print("市场周期分析")
    print('─'*50)
    # 2022熊市窗口
    bear_windows = [w for w in wfa_results if '2022' in w['test_start']]
    # 2023-2024牛市窗口
    bull_windows = [w for w in wfa_results if w['test_start'] >= '2023-10' and w['test_start'] <= '2024-06']
    
    if bear_windows:
        bear_wr = np.mean([w['oos_wr'] for w in bear_windows])
        bear_pass = sum(1 for w in bear_windows if w['pass'])
        print(f"  2022熊市窗口: n={len(bear_windows)} 平均WR={bear_wr:.1%} 通过={bear_pass}/{len(bear_windows)}")
    if bull_windows:
        bull_wr = np.mean([w['oos_wr'] for w in bull_windows])
        bull_pass = sum(1 for w in bull_windows if w['pass'])
        print(f"  2023-24牛市窗口: n={len(bull_windows)} 平均WR={bull_wr:.1%} 通过={bull_pass}/{len(bull_windows)}")
    
    # 最终判定
    print(f"\n{'='*50}")
    overall_pass = pass_count / total_windows >= 0.636  # ≥14/22
    print(f"WFA最终判定: {'✅ 通过' if overall_pass else '❌ 未通过'} ({pass_count}/{total_windows}≥14)")
    
    return {
        'symbol': symbol,
        'total_windows': total_windows,
        'pass_count': int(pass_count),
        'pass_rate': round(pass_count/total_windows, 4) if total_windows > 0 else 0,
        'mean_oos_wr': round(float(np.mean(all_wrs)), 4),
        'median_oos_wr': round(float(np.median(all_wrs)), 4),
        'min_oos_wr': round(float(min(all_wrs)), 4),
        'max_oos_wr': round(float(max(all_wrs)), 4),
        'overall_pass': bool(overall_pass),
        'regime_agg': {k: {'wr': round(v['wins']/v['total'],4), 'n': v['total']} 
                       for k,v in regime_agg.items() if v['total'] >= 5},
        'windows': wfa_results,
    }

# ── 执行 ─────────────────────────────────────────────────
all_results = {}
for sym in ['btc', 'eth']:
    r = run_wfa(sym)
    if r:
        all_results[sym] = r

report_path = BASE / 'data/validation/phase5_wfa_report.json'
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
print(f"\n✅ 报告已保存: {report_path}")
print("阶段5完成")
