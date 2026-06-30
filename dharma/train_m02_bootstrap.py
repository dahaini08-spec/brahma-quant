#!/usr/bin/env python3
"""
达摩院 M02 — Bootstrap×10万次置信区间验证
============================================
目标：为6枚试金石品种计算 PF 的 CI95/CI99
  - CI95 下限 ≥ 1.5 → GOLD：仓位解锁至 7%
  - CI95 下限 ≥ 1.3 → SILVER：标准仓位 5%
  - CI95 下限 < 1.3 → BRONZE/降级：保守 3%

完成后自动通过 DharmaBus.push_m02() 写入系统
"""
import json, random, math, time, sys, os
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

N_BOOTSTRAP = 100_000
RANDOM_SEED = 42

# M01 试金石数据（直接内嵌，不依赖文件IO）
TOUCHSTONE = {
    'BTCUSDT':  {'wr': 0.513, 'pf': 2.027, 'n': 304},
    'ETHUSDT':  {'wr': 0.530, 'pf': 2.083, 'n': 181},
    'DOGEUSDT': {'wr': 0.623, 'pf': 3.234, 'n': 53},
    'SOLUSDT':  {'wr': 0.507, 'pf': 2.064, 'n': 69},
    'LINKUSDT': {'wr': 0.696, 'pf': 4.033, 'n': 46},
    'WLDUSDT':  {'wr': 0.562, 'pf': 2.503, 'n': 64},
    # 候选品种（接近试金石）
    'XAUUSDT':  {'wr': 0.538, 'pf': 2.917, 'n': 39},
    'DOTUSDT':  {'wr': 0.493, 'pf': 2.510, 'n': 69},
    'TIAUSDT':  {'wr': 0.547, 'pf': 1.975, 'n': 64},
    'ADAUSDT':  {'wr': 0.486, 'pf': 1.968, 'n': 70},
    'LTCUSDT':  {'wr': 0.495, 'pf': 1.707, 'n': 220},
    'ATOMUSDT': {'wr': 0.446, 'pf': 1.995, 'n': 101},
    'NEARUSDT': {'wr': 0.579, 'pf': 2.209, 'n': 19},
    # 验证禁用品种（应该CI下限<1.0）
    'TONUSDT':  {'wr': 0.386, 'pf': 1.109, 'n': 223},
    'XRPUSDT':  {'wr': 0.439, 'pf': 1.206, 'n': 590},
}


def _build_trades(wr: float, pf: float, n: int, seed_offset: int = 0):
    """
    根据 wr/pf 构建模拟交易序列
    win_size / loss_size 满足 PF = sum(wins) / sum(losses)
    """
    rng = random.Random(RANDOM_SEED + seed_offset)
    b = pf * (1 - wr) / wr  # 平均盈亏比
    trades = []
    for _ in range(n):
        if rng.random() < wr:
            trades.append(+b)   # 盈利
        else:
            trades.append(-1.0) # 亏损（归一化为1）
    return trades


def bootstrap_pf(trades: list, n_boot: int) -> dict:
    """Bootstrap×n_boot 计算 PF 的分布"""
    n = len(trades)
    rng = random.Random(RANDOM_SEED)
    boot_pfs = []

    for _ in range(n_boot):
        sample = [trades[rng.randint(0, n-1)] for _ in range(n)]
        wins   = sum(x for x in sample if x > 0)
        losses = sum(-x for x in sample if x < 0)
        pf = wins / losses if losses > 0 else 0.0
        boot_pfs.append(pf)

    boot_pfs.sort()
    nb = len(boot_pfs)

    def percentile(p):
        idx = int(nb * p / 100)
        return round(boot_pfs[min(idx, nb-1)], 4)

    return {
        'n':           n,
        'obs_pf':      round(sum(x for x in trades if x > 0) /
                             max(sum(-x for x in trades if x < 0), 1e-9), 4),
        'median_pf':   percentile(50),
        'ci95':        [percentile(2.5),  percentile(97.5)],
        'ci99':        [percentile(0.5),  percentile(99.5)],
        'ci50':        [percentile(25),   percentile(75)],
        'std':         round(
            math.sqrt(sum((x - sum(boot_pfs)/nb)**2 for x in boot_pfs) / nb), 4
        ),
        'p_above_1':   round(sum(1 for x in boot_pfs if x > 1.0) / nb, 4),
        'p_above_15':  round(sum(1 for x in boot_pfs if x > 1.5) / nb, 4),
        'p_above_2':   round(sum(1 for x in boot_pfs if x > 2.0) / nb, 4),
    }


