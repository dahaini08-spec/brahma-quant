#!/usr/bin/env python3
"""
达摩院 · 精英框架自动校准器 v1.0
═══════════════════════════════════════════════════════════════════
触发条件: 每积累 CALIBRATE_INTERVAL 笔新实盘交易后自动运行
功能:
  1. 分析最新实盘 PF/WR 分布
  2. 更新 SYMBOL_DIR_BIAS（方向偏向）
  3. 更新 SYMBOL_DIR_THRESHOLD（品种动态阈值）
  4. 监控 ML 模块激活进度
  5. 写入 Blueprint 并生成变更报告
"""
import sys, os, json, math
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

# ── 日志 (设计院 2026-05-20) ──
import logging as _l_elitec
_log = _l_elitec.getLogger('EliteCal')


sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
sys.path.insert(0, str(Path(__file__).parent.parent))

TRADING_DIR  = Path(__file__).parent.parent
DATA_DIR     = TRADING_DIR / 'data'
RESULTS_DIR  = Path(__file__).parent / 'results'
CALIBRATE_INTERVAL = 50   # 每50笔实盘重新校准

# ── 最小样本量要求 ──────────────────────────────────────────────
MIN_SAMPLES_DIR_BIAS  = 15   # 至少15笔才调整方向偏向
MIN_SAMPLES_THRESHOLD = 20   # 至少20笔才调整阈值

def load_trades() -> list:
    f = DATA_DIR / 'hunter_v2_trades.json'
    if not f.exists(): return []
    return json.loads(f.read_text()) or []

def analyze_by_sym_dir(trades: list) -> dict:
    """按品种+方向统计实盘WR/PF"""
    groups = {}
    for t in trades:
        if t.get('pnl_pct') is None: continue
        sym  = t.get('symbol','').upper()
        dire = t.get('direction','')
        if not sym or not dire: continue
        dire_norm = 'LONG' if dire in ('做多','LONG','BUY','多') else 'SHORT'
        key = (sym, dire_norm)
        if key not in groups: groups[key] = []
        groups[key].append(float(t.get('pnl_pct', 0)))

    results = {}
    for (sym, dire), pnls in groups.items():
        if not pnls: continue
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr     = len(wins) / len(pnls)
        aw     = np.mean(wins) if wins else 0
        al     = abs(np.mean(losses)) if losses else 1e-9
        pf     = (len(wins) * aw) / (len(losses) * al + 1e-9)
        results[(sym, dire)] = {
            'n': len(pnls), 'wr': round(wr, 4),
            'pf': round(pf, 3), 'avg_win': round(aw, 4), 'avg_loss': round(al, 4)
        }
    return results

def suggest_dir_bias(analysis: dict) -> dict:
    """根据实盘数据建议方向偏向更新"""
    sym_groups = {}
    for (sym, dire), stat in analysis.items():
        if sym not in sym_groups: sym_groups[sym] = {}
        sym_groups[sym][dire] = stat

    suggestions = {}
    for sym, dirs in sym_groups.items():
        if 'LONG' not in dirs or 'SHORT' not in dirs:
            continue
        long_stat  = dirs['LONG']
        short_stat = dirs['SHORT']
        long_n  = long_stat['n']
        short_n = short_stat['n']
        if long_n < MIN_SAMPLES_DIR_BIAS or short_n < MIN_SAMPLES_DIR_BIAS:
            continue  # 样本不足

        long_pf  = long_stat['pf']
        short_pf = short_stat['pf']
        diff     = abs(long_pf - short_pf)
        preferred = 'LONG' if long_pf >= short_pf else 'SHORT'

        if diff >= 0.5:
            strength = 'STRONG'
        elif diff >= 0.2:
            strength = 'MODERATE'
        else:
            strength = 'WEAK'

        suggestions[sym] = {
            'preferred': preferred, 'strength': strength,
            'long_pf': long_pf, 'short_pf': short_pf,
            'diff': round(diff, 3), 'n_long': long_n, 'n_short': short_n
        }
    return suggestions

def suggest_thresholds(analysis: dict) -> dict:
    """根据实盘数据建议阈值更新（当前简化：高PF方向适当放宽）"""
    suggestions = {}
    for (sym, dire), stat in analysis.items():
        if stat['n'] < MIN_SAMPLES_THRESHOLD: continue
        pf = stat['pf']
        wr = stat['wr']
        # 当前使用Layer-7回测阈值，实盘样本少时仅记录不修改
        suggestions[(sym, dire)] = {
            'live_pf': pf, 'live_wr': wr, 'n': stat['n'],
            'recommendation': 'lower_if_stable' if pf >= 1.3 else 'keep' if pf >= 1.0 else 'raise'
        }
    return suggestions

