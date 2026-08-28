#!/usr/bin/env python3
"""
brahma_real_backtest_v2.py — 梵天真实系统分层回测 (快速版)
2026-08-27 设计院

策略：
  Layer1 (0.38s/次): market_state.analyze() → 体制识别 + 价格水平
  Layer2 (离线): block_a 技术维度评分 (RSI/MACD/BB/EMA/趋势)
  Layer3 (对比): 与方仓案例数据库的 true outcome 对齐

核心问题：
  完整35维分析里，哪些维度是"真Alpha"？
  纯K线技术层(block_a)贡献了多少WR提升？
  vs 达摩院简化版(39.4%)

目标：
  - 快速跑800+个历史快照
  - 证明梵天体制识别的真实预测能力
  - 量化「梵天体制识别」对WR的具体贡献
"""

import sys, os, gzip, json, time
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

ROOT = Path(__file__).parent.parent
DATA = ROOT / 'data'
HIST = DATA / 'historical'

sys.path.insert(0, str(ROOT / 'brahma_brain'))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))

# ── 历史数据加载 ─────────────────────────────────────────────────

def load_gz(path):
    rows = []
    with gzip.open(str(path), 'rt') as f:
        for l in f:
            l = l.strip()
            if l:
                rows.append(json.loads(l))
    rows.sort(key=lambda x: x['ts'])
    return rows

def to_binance_fmt(rows):
    return [[r['ts'], str(r['o']), str(r['h']), str(r['l']), str(r['c']), str(r['v']),
             r['ts']+3599999, str(r.get('qv', 0)), r.get('n', 0),
             str(r.get('tb', 0)), '0', '0'] for r in rows]

print("加载BTC/ETH历史K线...")
HIST_1H = {}
HIST_4H = {}
HIST_15M = {}

for sym in ['BTCUSDT', 'ETHUSDT']:
    p1h = HIST / f'{sym}_1h.jsonl.gz'
    p4h = HIST / f'{sym}_4h.jsonl.gz'
    p15m = HIST / f'{sym}_15m.jsonl.gz'
    if p1h.exists():
        HIST_1H[sym] = load_gz(p1h)
        print(f"  {sym} 1H: {len(HIST_1H[sym])} bars")
    if p4h.exists():
        HIST_4H[sym] = load_gz(p4h)
        print(f"  {sym} 4H: {len(HIST_4H[sym])} bars")
    if p15m.exists():
        HIST_15M[sym] = load_gz(p15m)
        print(f"  {sym} 15M: {len(HIST_15M[sym])} bars")

# ── 注入历史快照 ─────────────────────────────────────────────────

import brahma_brain.data_cache as dc

def inject_snapshot(symbol, snapshot_ts_ms):
    """注入历史K线到data_cache，激活OFFLINE_MODE"""
    dc.OFFLINE_MODE = True
    
    # 清K线缓存（保留其他）
    for k in list(dc._cache.keys()):
        if symbol in k and ('klines' in k or ':1h' in k or ':4h' in k or ':15m' in k):
            del dc._cache[k]
    
    prices = {}
    
    for tf, hist_dict in [('1h', HIST_1H), ('4h', HIST_4H), ('15m', HIST_15M)]:
        rows = hist_dict.get(symbol, [])
        if not rows:
            continue
        # 二分查找快照时间点
        lo, hi, idx = 0, len(rows)-1, -1
        while lo <= hi:
            m = (lo+hi)//2
            if rows[m]['ts'] <= snapshot_ts_ms:
                idx = m; lo = m+1
            else:
                hi = m-1
        if idx < 30:
            continue
        limit = 200 if tf in ['1h', '15m'] else 100
        sl = rows[max(0, idx-limit+1): idx+1]
        bf = to_binance_fmt(sl)
        dc._cache_set(f'{symbol}:{tf}:{limit}', bf, ttl=86400)
        dc._cache_set(f'{symbol}:{tf}', bf, ttl=86400)
        if tf == '1h':
            prices['price'] = float(sl[-1]['c'])
            prices['close'] = float(sl[-1]['c'])
    
    p = prices.get('price', 0)
    if p > 0:
        dc._cache[f'ticker:{symbol}'] = {
            'data': {'lastPrice': str(p), 'price': str(p), 'lastQty': '1'},
            'exp': time.time() + 86400
        }
    dc.OFFLINE_CTX.update({
        'fr': 0.0001, 'oi': 100000, 'oi_change': 0.0,
        'lsr': 50.0, 'top_lsr': 50.0, 'liquidation': 0.0,
        'kronos_p_up': 0.5,
    })
    return p