def run_m02():
    print('╔══════════════════════════════════════════════════╗')
    print('║   达摩院 M02 · Bootstrap×100,000次置信区间验证   ║')
    print('╠══════════════════════════════════════════════════╣')
    print(f'  品种数: {len(TOUCHSTONE)}  |  迭代: {N_BOOTSTRAP:,}次/品种')
    print()

    t0 = time.time()
    boot_results = {}

    for sym, data in TOUCHSTONE.items():
        ts = time.time()
        trades = _build_trades(data['wr'], data['pf'], data['n'], seed_offset=hash(sym) % 1000)
        result = bootstrap_pf(trades, N_BOOTSTRAP)

        ci95_low = result['ci95'][0]
        if ci95_low >= 1.5:
            tier = 'GOLD  🥇'
            pos = '7%'
        elif ci95_low >= 1.3:
            tier = 'SILVER🥈'
            pos = '5%'
        elif ci95_low >= 1.0:
            tier = 'BRONZE🥉'
            pos = '3%'
        else:
            tier = 'DISABL❌'
            pos = '0%（禁用验证）'

        boot_results[sym] = {
            **result,
            'tier': tier.strip().split()[0],
            'max_pos': {'GOLD': 0.07, 'SILVER': 0.05, 'BRONZE': 0.03, 'DISABL': 0.0}
                       .get(tier.strip().split()[0], 0.03),
            'ci95_low': ci95_low,
            'ci99_low': result['ci99'][0],
        }

        elapsed = time.time() - ts
        print(f'  {sym:15s} [{tier}] obs_PF={result["obs_pf"]:.3f} '
              f'CI95=[{ci95_low:.3f},{result["ci95"][1]:.3f}] '
              f'P>1.5={result["p_above_15"]:.1%} 仓位={pos}  ({elapsed:.1f}s)')

    total_elapsed = time.time() - t0
    print()
    print(f'  总耗时: {total_elapsed:.1f}s')
    print()

    # 保存结果
    ts_str = time.strftime('%Y%m%d_%H%M%S')
    out_file = BASE / 'dharma' / 'results' / f'bootstrap_m02_{ts_str}.json'
    out_data = {
        '_meta': {
            'n_bootstrap': N_BOOTSTRAP,
            'ts': ts_str,
            'version': 'M02',
            'elapsed_s': round(total_elapsed, 1),
        },
        'results': boot_results,
    }
    out_file.write_text(json.dumps(out_data, ensure_ascii=False, indent=2))
    print(f'  结果保存: {out_file.name}')

    # 通过总线写入系统
    try:
        from dharma.dharma_bus import push_m02 as _push
        _push(boot_results)
        print('  [DharmaBus] M02结果已自动写入系统 ✅')
    except Exception as e:
        print(f'  [DharmaBus] 写入失败（手动导入）: {e}')

    # 摘要
    print()
    print('╠══════════════════════════════════════════════════╣')
    print('║                   M02 摘要                       ║')
    print('╠══════════════════════════════════════════════════╣')
    gold   = [s for s,v in boot_results.items() if v['tier']=='GOLD']
    silver = [s for s,v in boot_results.items() if v['tier']=='SILVER']
    bronze = [s for s,v in boot_results.items() if v['tier']=='BRONZE']
    disabl = [s for s,v in boot_results.items() if v['tier']=='DISABL']
    print(f'  🥇 GOLD  (仓位7%): {gold}')
    print(f'  🥈 SILVER(仓位5%): {silver}')
    print(f'  🥉 BRONZE(仓位3%): {bronze}')
    print(f'  ❌ 禁用验证:       {disabl}')
    print('╚══════════════════════════════════════════════════╝')

    return boot_results


if __name__ == '__main__':
    run_m02()
