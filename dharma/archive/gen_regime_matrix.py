#!/usr/bin/env python3
"""
dharma/gen_regime_matrix.py — 个币WR矩阵生成器 v1.0
设计院 × 达摩院 2026-06-18

职责：
  1. 读取 sim_replay_*.jsonl 结算结果
  2. 按「体制×方向」分组计算WR/PF/EV（n≥30才入矩阵）
  3. 输出更新后的 dharma_runtime.json（个币专属，不再借用BTC近似）

用法：
  python3 dharma/gen_regime_matrix.py                   # 读最新sim_replay
  python3 dharma/gen_regime_matrix.py --file <path>     # 指定结果文件
  python3 dharma/gen_regime_matrix.py --dry             # 只打印不写入
"""

import sys, json, argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
RESULTS = BASE / 'dharma' / 'results'
RUNTIME_PATH = BASE / 'data' / 'dharma_runtime.json'

# 铁证样本量阈值（与设计院宪法对齐）
# 【设计院宪法 2026-06-18】小样本不分析、不参考、个例没有使用价值
N_IRON  = 1000   # 铁证级：可作核心系统升级依据
N_VALID = 500    # 次铁证：参考依据，轻度参数调整
N_MIN   = 100    # 最低入矩阵门槛：n<100 一律排除，禁止引用

# 封禁标准三条全中（与MEMORY.md宪法对齐）
# WR<48% AND avg_pnl<-0.15% AND n≥500（提升到500，防止小样本假封禁）

# 封禁标准（三条全中）：WR<48% AND avg_pnl<-0.15% AND n≥500
BLOCK_WR    = 0.48
BLOCK_PNL   = -0.15
BLOCK_N     = 500    # 封禁需 n≥500，防止小样本假封禁

CHOP_REGIMES = {'CHOP_MID', 'CHOP_HIGH', 'CHOP_LOW'}


def load_latest_results(override_path=None) -> list:
    """读取最新 sim_replay 结果"""
    if override_path:
        p = Path(override_path)
        if not p.exists():
            print(f'❌ 文件不存在: {p}')
            return []
    else:
        files = sorted(RESULTS.glob('sim_replay_*.jsonl'), reverse=True)
        if not files:
            # fallback: 读 live_signal_log.jsonl 实盘数据
            fallback = BASE / 'data' / 'live_signal_log.jsonl'
            if fallback.exists():
                print(f'⚠️  无sim_replay结果，使用实盘数据: {fallback}')
                p = fallback
            else:
                print('❌ 无回放结果，请先运行 sim_brahma_replay.py')
                return []
        else:
            p = files[0]
            print(f'📂 读取: {p.name}')

    records = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    print(f'   加载 {len(records)} 条记录')
    return records