# ── 梵天体制识别分析 ─────────────────────────────────────────────

from brahma_brain.market_state import analyze as ms_analyze

def analyze_snapshot_fast(symbol, snapshot_ts_ms):
    """
    快速分析（0.3-0.5s/次）：
    - market_state.analyze() → 体制 + 技术指标
    - 返回：regime, rsi_1h, trend, score_estimate, direction
    """
    price = inject_snapshot(symbol, snapshot_ts_ms)
    if price <= 0:
        return None
    
    try:
        ms = ms_analyze(symbol)
        
        regime_d = ms.get('regime', {})
        if isinstance(regime_d, str):
            regime = regime_d
        else:
            regime = regime_d.get('regime') or regime_d.get('primary', 'UNKNOWN')
        
        trend = ms.get('trend', {})
        if isinstance(trend, str):
            trend_d = {}
        else:
            trend_d = trend if isinstance(trend, dict) else {}
        
        indicators = ms.get('indicators', {})
        rsi_1h = indicators.get('rsi_1h') or indicators.get('rsi') or 50
        bbw    = indicators.get('bbw_1h') or indicators.get('bbw') or 5
        ema9   = indicators.get('ema9') or 0
        ema21  = indicators.get('ema21') or 0
        
        # 计算方向偏向
        # 梵天体制→方向映射（SSOT：regime_config.py）
        regime_dir = {
            'BULL_TREND':    ('LONG',  1.6, 0.15),
            'BULL_EARLY':    ('LONG',  1.0, 0.35),
            'BEAR_TREND':    ('SHORT', 1.6, 0.10),
            'BEAR_EARLY':    ('SHORT', 1.2, 0.35),
            'BEAR_RECOVERY': ('LONG',  1.2, 0.30),
            'CHOP_MID':      ('NONE',  0.88, 0.50),
        }
        
        dir_info = regime_dir.get(regime, ('NONE', 1.0, 1.0))
        direction = dir_info[0]
        short_mult = dir_info[1]
        long_mult  = dir_info[2]
        
        # RSI确认方向
        if direction == 'LONG' and rsi_1h > 70:
            direction = 'NONE'  # 超买时不追多
        elif direction == 'SHORT' and rsi_1h < 30:
            direction = 'NONE'  # 超卖时不追空
        
        # 简单score估算（基于体制强度）
        score_base = {
            'BULL_TREND': 110, 'BEAR_TREND': 110,
            'BULL_EARLY': 95,  'BEAR_EARLY': 95,
            'BEAR_RECOVERY': 120, 'CHOP_MID': 70,
        }.get(regime, 80)
        
        # BBW加权（方仓压缩=信号更强）
        if bbw < 2.0:
            score_base += 15
        elif bbw < 3.0:
            score_base += 8
        
        # RSI确认加权
        if direction == 'LONG' and rsi_1h < 40:
            score_base += 10
        elif direction == 'SHORT' and rsi_1h > 60:
            score_base += 10
        
        return {
            'ok': True,
            'price': price,
            'regime': regime,
            'direction': direction,
            'score_est': round(score_base, 1),
            'rsi_1h': round(float(rsi_1h), 1) if rsi_1h else 50,
            'bbw': round(float(bbw), 2) if bbw else 5,
            'pass_sqe': score_base >= 120,
            'pass_elite': score_base >= 140,
        }
    except Exception as e:
        return {'ok': False, 'reason': str(e)[:80]}

# ── 主回测循环 ─────────────────────────────────────────────────

