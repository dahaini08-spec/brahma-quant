#!/usr/bin/env python3
"""
brahma_real_backtest_v3.py — 梵天体制识别 × 历史K线 真实WR统计
2026-08-27 设计院封印

核心方法：
  - 用4H K线计算EMA体制（BULL/BEAR/CHOP/RECOVERY）
  - 用1H K线计算RSI/BBW/MACD指标
  - 体制→方向映射（SOUL.md SSOT）
  - 24H后价格验证真实WR

联合验证：
  - fangcang_cases_btc.json / fangcang_cases_eth.json
  - 方仓压缩(BBW<2%)时梵天能否识别正确方向

目标：
  证明梵天体制识别层的真实Alpha
  量化：体制层WR vs 达摩院简化版39.4%
"""

import sys, os, gzip, json, time
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
DATA = ROOT / 'data'
HIST = DATA / 'historical'

sys.path.insert(0, str(ROOT / 'brahma_brain'))
sys.path.insert(0, str(ROOT))

print("加载历史K线...")
ROWS = {}
for sym in ['BTCUSDT', 'ETHUSDT']:
    for tf in ['1h', '4h', '15m']:
        p = HIST / f'{sym}_{tf}.jsonl.gz'
        if p.exists():
            with gzip.open(str(p), 'rt') as f:
                rows = [json.loads(l.strip()) for l in f if l.strip()]
            rows.sort(key=lambda x: x['ts'])
            ROWS[f'{sym}:{tf}'] = rows
            print(f"  {sym} {tf}: {len(rows)} bars")

# ── 纯K线体制识别 ─────────────────────────────────────────────

def calc_regime(rows_4h, ts_ms, lookback=60) -> str:
    """用4H EMA识别体制（无需实时API）"""
    idx = -1
    lo, hi = 0, len(rows_4h)-1
    while lo <= hi:
        m = (lo+hi)//2
        if rows_4h[m]['ts'] <= ts_ms: idx = m; lo = m+1
        else: hi = m-1
    if idx < lookback:
        return 'UNKNOWN'
    sl = rows_4h[max(0, idx-lookback): idx+1]
    s = pd.Series([float(r['c']) for r in sl])
    ema9  = s.ewm(span=9,  adjust=False).mean().iloc[-1]
    ema21 = s.ewm(span=21, adjust=False).mean().iloc[-1]
    ema50 = s.ewm(span=50, adjust=False).mean().iloc[-1]
    c = s.iloc[-1]
    ret7d = (c - s.iloc[-42]) / s.iloc[-42] * 100 if len(s) >= 42 else 0
    if ema9 > ema21 > ema50 and ret7d > 5:    return 'BULL_TREND'
    elif ema9 > ema21 and ret7d > -2:          return 'BULL_EARLY'
    elif ema9 < ema21 < ema50 and ret7d < -5:  return 'BEAR_TREND'
    elif ema9 < ema21 and ret7d > -3:          return 'BEAR_RECOVERY'
    else:                                       return 'CHOP_MID'

