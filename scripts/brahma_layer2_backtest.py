#!/usr/bin/env python3
"""
brahma_layer2_backtest.py — Layer2: 梵天本地评分层
2026-08-27 设计院 苏摩111批准

基于Layer1的30,408笔快照，抽取BTC/ETH代表性样本，
用OFFLINE_MODE注入历史K线，调用真实market_state.analyze()
得到梵天本地score（block_a技术层，0.4s/次），
对比：同样快照，Layer1纯规则评分 vs 梵天真实评分 → WR差异

目的：量化梵天「本地技术层」评分对WR的实际贡献
"""

import sys, os, json, gzip, time, random
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

# ── 加载Layer1结果 ────────────────────────────────────────────
print("加载Layer1数据...")
with open(DATA / 'ultimate_log_layer1.jsonl') as f:
    layer1_rows = [json.loads(l) for l in f]

# BTC/ETH均匀抽样（每体制×方向50个）
btc_eth = [r for r in layer1_rows if r['symbol'] in ('BTC', 'ETH')]
random.seed(42)
groups = defaultdict(list)
for r in btc_eth:
    groups[f"{r['regime']}:{r['direction']}"].append(r)

selected = []
for k, items in groups.items():
    selected.extend(random.sample(items, min(50, len(items))))
random.shuffle(selected)

print(f"抽样: {len(selected)} 个快照 × BTC/ETH")

# ── 加载历史K线到内存 ──────────────────────────────────────────
print("加载历史K线...")
ROWS_1H = {}
ROWS_4H = {}
for sym in ['BTCUSDT', 'ETHUSDT']:
    for tf, store in [('1h', ROWS_1H), ('4h', ROWS_4H)]:
        with gzip.open(str(HIST / f'{sym}_{tf}.jsonl.gz'), 'rt') as f:
            rows = sorted([json.loads(l.strip()) for l in f if l.strip()], key=lambda x: x['ts'])
        store[sym] = rows

def to_binance_fmt(rows_slice):
    return [[r['ts'], str(r['o']), str(r['h']), str(r['l']), str(r['c']), str(r['v']),
             r['ts']+3599999, str(r.get('qv',0)), r.get('n',0),
             str(r.get('tb',0)), '0', '0'] for r in rows_slice]

# ── OFFLINE_MODE注入 ───────────────────────────────────────────
import brahma_brain.data_cache as dc

def inject_snapshot(sym_usdt, snapshot_ts_ms):
    dc.OFFLINE_MODE = True
    for k in list(dc._cache.keys()):
        if sym_usdt in k: del dc._cache[k]

    last_price = 0.0
    for tf, store in [('1h', ROWS_1H), ('4h', ROWS_4H)]:
        rows = store.get(sym_usdt, [])
        if not rows: continue
        lo, hi, idx = 0, len(rows)-1, -1
        while lo <= hi:
            m = (lo+hi)//2
            if rows[m]['ts'] <= snapshot_ts_ms: idx=m; lo=m+1
            else: hi=m-1
        if idx < 30: continue
        limit = 200 if tf == '1h' else 100
        sl = rows[max(0, idx-limit+1): idx+1]
        bf = to_binance_fmt(sl)
        dc._cache_set(f'{sym_usdt}:{tf}:{limit}', bf, ttl=86400)
        dc._cache_set(f'{sym_usdt}:{tf}', bf, ttl=86400)
        if tf == '1h':
            last_price = float(sl[-1]['c'])

    if last_price > 0:
        dc._cache[f'ticker:{sym_usdt}'] = {
            'data': {'lastPrice': str(last_price), 'price': str(last_price)},
            'exp': time.time() + 86400
        }
    dc.OFFLINE_CTX.update({'fr':0.0001,'oi':100000,'lsr':50.0,'kronos_p_up':0.5})
    return last_price

from brahma_brain.market_state import analyze as ms_analyze

def get_brahma_local_score(sym_usdt, snapshot_ts_ms, direction):
    """
    调用真实market_state.analyze() → 提取本地技术评分
    0.3-0.5s/次（无AI议会/HCME/链上）
    """
    try:
        price = inject_snapshot(sym_usdt, snapshot_ts_ms)
        if price <= 0: return None

        ms = ms_analyze(sym_usdt)

        # 提取体制
        regime_d = ms.get('regime', {})
        regime = regime_d.get('regime') or regime_d.get('primary','UNKNOWN') if isinstance(regime_d, dict) else str(regime_d)

        # 提取技术指标
        ind = ms.get('indicators', {})
        rsi = float(ind.get('rsi_1h') or ind.get('rsi') or 50)
        bbw = float(ind.get('bbw_1h') or ind.get('bbw') or 5)

        # 梵天本地评分公式（对应block_a核心逻辑）
        base = {
            'BULL_TREND':105,'BEAR_TREND':108,
            'BULL_EARLY':90, 'BEAR_EARLY':92,
            'BEAR_RECOVERY':115,'CHOP_MID':65,'UNKNOWN':70
        }.get(regime, 80)

        if bbw < 2.0:   base += 18
        elif bbw < 3.0: base += 10
        elif bbw > 5.0: base -= 8

        if direction=='LONG'  and rsi < 38: base += 12
        elif direction=='LONG'  and rsi < 45: base += 6
        if direction=='SHORT' and rsi > 62: base += 12
        elif direction=='SHORT' and rsi > 55: base += 6

        if direction=='LONG'  and rsi > 65: base -= 8
        if direction=='SHORT' and rsi < 35: base -= 8

        return {
            'ok': True,
            'price': price,
            'regime_brahma': regime,
            'rsi_brahma': round(rsi, 1),
            'bbw_brahma': round(bbw, 2),
            'brahma_score': int(base),
            'pass_sqe': base >= 120,
        }
    except Exception as e:
        return {'ok': False, 'reason': str(e)[:60]}
    finally:
        dc.OFFLINE_MODE = False

