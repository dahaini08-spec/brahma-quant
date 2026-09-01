#!/usr/bin/env python3
"""
brahma_real_backtest.py — 梵天真实系统 × 达摩院方仓数据库 联合回测
2026-08-27 设计院 苏摩111封印

核心思想：
  不用「简化版RSI+MACD」，而是用「真实brahma_core.analyze() 35维评分」
  通过 OFFLINE_MODE 注入历史K线，冻结实时API，让梵天用历史数据做分析
  对比：简化版WR=39% vs 真实35维评分WR=?

架构：
  1. 加载历史K线（data/historical/BTCUSDT_1h.jsonl.gz等）
  2. 按时间窗口滑动（每4H一个快照，需要200根1H K线作为上下文）
  3. 激活OFFLINE_MODE，注入历史K线到data_cache
  4. 调用真实 brahma_core.analyze() — 完整35维评分
  5. 收集score / regime / SQE_pass / direction
  6. 验证：24H后price涨跌方向
  7. 统计：按score分层 / 按体制分层 的真实WR矩阵

输出：
  data/brahma_real_wr_v1.json  — 真实WR矩阵
  data/brahma_real_backtest_log.jsonl — 每笔信号详情

方仓联合验证：
  对 fangcang_cases_btc.json 中每个方仓案例（ts_burst时间点）
  注入历史数据 → 调用真实analyze() → 获取35维score
  输出：有score标注的方仓案例 → 证明「方仓+35维」的组合Alpha
"""

import sys, os, json, gzip, time, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ROOT = Path(__file__).parent.parent
DATA = ROOT / 'data'
HIST = DATA / 'historical'
OUT  = DATA

sys.path.insert(0, str(ROOT / 'brahma_brain'))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)

# ── 配置 ─────────────────────────────────────────────────────────

SYMBOLS = ['BTCUSDT', 'ETHUSDT']  # 首轮只跑BTC/ETH（有完整历史数据）

# 回测窗口（越久越有统计意义，但跑得慢）
BACKTEST_STEP_H = 8          # 每8H做一次梵天分析（BTC 1H K线步长）
CONTEXT_BARS_1H = 200        # 每次分析需要的1H上下文bars数
CONTEXT_BARS_4H = 100        # 4H上下文
CONTEXT_BARS_15M = 200       # 15M上下文
OUTCOME_HOURS   = 24         # 24H后验证方向

# score分层（用于统计WR）
SCORE_TIERS = [
    (140, 999, 'S3_ELITE'),   # ≥140 精英信号
    (120, 140, 'S2_STRONG'),  # 120-140 强信号
    (100, 120, 'S1_WATCH'),   # 100-120 观察
    (0,   100, 'S0_WEAK'),    # <100 弱信号
]

# ── 历史数据加载 ─────────────────────────────────────────────────

def load_klines_gz(symbol: str, tf: str) -> list:
    """加载历史K线 jsonl.gz，返回 [{ts, o, h, l, c, v}...] 按时间升序"""
    path = HIST / f'{symbol}_{tf}.jsonl.gz'
    if not path.exists():
        # 尝试小写
        path = HIST / f'{symbol.lower()}' / f'{tf}.jsonl.gz'
    if not path.exists():
        return []
    rows = []
    with gzip.open(path, 'rt') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    rows.sort(key=lambda x: x['ts'])
    return rows

def klines_to_binance_format(rows: list) -> list:
    """
    转换成 Binance API klines 格式：
    [open_time, open, high, low, close, volume, close_time, ...]
    brahma_core 里的 klines_to_ohlcv 期望这种格式
    """
    result = []
    for r in rows:
        ts = r['ts']
        result.append([
            ts,            # 0: open_time (ms)
            str(r['o']),   # 1: open
            str(r['h']),   # 2: high
            str(r['l']),   # 3: low
            str(r['c']),   # 4: close
            str(r['v']),   # 5: volume
            ts + 3599999,  # 6: close_time
            str(r.get('qv', r['v'] * r['c'])),  # 7: quote volume
            r.get('n', 0), # 8: trades
            str(r.get('tb', r['v'] * 0.5)),  # 9: taker buy base vol
            '0', '0'
        ])
    return result