def calc_indicators(rows_1h, ts_ms, lookback=200):
    """从1H K线计算RSI/BBW/MACD"""
    idx = -1
    lo, hi = 0, len(rows_1h)-1
    while lo <= hi:
        m = (lo+hi)//2
        if rows_1h[m]['ts'] <= ts_ms: idx = m; lo = m+1
        else: hi = m-1
    if idx < 50:
        return {}
    sl = rows_1h[max(0, idx-lookback): idx+1]
    closes = [float(r['c']) for r in sl]
    s = pd.Series(closes)
    
    # RSI 14
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
    rsi = 100 - 100 / (1 + rs)
    
    # BBW
    sma20 = s[-20:].mean()
    std20 = s[-20:].std()
    bbw = (4 * std20 / sma20 * 100) if sma20 > 0 else 5.0
    
    # MACD
    ema12 = s.ewm(span=12).mean().iloc[-1]
    ema26 = s.ewm(span=26).mean().iloc[-1]
    macd_hist = (ema12 - ema26) / s.iloc[-1] * 100 if s.iloc[-1] > 0 else 0
    
    # 动量
    ret24h = (s.iloc[-1] - s.iloc[-25]) / s.iloc[-25] * 100 if len(s) >= 25 else 0
    
    # ATR
    trs = []
    for i in range(1, min(15, len(sl))):
        h, l, pc = float(sl[-i]['h']), float(sl[-i]['l']), float(sl[-i-1]['c'])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    atr = np.mean(trs) if trs else 0
    atr_pct = atr / s.iloc[-1] * 100 if s.iloc[-1] > 0 else 1.0
    
    return {
        'rsi': round(float(rsi), 1),
        'bbw': round(float(bbw), 2),
        'macd_hist': round(float(macd_hist), 4),
        'ret24h': round(float(ret24h), 2),
        'atr_pct': round(float(atr_pct), 3),
        'price': float(s.iloc[-1]),
    }

# ── 梵天体制→策略映射 ─────────────────────────────────────────

REGIME_DIR = {
    'BULL_TREND':    ('LONG',  1.6, 0.15),
    'BULL_EARLY':    ('LONG',  1.0, 0.35),
    'BEAR_TREND':    ('SHORT', 1.6, 0.10),
    'BEAR_EARLY':    ('SHORT', 1.2, 0.35),
    'BEAR_RECOVERY': ('LONG',  1.2, 0.30),
    'CHOP_MID':      (None,    0.88, 0.50),
}

def get_direction(regime, rsi=50, bbw=5) -> str:
    """
    梵天方向决策：
    1. 体制映射（SSOT）
    2. RSI极值修正（超买/超卖时不追）
    3. 返回 LONG/SHORT/NONE
    """
    info = REGIME_DIR.get(regime)
    if not info or info[0] is None:
        return 'NONE'
    
    direction = info[0]
    
    # RSI修正
    if direction == 'LONG' and rsi > 72:
        return 'NONE'  # 严重超买，不追多
    if direction == 'SHORT' and rsi < 28:
        return 'NONE'  # 严重超卖，不追空
    
    return direction

def get_score_estimate(regime, rsi, bbw, direction) -> int:
    """
    评分估算（仅用K线层指标，不含AI议会/链上/HCME等高级层）
    这是「基础技术层」的评分
    """
    base = {'BULL_TREND': 105, 'BEAR_TREND': 108,
            'BULL_EARLY': 90, 'BEAR_EARLY': 92,
            'BEAR_RECOVERY': 115, 'CHOP_MID': 65}.get(regime, 70)
    
    # BBW方仓压缩 +15/+8
    if bbw < 2.0:   base += 15
    elif bbw < 3.0: base += 8
    
    # RSI确认
    if direction == 'LONG'  and rsi < 40: base += 10
    if direction == 'SHORT' and rsi > 60: base += 10
    
    # RSI矛盾 -5
    if direction == 'LONG'  and rsi > 65: base -= 5
    if direction == 'SHORT' and rsi < 35: base -= 5
    
    return int(base)

# ── 回测主循环 ─────────────────────────────────────────────────