def build_matrix(records: list) -> dict:
    """
    按标的×体制×方向计算WR矩阵。
    只统计已结算（result不为None且不为TIMEOUT）的记录。
    """
    # 按 symbol → regime × direction 分组
    groups = defaultdict(lambda: defaultdict(list))
    for r in records:
        result = r.get('result', '')
        if not result or result in ('TIMEOUT', None, 'OPEN', 'EXPIRED', 'SUPERSEDED'):
            continue
        sym    = r.get('symbol', 'UNKNOWN')
        regime = r.get('regime', 'UNKNOWN')
        direct = r.get('direction', r.get('signal_dir', 'UNKNOWN'))
        groups[sym][f'{regime}×{direct}'].append(r)

    matrix = {}

    for sym, combos in groups.items():
        sym_matrix = {}

        # 汇总到 regime 维度（含两方向）
        regime_data = defaultdict(lambda: {'SHORT': [], 'LONG': []})
        for key, sigs in combos.items():
            parts = key.split('×')
            if len(parts) == 2:
                regime, direct = parts
                regime_data[regime][direct].extend(sigs)

        for regime, dirs in regime_data.items():
            short_sigs = dirs['SHORT']
            long_sigs  = dirs['LONG']

            def calc_stats(sigs):
                if not sigs: return None
                wins  = [s for s in sigs if 'WIN' in str(s.get('result',''))]
                losses = [s for s in sigs if s.get('result') == 'LOSS']
                n_denom = len(wins) + len(losses)
                if n_denom == 0: return None
                wr  = len(wins) / n_denom
                pnls = [s.get('pnl_pct', 0) for s in sigs]
                avg_pnl = sum(pnls) / len(pnls)
                win_pnls  = [s.get('pnl_pct',0) for s in wins]
                loss_pnls = [abs(s.get('pnl_pct',0)) for s in losses]
                pf = sum(win_pnls) / sum(loss_pnls) if sum(loss_pnls) > 0 else 0
                return {
                    'n': len(sigs),
                    'n_settled': n_denom,
                    'wr': round(wr, 4),
                    'avg_pnl': round(avg_pnl, 4),
                    'pf': round(pf, 3),
                }

            ss = calc_stats(short_sigs)
            ls = calc_stats(long_sigs)

            if (ss is None or ss['n'] < N_MIN) and (ls is None or ls['n'] < N_MIN):
                continue

            # 确定最优方向
            def wr_val(s): return s['wr'] if s else 0
            best_dir = 'SHORT' if wr_val(ss) >= wr_val(ls) else 'LONG'

            # 封禁判断（苏摩三原则：三条全中才封禁）
            def should_block(s):
                if s is None: return False
                return (s['wr'] < BLOCK_WR and
                        s['avg_pnl'] < BLOCK_PNL and
                        s['n_settled'] >= BLOCK_N)

            # Action决策
            def calc_action(ss, ls, regime):
                if regime in CHOP_REGIMES:
                    return 'BLOCK_ALL'
                if should_block(ss) and should_block(ls):
                    return 'BLOCK_ALL'
                if should_block(ss):
                    return 'LONG_ONLY'
                if should_block(ls):
                    return 'SHORT_ONLY'
                # 基于WR差值确定倾向
                s_wr = wr_val(ss)
                l_wr = wr_val(ls)
                if s_wr >= 0.65 and s_wr > l_wr + 0.05:
                    return 'SHORT_ONLY' if ss and ss['n_settled'] >= N_IRON else 'SHORT_PREFER'
                if l_wr >= 0.65 and l_wr > s_wr + 0.05:
                    return 'LONG_ONLY' if ls and ls['n_settled'] >= N_IRON else 'LONG_PREFER'
                if s_wr >= 0.60 and s_wr > l_wr + 0.03:
                    return 'SHORT_PREFER'
                if l_wr >= 0.60 and l_wr > s_wr + 0.03:
                    return 'LONG_PREFER'
                return 'BOTH'

            action = calc_action(ss, ls, regime)

            # 置信度
            max_n = max((ss['n_settled'] if ss else 0), (ls['n_settled'] if ls else 0))
            conf = 'HIGH' if max_n >= N_IRON else ('MED' if max_n >= N_VALID else 'LOW')

            entry = {
                'best_dir':    best_dir,
                'short_wr':    ss['wr'] if ss else 0,
                'long_wr':     ls['wr'] if ls else 0,
                'short_pf':    ss['pf'] if ss else 0,
                'long_pf':     ls['pf'] if ls else 0,
                'short_avg_pnl': ss['avg_pnl'] if ss else 0,
                'long_avg_pnl':  ls['avg_pnl'] if ls else 0,
                'n_short':     ss['n_settled'] if ss else 0,
                'n_long':      ls['n_settled'] if ls else 0,
                'action':      action,
                'confidence':  conf,
                'source':      'sim_brahma_replay_v1.0',
                'updated_at':  datetime.now(timezone.utc).isoformat(),
            }

            if regime in CHOP_REGIMES:
                entry['note'] = '震荡体制，双向封禁'
            elif should_block(ss) and not should_block(ls):
                entry['note'] = f'SHORT封禁铁证(WR={ss["wr"]:.1%} n={ss["n_settled"]})'
            elif should_block(ls) and not should_block(ss):
                entry['note'] = f'LONG封禁铁证(WR={ls["wr"]:.1%} n={ls["n_settled"]})'
            elif max_n < N_VALID:
                entry['note'] = f'n={max_n}<{N_VALID}，仅参考，待积累铁证'

            sym_matrix[regime] = entry

        if sym_matrix:
            matrix[sym] = sym_matrix

    return matrix