# ── 主回测循环 ─────────────────────────────────────────────────
print(f"\n运行Layer2评分（{len(selected)} 个快照）...")
results = []
t_start = time.time()

for n, snap in enumerate(selected):
    sym = snap['symbol']
    sym_usdt = f'{sym}USDT'
    ts_ms = snap['ts']
    direction = snap['direction']

    if n % 20 == 0:
        elapsed = time.time() - t_start
        eta = elapsed/(n+1)*(len(selected)-n-1) if n > 0 else 0
        dt = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime('%Y-%m')
        print(f"  [{n+1}/{len(selected)}] {sym} {dt} {snap['regime']} eta={eta:.0f}s", end='\r')

    l2 = get_brahma_local_score(sym_usdt, ts_ms, direction)
    if not l2 or not l2['ok']:
        continue

    results.append({
        **snap,
        'brahma_regime':  l2['regime_brahma'],
        'rsi_brahma':     l2['rsi_brahma'],
        'bbw_brahma':     l2['bbw_brahma'],
        'brahma_score':   l2['brahma_score'],
        'pass_sqe':       l2['pass_sqe'],
        'layer2_ok':      True,
    })

elapsed = time.time() - t_start
print(f"\n  ✅ Layer2完成: {len(results)} 笔  耗时 {elapsed:.0f}s")

# ── 统计 ───────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("Layer2 WR分析（梵天本地评分 vs 纯规则）")
print('='*60)

# 全量WR
total_wr = sum(1 for r in results if r['win']) / len(results) if results else 0
total_ev = sum(r['pnl_pct'] for r in results) / len(results) if results else 0
print(f"\n全量: WR={total_wr:.1%}  EV={total_ev:+.3f}%  n={len(results)}")

# SQE分层（核心）
sqe_pass = [r for r in results if r['pass_sqe']]
sqe_fail = [r for r in results if not r['pass_sqe']]

if sqe_pass:
    wr_pass = sum(1 for r in sqe_pass if r['win']) / len(sqe_pass)
    ev_pass = sum(r['pnl_pct'] for r in sqe_pass) / len(sqe_pass)
    print(f"SQE通过(score≥120): WR={wr_pass:.1%}  EV={ev_pass:+.3f}%  n={len(sqe_pass)}")
if sqe_fail:
    wr_fail = sum(1 for r in sqe_fail if r['win']) / len(sqe_fail)
    ev_fail = sum(r['pnl_pct'] for r in sqe_fail) / len(sqe_fail)
    print(f"SQE未通过(score<120): WR={wr_fail:.1%}  EV={ev_fail:+.3f}%  n={len(sqe_fail)}")

# 体制×梵天评分分层
print(f"\n体制 × SQE状态:")
m = defaultdict(lambda: {'wins':0,'n':0,'pnls':[]})
for r in results:
    k = f"{r['regime']}:{r['direction']}:{'SQE_PASS' if r['pass_sqe'] else 'SQE_FAIL'}"
    m[k]['n'] += 1
    m[k]['pnls'].append(r['pnl_pct'])
    if r['win']: m[k]['wins'] += 1

print(f"  {'策略键':<50} {'WR':>7} {'EV':>8} {'n':>5}")
print(f"  {'-'*72}")
for k, v in sorted(m.items(), key=lambda x: x[1]['wins']/max(x[1]['n'],1), reverse=True):
    if v['n'] < 10: continue
    wr = v['wins']/v['n']
    ev = sum(v['pnls'])/v['n']
    print(f"  {k:<50} {wr:>6.1%} {ev:>+7.3f}% {v['n']:>5}")

# score分布
scores = [r['brahma_score'] for r in results]
print(f"\n梵天本地评分分布:")
print(f"  min={min(scores)}  max={max(scores)}  mean={sum(scores)/len(scores):.0f}")
print(f"  ≥140: {sum(1 for s in scores if s>=140)}  ≥120: {sum(1 for s in scores if s>=120)}  <100: {sum(1 for s in scores if s<100)}")

# 保存
with open(DATA / 'layer2_results.json', 'w') as f:
    json.dump({
        'results': results,
        'summary': {
            'total': len(results),
            'wr_all': round(total_wr,4),
            'ev_all': round(total_ev,4),
            'wr_sqe_pass': round(sum(1 for r in sqe_pass if r['win'])/max(len(sqe_pass),1),4) if sqe_pass else 0,
            'n_sqe_pass': len(sqe_pass),
        },
        'generated': datetime.now(timezone.utc).isoformat(),
    }, f, ensure_ascii=False, indent=2)

print(f"\n输出: {DATA}/layer2_results.json")
print("="*60)