def run_backtest(symbol, max_samples=400):
    rows_1h = ROWS.get(f'{symbol}:1h', [])
    rows_4h = ROWS.get(f'{symbol}:4h', [])
    if not rows_1h or not rows_4h:
        print(f"  ❌ {symbol}: 缺少数据")
        return []
    
    CONTEXT = 200
    FUTURE_H = 24
    STEP_H = 8  # 每8H一个快照
    
    start_idx = CONTEXT
    end_idx = len(rows_1h) - FUTURE_H - 2
    indices = list(range(start_idx, end_idx, STEP_H))
    
    # 均匀抽样
    if len(indices) > max_samples:
        step_s = len(indices) // max_samples
        indices = indices[::step_s][:max_samples]
    
    print(f"\n{symbol}: {len(indices)} 个快照")
    
    results = []
    skipped_none = 0
    t0 = time.time()
    
    for n, idx in enumerate(indices):
        ts_ms = rows_1h[idx]['ts']
        snap_price = rows_1h[idx]['c']
        
        if n % 50 == 0:
            dt = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime('%Y-%m')
            print(f"  [{n+1}/{len(indices)}] {dt} p={snap_price:.0f}", end='\r')
        
        # 体制识别（4H K线）
        regime = calc_regime(rows_4h, ts_ms)
        if regime == 'UNKNOWN':
            continue
        
        # 技术指标（1H K线）
        ind = calc_indicators(rows_1h, ts_ms)
        if not ind:
            continue
        
        rsi = ind['rsi']
        bbw = ind['bbw']
        
        # 方向决策
        direction = get_direction(regime, rsi, bbw)
        if direction == 'NONE':
            skipped_none += 1
            continue
        
        # 评分估算
        score_est = get_score_estimate(regime, rsi, bbw, direction)
        
        # 24H后验证
        future_idx = idx + FUTURE_H
        if future_idx >= len(rows_1h):
            continue
        future_price = rows_1h[future_idx]['c']
        pnl = (future_price - snap_price) / snap_price * 100
        correct = (pnl > 0) if direction == 'LONG' else (pnl < 0)
        
        results.append({
            'symbol': symbol,
            'ts': ts_ms,
            'date': datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d'),
            'price': snap_price,
            'future_price': future_price,
            'pnl': round(pnl, 3),
            'regime': regime,
            'direction': direction,
            'rsi': rsi,
            'bbw': bbw,
            'score_est': score_est,
            'pass_sqe': score_est >= 120,
            'pass_elite': score_est >= 140,
            'correct': correct,
        })
    
    elapsed = time.time() - t0
    print(f"\n  ✅ {symbol}: {len(results)} 笔有效  {skipped_none} 笔NONE  {elapsed:.1f}s")
    return results

# ── 统计 ─────────────────────────────────────────────────────────

def calc_matrix(results):
    m = defaultdict(lambda: {'wins': 0, 'total': 0, 'pnls': []})
    for r in results:
        reg, dir_, score = r['regime'], r['direction'], r['score_est']
        
        for key in [
            f"ALL:{dir_}",
            f"{reg}:{dir_}",
        ]:
            m[key]['total'] += 1
            m[key]['pnls'].append(r['pnl'])
            if r['correct']: m[key]['wins'] += 1
        
        if r['pass_sqe']:
            k = f"SQE:{reg}:{dir_}"
            m[k]['total'] += 1; m[k]['pnls'].append(r['pnl'])
            if r['correct']: m[k]['wins'] += 1
        
        if r['bbw'] < 2.0:
            k = f"FCG:{reg}:{dir_}"  # FCG=方仓
            m[k]['total'] += 1; m[k]['pnls'].append(r['pnl'])
            if r['correct']: m[k]['wins'] += 1
    
    out = {}
    for k, v in m.items():
        n = v['total']
        if n < 3: continue
        pnls = v['pnls']
        wins_pnl = [p for p in pnls if p > 0]
        loss_pnl = [p for p in pnls if p < 0]
        out[k] = {
            'wr':       round(v['wins']/n, 4),
            'n':        n,
            'avg_pnl':  round(sum(pnls)/n, 3),
            'avg_win':  round(sum(wins_pnl)/len(wins_pnl), 3) if wins_pnl else 0,
            'avg_loss': round(sum(loss_pnl)/len(loss_pnl), 3) if loss_pnl else 0,
            'iron_proof': n >= 20,
        }
    return out

# ── 方仓联合验证 ─────────────────────────────────────────────────