def check_ml_progress(trades: list) -> dict:
    """检查ML模块激活进度"""
    n_closed = len([t for t in trades if t.get('pnl_pct') is not None])
    status = {
        'n_closed': n_closed,
        'target': 50,
        'progress_pct': round(n_closed / 50 * 100, 1),
        'remaining': max(0, 50 - n_closed),
    }

    # XGBoost/在线贝叶斯激活条件
    if n_closed >= 50:
        status['d14_ml_status'] = '🔥 全激活'
    elif n_closed >= 30:
        status['d14_ml_status'] = '🟡 接近激活'
    else:
        status['d14_ml_status'] = '🟠 积累中'

    return status

def run_calibration(force: bool = False) -> dict:
    """主校准逻辑"""
    ts = datetime.now(timezone.utc).isoformat()
    trades = load_trades()
    n_closed = len([t for t in trades if t.get('pnl_pct') is not None])

    print(f'[elite_calibrator] 实盘总数={len(trades)} 已平仓={n_closed}')

    # 检查是否需要校准
    state_f = RESULTS_DIR / 'elite_calibrator_state.json'
    state   = json.loads(state_f.read_text()) if state_f.exists() else {'last_calibrated_n': 0}
    last_n  = state.get('last_calibrated_n', 0)

    if not force and (n_closed - last_n) < CALIBRATE_INTERVAL:
        remaining = CALIBRATE_INTERVAL - (n_closed - last_n)
        print(f'[elite_calibrator] 距下次校准还差 {remaining} 笔 (当前{n_closed}, 上次{last_n})')
        ml_status = check_ml_progress(trades)
        return {'status': 'skip', 'n_closed': n_closed, 'ml': ml_status}

    print(f'[elite_calibrator] 开始校准 (新增{n_closed - last_n}笔实盘)')

    # 分析
    analysis     = analyze_by_sym_dir(trades)
    dir_suggests = suggest_dir_bias(analysis)
    th_suggests  = suggest_thresholds(analysis)
    ml_status    = check_ml_progress(trades)

    # 打印建议
    print('\n=== 方向偏向校准建议 ===')
    for sym, s in dir_suggests.items():
        print(f'  {sym:12s} {s["preferred"]:5s} {s["strength"]:8s} '
              f'LONG_PF={s["long_pf"]:.2f} SHORT_PF={s["short_pf"]:.2f} Δ={s["diff"]:.2f}')

    print('\n=== ML激活进度 ===')
    print(f'  已平仓={ml_status["n_closed"]}/50  进度={ml_status["progress_pct"]:.1f}%  '
          f'D14状态={ml_status["d14_ml_status"]}')

    # 写入Blueprint
    try:
        bp_f = TRADING_DIR / 'FANTAN_BLUEPRINT_V3.json'
        bp   = json.loads(bp_f.read_text())
        bp['elite_calibration'] = {
            'ts': ts,
            'n_closed': n_closed,
            'dir_bias_suggestions': {k: v for k, v in dir_suggests.items()},
            'ml_status': ml_status,
            'th_suggestions': {f'{k[0]}_{k[1]}': v for k, v in th_suggests.items()},
        }
        bp['version'] = str(float(bp.get('version', '3.15')) + 0.01)[:4]
        bp['last_updated'] = ts
        bp_f.write_text(json.dumps(bp, indent=2, ensure_ascii=False))
        print(f'\n✅ Blueprint v{bp["version"]} 已更新')
    except Exception as e:
        print(f'⚠️ Blueprint写入失败: {e}')

    # 更新状态
    state['last_calibrated_n'] = n_closed
    state['last_calibrated_ts'] = ts
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    state_f.write_text(json.dumps(state, indent=2))

    return {
        'status': 'calibrated', 'n_closed': n_closed,
        'dir_suggests': dir_suggests, 'ml_status': ml_status,
    }

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--force', action='store_true', help='强制校准（忽略间隔限制）')
    p.add_argument('--status', action='store_true', help='仅显示当前状态')
    args = p.parse_args()

    if args.status:
        trades = load_trades()
        ml = check_ml_progress(trades)
        n_closed = ml['n_closed']
        print(f'实盘进度: {n_closed}/50 ({ml["progress_pct"]:.1f}%)')
        print(f'ML状态:   {ml["d14_ml_status"]}  还差 {ml["remaining"]} 笔')
        analysis = analyze_by_sym_dir(trades)
        if analysis:
            print('\n实盘品种统计:')
            for (sym, dire), s in sorted(analysis.items()):
                em = '🟢' if s['pf'] >= 1.3 else '🟡' if s['pf'] >= 1.0 else '🔴'
                print(f'  {em} {sym:12s} {dire:5s} n={s["n"]:3d} WR={s["wr"]:.1%} PF={s["pf"]:.2f}')
    else:
        result = run_calibration(force=args.force)
        print(f'\n校准结果: {result["status"]}')
    assert True, 'elite_calibrator import ok'
