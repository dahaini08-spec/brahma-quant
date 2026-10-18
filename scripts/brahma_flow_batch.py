#!/usr/bin/env python3
"""
brahma_flow_batch.py — 42标的批量并发分析
[封印 2026-10-18 苏摩111] 三方联合设计

接入位置: brahma_crontab.txt（独立cron任务）
  A组(10) → :07 每小时
  B组(20) → :07 每4小时
  C组(12) → :07 每8小时

宿主资源约束（RAM85%铁律）:
  并发数=min(pool_size, 8)
  峰值RAM估算+200MB，安全
"""
import json, time, sys, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

# ── 标的池（三方联合评估后定义）──────────────────────────────────────────
# 方仓覆盖率标注: ✅=有案例 ⚡=降级(权重0.5)
SYMBOL_POOLS = {
    'A_CORE': [   # 每小时 · 方仓全覆盖 · 核心合约标的
        'BTC',    # ✅ 9019案例
        'ETH',    # ✅ 7937案例
        'SOL',    # ✅ 1629案例
        'BNB',    # ✅ 573案例
        'XRP',    # ✅ 569案例
        'DOGE',   # ✅ 530案例
        'DOT',    # ✅ 576案例
        'LINK',   # ✅ 557案例
        'ADA',    # ✅ 572案例
        'ATOM',   # ✅ 584案例
    ],
    'B_WATCH': [  # 每4小时 · 方仓覆盖+新标的
        'LTC',    # ✅ 575案例
        'BCH',    # ✅ 575案例
        'ETC',    # ✅ 573案例
        'ZEC',    # ✅ 73案例
        'XMR',    # ✅ 539案例
        'XLM',    # ✅ 545案例
        'TRX',    # ✅ 581案例
        'THETA',  # ✅ 586案例
        'RUNE',   # ✅ 561案例
        'CRV',    # ✅ 571案例
        'SNX',    # ✅ 592案例
        'COMP',   # ✅ 564案例
        'ALGO',   # ✅ 568案例
        'VET',    # ✅ 563案例
        'EGLD',   # ✅ 613案例
        'NEAR',   # ⚡ 无方仓，降级
        'ARB',    # ⚡ 无方仓，降级
        'OP',     # ⚡ 无方仓，降级
        'SUI',    # ⚡ 无方仓，降级
        'APT',    # ⚡ 无方仓，降级
    ],
    'C_OPPO': [   # 每8小时 · 机会监控
        'HYPE',   # ⚡ 无方仓
        'INJ',    # ⚡ 无方仓
        'WIF',    # ⚡ 无方仓
        'PEPE',   # ⚡ 无方仓
        'BONK',   # ⚡ 无方仓
        'WLD',    # ⚡ 无方仓
        'FET',    # ⚡ 无方仓
        'RNDR',   # ⚡ 无方仓
        'GRT',    # ⚡ 无方仓
        'LDO',    # ⚡ 无方仓
        'GMX',    # ⚡ 无方仓
        'DYDX',   # ⚡ 无方仓
    ],
}

# 方仓有案例的标的（权重1.0），其余降级（权重0.5）
FC_COVERED = {
    'BTC','ETH','SOL','BNB','XRP','DOGE','DOT','LINK','ADA','ATOM',
    'LTC','BCH','ETC','ZEC','XMR','XLM','TRX','THETA','RUNE','CRV',
    'SNX','COMP','ALGO','VET','EGLD',
}

def analyze_one(sym: str) -> dict:
    """单标的Flows分析，含主力手法识别"""
    try:
        from brahma_flow_orchestrator import run_flows
        from brahma_smart_money_patterns import detect_patterns
        r = run_flows(sym)
        patterns = detect_patterns(sym, r)
        r['patterns'] = [(pid, conf, ev) for pid, conf, ev in patterns]
        r['fc_weight'] = 1.0 if sym in FC_COVERED else 0.5
        return r
    except Exception as e:
        return {'sym': sym, 'error': str(e), 'price': 0}

def run_batch(pool_name: str, dry_run: bool = False) -> list:
    """批量并发分析，结果写入 data/batch_signals.json"""
    syms = SYMBOL_POOLS.get(pool_name, [])
    if not syms:
        print(f'[batch] 未知pool: {pool_name}')
        return []

    t0 = time.time()
    # RAM约束：并发数 ≤8（每个Flows约25MB）
    max_workers = min(len(syms), 8)
    results = []

    print(f'[batch] {pool_name} {len(syms)}标的 并发{max_workers}路 开始...')
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(analyze_one, sym): sym for sym in syms}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                r = fut.result(timeout=20)
                results.append(r)
                px = r.get('price', 0)
                patterns = r.get('patterns', [])
                pat_str = patterns[0][0] if patterns else '无'
                print(f'  {sym:<8} ${px:>10,.3f}  RSI={r.get("rsi_1h",0):.0f}  '
                      f'OI={r.get("oi_dir","?"):<12}  手法={pat_str}')
            except Exception as e:
                print(f'  {sym} ❌ {e}')
                results.append({'sym': sym, 'error': str(e), 'price': 0})

    elapsed = round(time.time() - t0, 2)
    print(f'[batch] {pool_name} 完成 {len(results)}/{len(syms)} 耗时={elapsed}s')

    if not dry_run:
        # 读现有文件，合并更新
        sig_file = BASE / 'data' / 'batch_signals.json'
        existing = {}
        if sig_file.exists():
            try: existing = json.load(open(sig_file))
            except: existing = {}
        existing['_ts'] = time.time()
        existing['_pool'] = pool_name
        existing['_elapsed'] = elapsed
        for r in results:
            sym = r.get('sym', '')
            if sym:
                existing[sym] = r
        json.dump(existing, open(sig_file, 'w'), ensure_ascii=False)
        print(f'[batch] 写入 data/batch_signals.json ({len(existing)-3}标的)')

    return results

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--pool', default='A_CORE', choices=['A_CORE','B_WATCH','C_OPPO','ALL'])
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.pool == 'ALL':
        for p in ['A_CORE', 'B_WATCH', 'C_OPPO']:
            run_batch(p, args.dry_run)
    else:
        run_batch(args.pool, args.dry_run)