# ── 历史数据字典（全量预加载）─────────────────────────────────────

print("加载历史K线数据...")
HIST_DATA = {}
for sym in SYMBOLS:
    for tf in ['15m', '1h', '4h']:
        rows = load_klines_gz(sym, tf)
        if rows:
            HIST_DATA[f'{sym}:{tf}'] = rows
            print(f"  ✅ {sym} {tf}: {len(rows)} 根K线  [{datetime.fromtimestamp(rows[0]['ts']/1000, tz=timezone.utc).strftime('%Y-%m')} ~ {datetime.fromtimestamp(rows[-1]['ts']/1000, tz=timezone.utc).strftime('%Y-%m')}]")
        else:
            print(f"  ❌ {sym} {tf}: 无数据")

# ── Mock数据注入核心 ──────────────────────────────────────────────

def inject_historical_snapshot(symbol: str, snapshot_ts_ms: int):
    """
    核心函数：将历史时间点的K线数据注入data_cache
    snapshot_ts_ms: 分析时刻（毫秒时间戳）

    原理：
    - 找到 snapshot_ts_ms 之前的最近200根K线
    - 用 data_cache._cache_set 注入（正确的注入方式）
    - 激活 OFFLINE_MODE 冻结实时API
    """
    import brahma_brain.data_cache as dc
    
    # 激活离线模式
    dc.OFFLINE_MODE = True
    
    # 清空旧K线缓存（防止上一次快照污染）
    # 只清K线key，保留其他
    keys_to_del = [k for k in dc._cache if ':klines:' in k or k.startswith('klines:')]
    for k in keys_to_del:
        del dc._cache[k]
    # 同时清磁盘缓存中的K线文件（防止get时走磁盘路径拿到旧K线）
    import glob
    for f in glob.glob(os.path.join(str(ROOT), 'data', 'brahma_cache', f'{symbol}*klines*.json')):
        try:
            os.remove(f)
        except Exception:
            pass
    
    last_close = 0.0
    
    for tf in ['15m', '1h', '4h']:
        sym_key = f'{symbol}:{tf}'
        if sym_key not in HIST_DATA:
            continue
        rows = HIST_DATA[sym_key]
        
        # 二分查找 snapshot_ts 之前的最后一根K线
        lo, hi = 0, len(rows) - 1
        idx = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if rows[mid]['ts'] <= snapshot_ts_ms:
                idx = mid
                lo = mid + 1
            else:
                hi = mid - 1
        
        if idx < 50:
            continue  # 历史数据不足
        
        # 取最近N根
        limit = 200 if tf in ['1h', '15m'] else 100
        slice_rows = rows[max(0, idx - limit + 1): idx + 1]
        binance_fmt = klines_to_binance_format(slice_rows)
        
        # 用正确的 _cache_set 注入（TTL设24H）
        # data_cache.get_klines 的key格式：_cache_key(symbol, interval, limit)
        # = f'{symbol}:{interval}:{limit}'
        cache_key = f'{symbol}:{tf}:{limit}'
        dc._cache_set(cache_key, binance_fmt, ttl=86400)
        
        # 也注入不带limit的key（兼容老代码）
        dc._cache_set(f'{symbol}:{tf}', binance_fmt, ttl=86400)
        
        if tf == '1h':
            last_close = float(slice_rows[-1]['c'])
    
    # 注入价格（只存内存，不存磁盘）
    if last_close > 0:
        dc._cache[f'ticker:{symbol}'] = {
            'data': {'lastPrice': str(last_close), 'price': str(last_close)},
            'exp': time.time() + 86400
        }
    
    # 设置OFFLINE_CTX（链上数据用中性值）
    dc.OFFLINE_CTX.update({
        'fr': 0.0001,
        'oi': 100000,
        'oi_change': 0.0,
        'lsr': 50.0,
        'top_lsr': 50.0,
        'liquidation': 0.0,
        'kronos_p_up': 0.5,
    })
    
    return last_close