def run_fangcang(symbol='BTC', max_cases=100):
    sym_usdt = f'{symbol.upper()}USDT'
    rows_1h = ROWS.get(f'{sym_usdt}:1h', [])
    rows_4h = ROWS.get(f'{sym_usdt}:4h', [])
    if not rows_1h:
        return []
    
    p = DATA / f'fangcang_cases_{symbol.lower()}.json'
    if not p.exists():
        return []
    with open(p) as f:
        cases = json.load(f)
    
    try:
        from dateutil import parser as _dp
    except ImportError:
        return []
    
    print(f"\n方仓验证: {symbol} ({min(max_cases, len(cases))} 案例)")
    results = []
    
    for n, case in enumerate(cases[:max_cases]):
        ts_str = str(case.get('ts_burst') or case.get('ts_squeeze_start', ''))
        if not ts_str: continue
        try:
            ts_ms = int(_dp.parse(ts_str).timestamp() * 1000)
        except Exception:
            continue
        
        regime = calc_regime(rows_4h, ts_ms)
        if regime == 'UNKNOWN': continue
        ind = calc_indicators(rows_1h, ts_ms)
        if not ind: continue
        
        direction = get_direction(regime, ind['rsi'], ind['bbw'])
        score_est = get_score_estimate(regime, ind['rsi'], ind['bbw'], direction) if direction != 'NONE' else 50
        
        true_dir = case.get('direction', 'UP')
        true_ret = float(case.get('future_return_24h', 0))
        is_genuine = bool(case.get('is_genuine_breakout', False))
        
        brahma_bull = direction == 'LONG'
        case_bull   = true_dir == 'UP'
        match       = brahma_bull == case_bull if direction != 'NONE' else False
        
        results.append({
            'symbol': symbol,
            'date': ts_str[:10],
            'fangcang_dir': true_dir,
            'fangcang_return': round(true_ret, 3),
            'is_genuine': is_genuine,
            'bbw': ind['bbw'],
            'rsi': ind['rsi'],
            'regime': regime,
            'direction': direction,
            'score_est': score_est,
            'direction_match': match,
            'double_confirm': match and is_genuine,
        })
    
    print(f"  ✅ {symbol}: {len(results)} 案例")
    return results

# ── 主程序 ─────────────────────────────────────────────────────────

