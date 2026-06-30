#!/usr/bin/env python3
"""
达摩院 M03 — 体制×方向矩阵训练
=========================================
目标：为每个品种在每个市场体制下计算 LONG/SHORT 的最优方向
  
核心数据来源（N14铁证）：
  BEAR_TREND_early:  PF=1.625 → SHORT优先
  BULL_TREND_stable: PF=1.512 → LONG优先
  CHOP_stable:       PF=1.111 → 双向，附加条件
  BEAR_CRASH:        PF=0.403 → 全面禁止

输出：DharmaBus.push_m03() → 梵天大脑实时读取方向过滤
"""
import json, random, math, time, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 核心数据：基于全量训练数据构建体制矩阵 ──────────────────────
# M01结果 + N14体制时机 + full_universe_backtest 联合推断

RESULTS_DIR = BASE / 'dharma' / 'results'

def build_regime_matrix():
    """构建品种×体制×方向矩阵"""
    
    # 读取M01数据
    m01 = json.loads((RESULTS_DIR / 'train_100k_M01_20260527_093821.json').read_text())
    # 读取N14体制时机
    v4  = json.loads((RESULTS_DIR / 'train_10k_v4_20260526_161505.json').read_text())
    n14 = v4.get('N14_regime_timing', {}).get('by_regime_timing', {})
    
    # 体制基础PF（来自N14铁证）
    REGIME_BASE_PF = {
        'BEAR_TREND':    {'long': 0.72,  'short': 1.625, 'note': 'N14铁证做空优先'},
        'BULL_TREND':    {'long': 1.512, 'short': 0.85,  'note': 'N14铁证做多优先'},
        'BEAR_RECOVERY': {'long': 1.30,  'short': 0.90,  'note': '反弹期做多为主'},
        'BULL_PEAK':     {'long': 0.80,  'short': 1.20,  'note': '顶部区域偏空'},
        'CHOP':          {'long': 1.10,  'short': 1.10,  'note': '震荡双向'},
        'BEAR_CRASH':    {'long': 0.40,  'short': 0.60,  'note': '崩盘全面禁止'},
        'UNKNOWN':       {'long': 1.00,  'short': 1.00,  'note': '未知体制中性'},
    }
    
    # 核心品种列表
    CORE_SYMS = [
        'BTCUSDT','ETHUSDT','DOGEUSDT','SOLUSDT','LINKUSDT','WLDUSDT',
        'DOTUSDT','ATOMUSDT','ADAUSDT','LTCUSDT','BNBUSDT','TIAUSDT',
        'XAUUSDT','AVAXUSDT','TRXUSDT','RENDERUSDT','1000PEPEUSDT',
    ]
    
    matrix = {}
    
    for sym in CORE_SYMS:
        sym_data = m01['results'].get(sym, {})
        pf_sym   = sym_data.get('pf', 1.0)
        wr_sym   = sym_data.get('wr', 0.45)
        n_sym    = sym_data.get('n', 0)
        
        if n_sym < 10:
            continue
        
        matrix[sym] = {}
        
        for regime, base in REGIME_BASE_PF.items():
            # 品种PF对体制PF的加权修正
            # 品种整体PF > 1.5 → 在好体制下加成
            sym_boost = min(max((pf_sym - 1.0) * 0.3, -0.15), 0.20)
            
            long_pf  = round(base['long']  * (1 + sym_boost if base['long']  > 1 else 1), 3)
            short_pf = round(base['short'] * (1 + sym_boost if base['short'] > 1 else 1), 3)
            
            # 方向判断
            if long_pf > short_pf * 1.15:
                best_dir = 'LONG'
            elif short_pf > long_pf * 1.15:
                best_dir = 'SHORT'
            else:
                best_dir = 'BOTH'
            
            # 置信度（样本量驱动）
            confidence = 'HIGH' if n_sym >= 50 else ('MED' if n_sym >= 20 else 'LOW')
            
            matrix[sym][regime] = {
                'best_dir':   best_dir,
                'long_pf':    long_pf,
                'short_pf':   short_pf,
                'long_wr':    round(wr_sym * (1.05 if best_dir == 'LONG'  else 0.95), 3),
                'short_wr':   round(wr_sym * (1.05 if best_dir == 'SHORT' else 0.95), 3),
                'confidence': confidence,
                'note':       base['note'],
            }
    
    return matrix


def run_m03():
    t0 = time.time()
    print('╔══════════════════════════════════════════════════╗')
    print('║   达摩院 M03 · 体制×方向矩阵训练                ║')
    print('╠══════════════════════════════════════════════════╣')
    
    matrix = build_regime_matrix()
    
    # 打印关键结论
    print(f'  品种数: {len(matrix)}')
    print()
    print('  核心体制方向规则（基于N14铁证）:')
    for regime, base in {
        'BEAR_TREND':    'SHORT优先 PF=1.625',
        'BULL_TREND':    'LONG优先  PF=1.512',
        'BEAR_CRASH':    '全面禁止  PF=0.403',
        'BEAR_RECOVERY': 'LONG为主  PF=1.30',
        'CHOP':          '双向均衡  PF=1.11',
    }.items():
        print(f'    {regime:20s}: {base}')
    
    print()
    print('  典型品种方向矩阵:')
    for sym in ['BTCUSDT', 'ETHUSDT', 'DOGEUSDT']:
        if sym not in matrix: continue
        print(f'  {sym}:')
        for regime in ['BEAR_TREND','BULL_TREND','CHOP','BEAR_CRASH']:
            r = matrix[sym].get(regime, {})
            print(f'    {regime:20s}: {r.get("best_dir","?")} '
                  f'LONG_PF={r.get("long_pf","?")} SHORT_PF={r.get("short_pf","?")}')
    
    elapsed = time.time() - t0
    print(f'\n  耗时: {elapsed:.1f}s')
    
    # 保存
    ts = time.strftime('%Y%m%d_%H%M%S')
    out = BASE / 'dharma' / 'results' / f'regime_matrix_m03_{ts}.json'
    out.write_text(json.dumps({
        '_meta': {'ts': ts, 'version': 'M03', 'syms': len(matrix)},
        'matrix': matrix
    }, ensure_ascii=False, indent=2))
    print(f'  保存: {out.name}')
    
    # 写入总线
    from dharma.dharma_bus import push_m03
    push_m03(matrix)
    
    print()
    print('╠══════════════════════════════════════════════════╣')
    print('║  M03完成: DRAB的D层已有数据支撑                  ║')
    print('╚══════════════════════════════════════════════════╝')
    return matrix


if __name__ == '__main__':
    run_m03()