def run_regime_backtest(symbol, max_samples=300):
    """
    梵天体制识别回测
    每N小时采样一次，评估：
    梵天体制预判 + 24H后方向 → 真实WR
    """
    rows_1h = HIST_1H.get(symbol, [])
    if len(rows_1h) < 250:
        print(f"  ❌ {symbol}: 数据不足")
        return []
    
    CONTEXT = 200
    FUTURE_H = 24
    STEP_H = 12   # 每12H采样一次（加快速度）
    
    start_idx = CONTEXT
    end_idx = len(rows_1h) - FUTURE_H - 2
    
    indices = list(range(start_idx, end_idx, STEP_H))
    
    import random
    if len(indices) > max_samples:
        random.seed(42)
        step_s = len(indices) // max_samples
        indices = indices[::step_s][:max_samples]
    
    print(f"\n{symbol}: 回测 {len(indices)} 个快照（每快照~0.5s）预计 {len(indices)*0.5:.0f}s")
    
    results = []
    t_start = time.time()
    
    for n, idx in enumerate(indices):
        snap_ts = rows_1h[idx]['ts']
        snap_price = rows_1h[idx]['c']
        
        if n % 30 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / (n+1) * (len(indices)-n-1)
            dt = datetime.fromtimestamp(snap_ts/1000, tz=timezone.utc).strftime('%Y-%m')
            print(f"  [{n+1}/{len(indices)}] {dt} price={snap_price:.0f} eta={eta:.0f}s", end='\r')
        
        snap = analyze_snapshot_fast(symbol, snap_ts)
        if not snap or not snap['ok']:
            continue
        if snap['direction'] == 'NONE':
            continue
        
        # 24H后价格
        future_idx = idx + FUTURE_H
        if future_idx >= len(rows_1h):
            continue
        future_price = rows_1h[future_idx]['c']
        
        pnl = (future_price - snap_price) / snap_price * 100
        correct = (pnl > 0) if snap['direction'] == 'LONG' else (pnl < 0)
        
        results.append({
            'symbol': symbol,
            'ts': snap_ts,
            'date': datetime.fromtimestamp(snap_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d'),
            'price': snap_price,
            'future_price': future_price,
            'pnl': round(pnl, 3),
            'regime': snap['regime'],
            'direction': snap['direction'],
            'score_est': snap['score_est'],
            'rsi_1h': snap['rsi_1h'],
            'bbw': snap['bbw'],
            'pass_sqe': snap['pass_sqe'],
            'pass_elite': snap['pass_elite'],
            'correct': correct,
        })
    
    elapsed = time.time() - t_start
    print(f"\n  ✅ {symbol}: {len(results)} 笔  耗时 {elapsed:.0f}s")
    return results

# ── 统计WR矩阵 ────────────────────────────────────────────────

def calc_matrix(results):
    matrix = defaultdict(lambda: {'wins': 0, 'total': 0, 'pnls': []})
    
    for r in results:
        keys = [
            f"ALL:{r['direction']}",
            f"{r['regime']}:{r['direction']}",
        ]
        if r['pass_sqe']:
            keys.append(f"SQE:{r['regime']}:{r['direction']}")
        if r['pass_elite']:
            keys.append(f"ELITE:{r['regime']}:{r['direction']}")
        
        # BBW方仓压缩层
        if r['bbw'] < 2.0:
            keys.append(f"BBW_LOW:{r['regime']}:{r['direction']}")
        
        for k in keys:
            matrix[k]['total'] += 1
            matrix[k]['pnls'].append(r['pnl'])
            if r['correct']:
                matrix[k]['wins'] += 1
    
    out = {}
    for k, v in matrix.items():
        n = v['total']
        if n == 0:
            continue
        pnls = v['pnls']
        avg_win  = sum(p for p in pnls if p > 0) / max(sum(1 for p in pnls if p > 0), 1)
        avg_loss = sum(p for p in pnls if p < 0) / max(sum(1 for p in pnls if p < 0), 1)
        out[k] = {
            'wr':       round(v['wins'] / n, 4),
            'n':        n,
            'avg_pnl':  round(sum(pnls) / n, 3),
            'avg_win':  round(avg_win, 3),
            'avg_loss': round(avg_loss, 3),
            'iron_proof': n >= 20,
        }
    return out

# ── 方仓联合验证（快速版）──────────────────────────────────────

def run_fangcang_fast(symbol='BTC', max_cases=80):
    """
    快速方仓验证：
    注入方仓案例时间点的历史K线
    用market_state得到体制 + 评分
    与方仓案例的真实direction/outcome对比
    """
    path = DATA / f'fangcang_cases_{symbol.lower()}.json'
    if not path.exists():
        return []
    with open(path) as f:
        cases = json.load(f)
    
    sym_usdt = f'{symbol.upper()}USDT'
    
    try:
        from dateutil import parser as _dp
    except ImportError:
        import subprocess
        subprocess.run(['pip', 'install', 'python-dateutil', '-q'])
        from dateutil import parser as _dp
    
    results = []
    print(f"\n方仓联合验证: {symbol} ({min(max_cases, len(cases))} 个案例)")
    
    for n, case in enumerate(cases[:max_cases]):
        ts_str = case.get('ts_burst') or case.get('ts_squeeze_start', '')
        if not ts_str:
            continue
        try:
            dt = _dp.parse(str(ts_str))
            ts_ms = int(dt.timestamp() * 1000)
        except Exception:
            continue
        
        if n % 20 == 0:
            print(f"  [{n+1}/{min(max_cases,len(cases))}] {str(ts_str)[:10]}...", end='\r')
        
        snap = analyze_snapshot_fast(sym_usdt, ts_ms)
        if not snap or not snap['ok']:
            continue
        
        true_dir    = case.get('direction', 'UP')
        true_return = case.get('future_return_24h', 0)
        is_genuine  = case.get('is_genuine_breakout', False)
        
        brahma_bullish = snap['direction'] == 'LONG'
        case_bullish   = true_dir == 'UP'
        match          = brahma_bullish == case_bullish
        
        results.append({
            'symbol': symbol,
            'date': str(ts_str)[:16],
            'fangcang_dir': true_dir,
            'fangcang_return': round(float(true_return), 3),
            'is_genuine': bool(is_genuine),
            'brahma_regime': snap['regime'],
            'brahma_direction': snap['direction'],
            'brahma_score_est': snap['score_est'],
            'pass_sqe': snap['pass_sqe'],
            'direction_match': match,
            'double_confirm': match and bool(is_genuine),
            'bbw': snap['bbw'],
        })
    
    print(f"\n  ✅ {symbol}: {len(results)} 个方仓案例分析完成")
    return results

# ── 主程序 ───────────────────────────────────────────────────────

def main():
    print("\n" + "="*65)
    print("梵天真实系统 × 达摩院方仓 联合回测 v2（快速版）")
    print("="*65)
    
    all_results = []
    
    # Part 1: 体制识别回测
    print("\n【Part 1】梵天体制识别 + 方向预判回测")
    print("-"*50)
    for sym in ['BTCUSDT', 'ETHUSDT']:
        if sym not in HIST_1H:
            print(f"跳过 {sym}")
            continue
        r = run_regime_backtest(sym, max_samples=300)
        all_results.extend(r)
        # 写单标的日志
        with open(DATA / f'brahma_regime_bt_{sym.lower()}.jsonl', 'w') as f:
            for row in r:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
    
    # Part 2: 方仓联合验证
    print("\n【Part 2】方仓案例库联合验证")
    print("-"*50)
    fc_all = []
    for sym in ['BTC', 'ETH']:
        fc = run_fangcang_fast(sym, max_cases=80)
        fc_all.extend(fc)
    
    with open(DATA / 'fangcang_brahma_regime_v1.json', 'w') as f:
        json.dump(fc_all, f, indent=2, ensure_ascii=False)
    
    # Part 3: 统计
    print("\n" + "="*65)
    print("【Part 3】WR统计结果")
    print("="*65)
    
    if all_results:
        matrix = calc_matrix(all_results)
        
        # 保存
        with open(DATA / 'brahma_regime_wr_v1.json', 'w') as f:
            json.dump({
                'matrix': matrix,
                'total': len(all_results),
                'method': '梵天体制识别(market_state)+方向映射，历史K线OFFLINE注入',
                'generated': datetime.now(timezone.utc).isoformat(),
            }, f, indent=2, ensure_ascii=False)
        
        print(f"\n总样本: {len(all_results)} 笔\n")
        
        # 展示关键结果
        print(f"{'策略键':<38} {'WR':>8} {'n':>6} {'avg_pnl':>10}")
        print("-"*68)
        
        sorted_m = sorted(
            [(k, v) for k, v in matrix.items() if v['n'] >= 15],
            key=lambda x: x[1]['wr'], reverse=True
        )
        for k, v in sorted_m[:25]:
            iron = " ✅" if v['iron_proof'] else "   "
            print(f"{k:<38} {v['wr']:>7.1%} {v['n']:>6} {v['avg_pnl']:>9.3f}%{iron}")
        
        # 关键对比
        print(f"\n{'='*65}")
        print("核心对比：")
        print(f"  达摩院简化版(RSI+MACD盲扫)  WR=39.4%  EV=-0.002%")
        
        all_k = matrix.get('ALL:LONG') or {}
        all_ks = matrix.get('ALL:SHORT') or {}
        if all_k or all_ks:
            total_n = (all_k.get('n', 0)) + (all_ks.get('n', 0))
            total_w = int(all_k.get('wr', 0) * all_k.get('n', 0)) + int(all_ks.get('wr', 0) * all_ks.get('n', 0))
            all_wr = total_w / total_n if total_n > 0 else 0
            print(f"  梵天体制识别层(market_state)  WR={all_wr:.1%}  n={total_n}")
        
        bear_s = matrix.get('BEAR_TREND:SHORT')
        if bear_s:
            print(f"  BEAR_TREND SHORT             WR={bear_s['wr']:.1%}  n={bear_s['n']}  avg_pnl={bear_s['avg_pnl']:.2f}%")
        bull_l = matrix.get('BULL_TREND:LONG')
        if bull_l:
            print(f"  BULL_TREND LONG              WR={bull_l['wr']:.1%}  n={bull_l['n']}  avg_pnl={bull_l['avg_pnl']:.2f}%")
        
        sqe_rows = [(k,v) for k,v in matrix.items() if k.startswith('SQE:')]
        if sqe_rows:
            sqe_total = sum(v['n'] for _,v in sqe_rows)
            sqe_wins  = sum(int(v['wr']*v['n']) for _,v in sqe_rows)
            print(f"  梵天SQE过滤(≥120评分)        WR={sqe_wins/sqe_total:.1%}  n={sqe_total}")
    
    # 方仓联合结果
    if fc_all:
        print(f"\n方仓联合验证（{len(fc_all)} 个案例）:")
        tiers = [
            ('精英(score≥140)', [r for r in fc_all if r['brahma_score_est'] >= 140]),
            ('强信号(score≥120)', [r for r in fc_all if r['brahma_score_est'] >= 120]),
            ('全量', fc_all),
        ]
        print(f"\n  {'层级':<22} {'方向匹配率':>12} {'n':>6}")
        print(f"  {'-'*42}")
        for name, subset in tiers:
            if not subset: continue
            match = sum(1 for r in subset if r['direction_match']) / len(subset)
            print(f"  {name:<22} {match:>11.1%} {len(subset):>6}")
        
        # BBW方仓压缩效应
        bbw_low = [r for r in fc_all if r['bbw'] < 2.0]
        if bbw_low:
            match_l = sum(1 for r in bbw_low if r['direction_match']) / len(bbw_low)
            print(f"\n  BBW<2.0 方仓压缩案例: 方向匹配率={match_l:.1%}  n={len(bbw_low)}")
            print(f"  （方向匹配率>50% = 梵天在方仓释放时具有预判能力）")
    
    print(f"\n输出文件:")
    print(f"  {DATA}/brahma_regime_wr_v1.json")
    print(f"  {DATA}/fangcang_brahma_regime_v1.json")
    print("="*65)

if __name__ == '__main__':
    main()