def main():
    print("\n" + "="*68)
    print("梵天体制识别 × 方仓数据库 联合真实回测 v3")
    print("="*68)
    
    all_results = []
    
    # Part 1: 体制识别回测
    print("\n【Part 1】体制识别 + 方向预判回测（400快照×2标的）")
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = run_backtest(sym, max_samples=400)
        all_results.extend(r)
        with open(DATA / f'bt_v3_{sym.lower()}.jsonl', 'w') as f:
            for row in r:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
    
    # Part 2: 方仓联合验证
    print("\n【Part 2】方仓案例联合验证")
    fc_all = []
    for sym in ['BTC', 'ETH']:
        fc_all.extend(run_fangcang(sym, max_cases=100))
    with open(DATA / 'fangcang_brahma_v3.json', 'w') as f:
        json.dump(fc_all, f, indent=2, ensure_ascii=False)
    
    # Part 3: 统计
    print("\n" + "="*68)
    print("【Part 3】WR统计结果（体制识别层 vs 达摩院简化版）")
    print("="*68)
    
    if all_results:
        matrix = calc_matrix(all_results)
        with open(DATA / 'brahma_v3_wr.json', 'w') as f:
            json.dump({'matrix': matrix, 'total': len(all_results),
                       'method': '4H EMA体制+1H RSI/BBW，无实时API，历史K线纯本地',
                       'generated': datetime.now(timezone.utc).isoformat()},
                      f, indent=2, ensure_ascii=False)
        
        print(f"\n总样本: {len(all_results)} 笔\n")
        print(f"{'策略键':<40} {'WR':>8} {'n':>6} {'avg_pnl':>10} {'avg_win':>10}")
        print("-"*78)
        
        sorted_m = sorted([(k,v) for k,v in matrix.items() if v['n'] >= 15],
                          key=lambda x: x[1]['wr'], reverse=True)
        for k, v in sorted_m[:30]:
            iron = " ✅" if v['iron_proof'] else "   "
            print(f"{k:<40} {v['wr']:>7.1%} {v['n']:>6} {v['avg_pnl']:>9.3f}% {v['avg_win']:>9.3f}%{iron}")
        
        # 对比基线
        print(f"\n{'='*68}")
        print("核心对比（最重要的数字）：")
        print(f"  达摩院简化版（RSI+MACD盲扫，无体制过滤）: WR=39.4%  EV=-0.002%/笔")
        
        # 总体WR
        all_long = matrix.get('ALL:LONG', {})
        all_short = matrix.get('ALL:SHORT', {})
        n_total = all_long.get('n',0) + all_short.get('n',0)
        n_wins = int(all_long.get('wr',0)*all_long.get('n',0)) + int(all_short.get('wr',0)*all_short.get('n',0))
        if n_total > 0:
            print(f"  梵天体制识别层（体制→方向，无SQE）:          WR={n_wins/n_total:.1%}  n={n_total}")
        
        # SQE过滤
        sqe_r = [(k,v) for k,v in matrix.items() if k.startswith('SQE:')]
        if sqe_r:
            sn = sum(v['n'] for _,v in sqe_r)
            sw = sum(int(v['wr']*v['n']) for _,v in sqe_r)
            print(f"  梵天体制识别 + SQE过滤(评分≥120):          WR={sw/sn:.1%}  n={sn}")
        
        # 方仓强化
        fcg_r = [(k,v) for k,v in matrix.items() if k.startswith('FCG:')]
        if fcg_r:
            fn = sum(v['n'] for _,v in fcg_r)
            fw = sum(int(v['wr']*v['n']) for _,v in fcg_r)
            print(f"  梵天体制识别 + 方仓压缩(BBW<2%):           WR={fw/fn:.1%}  n={fn}")
        
        # 最强体制
        print(f"\n  关键体制WR:")
        for key in ['BEAR_TREND:SHORT', 'BULL_TREND:LONG', 'BEAR_RECOVERY:LONG',
                    'BULL_EARLY:LONG', 'BEAR_TREND:LONG']:
            if key in matrix:
                v = matrix[key]
                print(f"    {key:<30} WR={v['wr']:.1%}  n={v['n']}  avg_pnl={v['avg_pnl']:+.2f}%")
    
    # 方仓结果
    if fc_all:
        print(f"\n{'='*68}")
        print(f"方仓联合验证（{len(fc_all)} 个案例）:")
        print(f"\n  {'层级':<25} {'方向匹配率':>12} {'双重确认率':>12} {'n':>6}")
        print(f"  {'-'*58}")
        tiers = [
            ('精英(score≥140)',   [r for r in fc_all if r['score_est'] >= 140]),
            ('强信号(score≥120)',  [r for r in fc_all if r['score_est'] >= 120]),
            ('方仓压缩(BBW<2%)',   [r for r in fc_all if r['bbw'] < 2.0]),
            ('方仓真突破(genuine)', [r for r in fc_all if r['is_genuine']]),
            ('全量',              fc_all),
        ]
        for name, subset in tiers:
            if not subset: continue
            match = sum(1 for r in subset if r['direction_match']) / len(subset)
            double = sum(1 for r in subset if r['double_confirm']) / len(subset)
            print(f"  {name:<25} {match:>11.1%} {double:>11.1%} {len(subset):>6}")
        
        print(f"\n  关键结论：方向匹配率 > 50% = 梵天在方仓场景有显著预判能力")
        print(f"  随机基线：50%（纯猜上下）")
    
    print(f"\n输出: {DATA}/brahma_v3_wr.json")
    print("="*68)

if __name__ == '__main__':
    main()