def restore_online_mode():
    """回测完成后恢复实时模式"""
    try:
        import brahma_brain.data_cache as dc
        dc.OFFLINE_MODE = False
    except Exception:
        pass

# ── 真实梵天分析（单快照）────────────────────────────────────────

def run_brahma_snapshot(symbol: str, snapshot_ts_ms: int) -> dict:
    """
    在历史时间点调用真实 brahma_core.analyze()
    返回: {score, regime, direction, pass_sqe, price, ts}
    """
    sym_usdt = symbol if symbol.endswith('USDT') else f'{symbol}USDT'
    
    try:
        current_price = inject_historical_snapshot(sym_usdt, snapshot_ts_ms)
        if current_price <= 0:
            return {'ok': False, 'reason': 'no_price'}
        
        # 调用真实35维分析
        from brahma_brain.brahma_core import analyze as _analyze
        
        # 分别跑做多和做空
        result_long  = _analyze(sym_usdt, signal_dir='LONG',  deep=False)
        result_short = _analyze(sym_usdt, signal_dir='SHORT', deep=False)
        
        def _extract(r):
            cf = r.get('confluence', {})
            score = float(cf.get('total') or r.get('score_final', 0) or 0)
            regime = r.get('regime', {})
            if isinstance(regime, dict):
                regime_str = regime.get('regime') or regime.get('primary', 'UNKNOWN')
            else:
                regime_str = str(regime)
            return score, regime_str
        
        score_l, regime_l = _extract(result_long)
        score_s, regime_s = _extract(result_short)
        
        # 确定主方向（取更高score的方向）
        if score_l >= score_s:
            main_dir, main_score, main_regime = 'LONG', score_l, regime_l
        else:
            main_dir, main_score, main_regime = 'SHORT', score_s, regime_s
        
        return {
            'ok': True,
            'ts': snapshot_ts_ms,
            'price': current_price,
            'regime': main_regime,
            'direction': main_dir,
            'score': round(main_score, 1),
            'score_long': round(score_l, 1),
            'score_short': round(score_s, 1),
            'pass_sqe': main_score >= 120,
            'pass_elite': main_score >= 140,
        }
    
    except Exception as e:
        return {'ok': False, 'reason': str(e)[:100]}
    finally:
        restore_online_mode()

# ── 验证结果：24H后的方向 ─────────────────────────────────────────

def get_future_price(symbol: str, snapshot_ts_ms: int, hours: int = 24) -> float:
    """从历史数据获取N小时后的价格"""
    target_ts = snapshot_ts_ms + hours * 3600 * 1000
    key = f'{symbol}:1h'
    sym_key = f'{symbol}USDT:1h' if not symbol.endswith('USDT') else f'{symbol}:1h'
    
    rows = HIST_DATA.get(sym_key) or HIST_DATA.get(key)
    if not rows:
        return 0.0
    
    # 找最近的K线
    best = None
    for r in rows:
        if r['ts'] >= target_ts:
            best = r
            break
    
    return best['c'] if best else 0.0

# ── 主回测循环 ──────────────────────────────────────────────────