def update_runtime(matrix: dict, dry: bool = False):
    """将新矩阵合并写入 dharma_runtime.json"""
    try:
        runtime = json.loads(RUNTIME_PATH.read_text())
    except Exception:
        runtime = {}

    old_matrix = runtime.get('regime_matrix', {})
    changed_syms = []

    for sym, sym_data in matrix.items():
        if sym not in old_matrix:
            changed_syms.append(f'{sym}(新增)')
        else:
            old_regimes = set(old_matrix[sym].keys())
            new_regimes = set(sym_data.keys())
            if old_regimes != new_regimes or any(
                sym_data[r].get('action') != old_matrix[sym].get(r,{}).get('action')
                for r in new_regimes
            ):
                changed_syms.append(f'{sym}(更新)')
        old_matrix[sym] = sym_data

    runtime['regime_matrix'] = old_matrix
    runtime['regime_matrix_updated_at'] = datetime.now(timezone.utc).isoformat()
    runtime['regime_matrix_source'] = 'sim_brahma_replay_v1.0（个币专属，非BTC近似）'

    if dry:
        print('\n[DRY RUN] 以下将写入 dharma_runtime.json:')
        print(json.dumps({'regime_matrix': matrix}, indent=2, ensure_ascii=False)[:2000])
        print(f'\n变更标的: {changed_syms}')
        return

    RUNTIME_PATH.write_text(json.dumps(runtime, indent=2, ensure_ascii=False))
    print(f'✅ dharma_runtime.json 已更新')
    print(f'   变更标的: {changed_syms or ["无变化"]}')


def print_matrix_report(matrix: dict):
    """打印矩阵报告"""
    print(f'\n{"═"*60}')
    print('  达摩院 WR矩阵报告（个币专属）')
    print(f'{"═"*60}')
    for sym, sym_data in sorted(matrix.items()):
        print(f'\n  📊 {sym}:')
        for regime, entry in sorted(sym_data.items()):
            action = entry['action']
            conf   = entry['confidence']
            s_wr   = entry['short_wr']
            l_wr   = entry['long_wr']
            ns     = entry['n_short']
            nl     = entry['n_long']
            note   = entry.get('note', '')
            flag   = '✅' if action in ('SHORT_ONLY','LONG_ONLY','SHORT_PREFER','LONG_PREFER') else ('❌' if action == 'BLOCK_ALL' else '⚠️')
            print(f'    {flag} {regime:20s} [{conf:4s}] {action:15s} '
                  f'SHORT={s_wr:.1%}(n={ns}) LONG={l_wr:.1%}(n={nl})')
            if note:
                print(f'         → {note}')
    print(f'\n{"═"*60}\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', default=None, help='指定结果文件路径')
    parser.add_argument('--dry',  action='store_true', help='只打印不写入')
    args = parser.parse_args()

    print('🏛️  达摩院 gen_regime_matrix.py v1.0')
    print('=' * 50)

    records = load_latest_results(args.file)
    if not records:
        sys.exit(1)

    settled = [r for r in records if r.get('result') not in (None, 'OPEN', 'TIMEOUT', 'EXPIRED', 'SUPERSEDED', '')]
    print(f'已结算: {len(settled)} / {len(records)}')

    if len(settled) < N_MIN:
        print(f'⚠️  结算样本不足{N_MIN}条，矩阵置信度低，建议先运行完整回放')

    matrix = build_matrix(records)
    print_matrix_report(matrix)
    update_runtime(matrix, dry=args.dry)


if __name__ == '__main__':
    main()