def run_backtest(symbol: str, max_samples: int = 200) -> list:
    """
    对单个标的进行梵天真实系统回测
    max_samples: 最大样本数（全量太慢，先跑200个验证可行性）
    """
    sym_usdt = symbol if symbol.endswith('USDT') else f'{symbol}USDT'
    rows_1h = HIST_DATA.get(f'{sym_usdt}:1h') or []
    
    if len(rows_1h) < CONTEXT_BARS_1H + OUTCOME_HOURS:
        print(f"  ❌ {symbol}: 历史数据不足")
        return []
    
    results = []
    
    # 时间窗口：从有足够历史数据的时间点开始
    start_idx = CONTEXT_BARS_1H
    end_idx = len(rows_1h) - OUTCOME_HOURS - 1
    
    # 步长：每BACKTEST_STEP_H小时采样一次
    step = BACKTEST_STEP_H
    indices = list(range(start_idx, end_idx, step))
    
    # 限制样本数
    if len(indices) > max_samples:
        import random
        random.seed(42)
        # 均匀采样，覆盖全部时间段
        step_sample = len(indices) // max_samples
        indices = indices[::step_sample][:max_samples]
    
    print(f"\n  {symbol}: 回测 {len(indices)} 个时间点...")
    
    failed = 0
    for n, idx in enumerate(indices):
        snapshot_ts = rows_1h[idx]['ts']
        snapshot_price = rows_1h[idx]['c']
        
        if n % 20 == 0:
            dt_str = datetime.fromtimestamp(snapshot_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d')
            print(f"    [{n+1}/{len(indices)}] {dt_str} price={snapshot_price:.0f}", end='\r')
        
        # 调用真实梵天分析
        snap = run_brahma_snapshot(sym_usdt, snapshot_ts)
        
        if not snap['ok']:
            failed += 1
            continue
        
        # 验证：24H后价格方向
        future_price = get_future_price(sym_usdt, snapshot_ts, OUTCOME_HOURS)
        if future_price <= 0:
            continue
        
        pnl_pct = (future_price - snapshot_price) / snapshot_price * 100
        
        # 方向正确性
        if snap['direction'] == 'LONG':
            correct = pnl_pct > 0
        else:
            correct = pnl_pct < 0
        
        results.append({
            'symbol': symbol,
            'ts': snapshot_ts,
            'date': datetime.fromtimestamp(snapshot_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
            'price': snapshot_price,
            'future_price': future_price,
            'pnl_pct': round(pnl_pct, 3),
            'regime': snap['regime'],
            'direction': snap['direction'],
            'score': snap['score'],
            'score_long': snap['score_long'],
            'score_short': snap['score_short'],
            'pass_sqe': snap['pass_sqe'],
            'pass_elite': snap['pass_elite'],
            'correct': correct,
        })
    
    print(f"\n  ✅ {symbol}: {len(results)}笔完成, {failed}笔失败")
    return results

# ── 统计WR矩阵 ───────────────────────────────────────────────────

def calc_wr_matrix(results: list) -> dict:
    """按体制×score层×方向统计真实WR"""
    matrix = defaultdict(lambda: {'wins': 0, 'total': 0, 'sum_pnl': 0.0})
    
    for r in results:
        regime = r['regime']
        direction = r['direction']
        score = r['score']
        
        # 按体制×方向
        k1 = f"{regime}:{direction}"
        matrix[k1]['total'] += 1
        matrix[k1]['sum_pnl'] += r['pnl_pct']
        if r['correct']:
            matrix[k1]['wins'] += 1
        
        # 按score层
        tier = 'S0'
        for lo, hi, name in SCORE_TIERS:
            if lo <= score < hi:
                tier = name
                break
        
        k2 = f"{tier}:{regime}:{direction}"
        matrix[k2]['total'] += 1
        matrix[k2]['sum_pnl'] += r['pnl_pct']
        if r['correct']:
            matrix[k2]['wins'] += 1
        
        # SQE过后的
        if r['pass_sqe']:
            k3 = f"SQE_PASS:{regime}:{direction}"
            matrix[k3]['total'] += 1
            matrix[k3]['sum_pnl'] += r['pnl_pct']
            if r['correct']:
                matrix[k3]['wins'] += 1
        
        # 精英信号
        if r['pass_elite']:
            k4 = f"ELITE:{regime}:{direction}"
            matrix[k4]['total'] += 1
            matrix[k4]['sum_pnl'] += r['pnl_pct']
            if r['correct']:
                matrix[k4]['wins'] += 1
    
    # 计算WR
    output = {}
    for k, v in matrix.items():
        n = v['total']
        wins = v['wins']
        wr = wins / n if n > 0 else 0
        avg_pnl = v['sum_pnl'] / n if n > 0 else 0
        output[k] = {
            'wr': round(wr, 4),
            'n': n,
            'wins': wins,
            'avg_pnl': round(avg_pnl, 4),
            'iron_proof': n >= 20,
        }
    
    return output

# ── 方仓案例联合验证 ─────────────────────────────────────────────

def run_fangcang_validation(symbol: str = 'BTC', max_cases: int = 50) -> list:
    """
    对方仓案例库中的案例，在ts_burst时间点注入历史数据
    调用真实brahma_core.analyze()，获取35维score
    证明「方仓识别+35维评分」的组合Alpha

    这是最核心的验证：
    - 方仓案例已经过人工精标（is_genuine_breakout字段）
    - 梵天在方仓释放时能打出多高的score？
    - score≥120的方仓案例WR如何？
    """
    sym_upper = symbol.upper()
    sym_usdt  = f'{sym_upper}USDT'
    
    cases_path = DATA / f'fangcang_cases_{symbol.lower()}.json'
    if not cases_path.exists():
        print(f"  ❌ 方仓案例不存在: {cases_path}")
        return []
    
    with open(cases_path) as f:
        cases = json.load(f)
    
    print(f"\n方仓联合验证: {symbol} ({len(cases)} 个案例, 跑前 {max_cases} 个)")
    
    # 转换时间戳
    from dateutil import parser as _dp
    
    results = []
    for n, case in enumerate(cases[:max_cases]):
        ts_str = case.get('ts_burst') or case.get('ts_squeeze_start', '')
        if not ts_str:
            continue
        
        try:
            dt = _dp.parse(ts_str)
            ts_ms = int(dt.timestamp() * 1000)
        except Exception:
            continue
        
        if n % 10 == 0:
            print(f"  [{n+1}/{min(max_cases, len(cases))}] {ts_str[:10]}...", end='\r')
        
        # 调用真实梵天
        snap = run_brahma_snapshot(sym_usdt, ts_ms)
        if not snap['ok']:
            continue
        
        # 结合方仓案例的真实outcome
        true_dir   = case.get('direction', 'UP')
        true_return = case.get('future_return_24h', 0)
        is_genuine = case.get('is_genuine_breakout', False)
        
        # 梵天direction与方仓方向是否一致
        brahma_bullish = snap['direction'] == 'LONG'
        case_bullish   = true_dir == 'UP'
        direction_match = brahma_bullish == case_bullish
        
        results.append({
            'symbol': symbol,
            'ts': ts_ms,
            'date': ts_str[:16],
            'fangcang_dir': true_dir,
            'fangcang_return': round(true_return, 3),
            'is_genuine': is_genuine,
            'brahma_score': snap['score'],
            'brahma_direction': snap['direction'],
            'brahma_regime': snap['regime'],
            'pass_sqe': snap['pass_sqe'],
            'direction_match': direction_match,
            # 如果梵天方向正确 + 方仓是真实突破 = 双重确认
            'double_confirm': direction_match and is_genuine,
        })
    
    print(f"\n  ✅ 方仓联合验证完成: {len(results)} 个案例")
    return results

# ── 主程序 ──────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("梵天真实系统 × 达摩院方仓 联合回测")
    print("="*60)
    
    all_results   = []
    all_fangcang  = []
    
    # === Part 1: 真实35维系统回测 ===
    print("\n【Part 1】梵天真实35维评分回测")
    print("-" * 40)
    
    for sym in SYMBOLS:
        sym_usdt = sym if sym.endswith('USDT') else f'{sym}USDT'
        if f'{sym_usdt}:1h' not in HIST_DATA:
            print(f"  跳过 {sym}: 无1H历史数据")
            continue
        
        results = run_backtest(sym_usdt, max_samples=150)
        all_results.extend(results)
        
        # 写单标的日志
        log_path = DATA / f'brahma_real_backtest_{sym.lower()}.jsonl'
        with open(log_path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
    
    # === Part 2: 方仓联合验证 ===
    print("\n【Part 2】方仓数据库联合验证")
    print("-" * 40)
    
    for sym in ['BTC', 'ETH']:
        fc_results = run_fangcang_validation(sym, max_cases=40)
        all_fangcang.extend(fc_results)
    
    # === Part 3: 统计分析 ===
    print("\n【Part 3】统计分析")
    print("="*60)
    
    if all_results:
        matrix = calc_wr_matrix(all_results)
        
        # 保存矩阵
        matrix_path = DATA / 'brahma_real_wr_v1.json'
        with open(matrix_path, 'w') as f:
            json.dump({
                'matrix': matrix,
                'total_samples': len(all_results),
                'symbols': SYMBOLS,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'method': '梵天真实35维系统，OFFLINE_MODE历史K线注入',
            }, f, indent=2, ensure_ascii=False)
        
        print(f"\n📊 真实35维WR矩阵（总样本: {len(all_results)} 笔）\n")
        print(f"{'策略键':<35} {'WR':>8} {'n':>6} {'avg_pnl':>10} {'铁证'}")
        print("-"*70)
        
        # 按WR排序展示关键条目
        sorted_items = sorted(
            [(k,v) for k,v in matrix.items() if v['n'] >= 10],
            key=lambda x: x[1]['wr'],
            reverse=True
        )
        
        for k, v in sorted_items[:20]:
            iron = "✅" if v['iron_proof'] else "  "
            print(f"{k:<35} {v['wr']:>7.1%} {v['n']:>6} {v['avg_pnl']:>9.3f}% {iron}")
        
        print(f"\n对比基线（达摩院简化版）:")
        print(f"  简化版RSI+MACD WR = 39.4%  EV = -0.002%/笔")
        
        # 找到SQE_PASS的整体WR
        sqe_keys = [(k,v) for k,v in matrix.items() if k.startswith('SQE_PASS:')]
        if sqe_keys:
            total_sqe = sum(v['n'] for _, v in sqe_keys)
            total_wins = sum(v['wins'] for _, v in sqe_keys)
            sqe_wr = total_wins / total_sqe if total_sqe > 0 else 0
            print(f"  真实35维 + SQE过滤(≥120) WR = {sqe_wr:.1%}  n={total_sqe}")
        
        elite_keys = [(k,v) for k,v in matrix.items() if k.startswith('ELITE:')]
        if elite_keys:
            total_elite = sum(v['n'] for _, v in elite_keys)
            total_elite_wins = sum(v['wins'] for _, v in elite_keys)
            elite_wr = total_elite_wins / total_elite if total_elite > 0 else 0
            print(f"  真实35维 + 精英过滤(≥140) WR = {elite_wr:.1%}  n={total_elite}")
    
    # 方仓验证结果
    if all_fangcang:
        fc_path = DATA / 'fangcang_brahma_validated.json'
        with open(fc_path, 'w') as f:
            json.dump(all_fangcang, f, indent=2, ensure_ascii=False)
        
        print(f"\n📦 方仓联合验证（{len(all_fangcang)} 个案例）\n")
        
        # 按score层统计方向匹配率
        tiers = [
            ('精英(≥140)', [r for r in all_fangcang if r['brahma_score'] >= 140]),
            ('强信号(≥120)', [r for r in all_fangcang if r['brahma_score'] >= 120]),
            ('观察(≥100)', [r for r in all_fangcang if r['brahma_score'] >= 100]),
            ('全量', all_fangcang),
        ]
        
        print(f"{'层级':<20} {'方向匹配率':>12} {'双重确认率':>12} {'n':>6}")
        print("-"*55)
        for name, subset in tiers:
            if not subset:
                continue
            match_rate = sum(1 for r in subset if r['direction_match']) / len(subset)
            double_rate = sum(1 for r in subset if r['double_confirm']) / len(subset)
            print(f"{name:<20} {match_rate:>11.1%} {double_rate:>11.1%} {len(subset):>6}")
        
        print(f"\n  方向匹配率 > 50% = 梵天在方仓释放时具有预判能力")
        print(f"  双重确认率 = 梵天方向正确 AND 方仓是真实突破")
    
    print("\n" + "="*60)
    print("回测完成")
    if all_results:
        print(f"  真实WR矩阵: {DATA}/brahma_real_wr_v1.json")
    if all_fangcang:
        print(f"  方仓验证: {DATA}/fangcang_brahma_validated.json")
    print("="*60)

if __name__ == '__main__':
    main()
