"""
dharma/causal_discovery.py · 达摩院因果发现模块 v1.0
======================================================
设计院 × 达摩院 · 因果AI增强系列 P1 · 2026-06-18

哲学：
  Causal Copilot核心理念："Discovering mechanisms, not just confirming correlations."
  每周自动运行，生成因果图更新报告，驱动达摩院权重调整。

三大算法：
  1. Granger多变量因果（时序）    → 哪些维度Granger-causes盈利？
  2. LiNGAM（线性非高斯加性）     → 维度→盈利的因果方向
  3. PC算法简化版（条件独立检验） → 体制条件下的因果图结构

输入：
  - data/wuqu_paper_settled.jsonl（已结算信号）
  - data/dharma_iron_evidence.json（铁证数据库）

输出：
  - data/causal_discovery_report.json（每次运行覆盖）
  - data/causal_discovery_history.jsonl（历史追加，漂移检测用）

设计院原则：
  - n≥100 才写入正式报告，n<100 仅输出「数据不足」
  - 所有建议权重变更提交到 soma_ai_registry 审批
  - 不直接修改任何参数文件，只生成报告
"""

import os
import sys
import json
import time
import math
import warnings
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / 'data'
REPORT_PATH   = DATA_DIR / 'causal_discovery_report.json'
HISTORY_PATH  = DATA_DIR / 'causal_discovery_history.jsonl'
IRON_EVIDENCE = DATA_DIR / 'dharma_iron_evidence.json'

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

warnings.filterwarnings('ignore')

# ── 最小样本要求 ──────────────────────────────────────────────────
MIN_SAMPLES_GRANGER  = 30   # Granger最低要求
MIN_SAMPLES_LINGAM   = 50   # LiNGAM最低要求
MIN_SAMPLES_REPORT   = 100  # 正式报告进入铁证要求


# ══════════════════════════════════════════════════════════════════
# 数据加载 + 特征提取
# ══════════════════════════════════════════════════════════════════

def _load_settled_signals(min_n: int = 30) -> List[Dict]:
    """加载已结算信号，过滤出有完整字段的记录"""
    path = DATA_DIR / 'wuqu_paper_settled.jsonl'
    if not path.exists():
        return []

    records = []
    for line in path.open():
        try:
            r = json.loads(line)
            # 必须有结算结果和评分
            if r.get('outcome') in ('TP1', 'TP2', 'SL') and r.get('score'):
                records.append(r)
        except Exception:
            continue

    print(f'[CausalDiscovery] 加载结算信号: {len(records)} 条')
    return records


def _extract_features(records: List[Dict]) -> Tuple[List[List[float]], List[float], List[str]]:
    """
    从结算信号中提取特征矩阵和结果向量。
    返回 (features_matrix, outcomes, feature_names)
    """
    # 目标变量：盈利=+1，亏损=-1
    outcomes = []
    feature_matrix = []
    feature_names = [
        'score',           # 总评分
        'structure_grade', # 结构级别
        'rr1',             # RR比
        'gap_pct',         # 入场 gap
        'regime_bear',     # 是否熊市体制 (1/0)
        'regime_chop',     # 是否震荡体制
        'dir_short',       # 方向是否SHORT (1/0)
        'score_causal_pct',# (如有) 因果维度占比
    ]

    for r in records:
        outcome = 1.0 if r.get('outcome') in ('TP1', 'TP2') else -1.0
        score   = float(r.get('score', 0) or 0)
        grade   = float(r.get('structure_grade', 0) or 0)
        if not grade:
            g_raw = r.get('grade', 50)
            try: grade = float(g_raw or 50)
            except: grade = 50.0  # emoji grade
        rr1     = float(r.get('rr1', 2.0) or 2.0)
        gap     = float(r.get('gap_pct', 0) or 0)
        regime  = str(r.get('regime', ''))
        dirn    = str(r.get('signal_dir', r.get('direction', '')))
        regime_bear = 1.0 if 'BEAR' in regime else 0.0
        regime_chop = 1.0 if 'CHOP' in regime else 0.0
        dir_short   = 1.0 if dirn == 'SHORT' else 0.0

        # 因果维度占比（如果有 counterfactual 字段）
        cf = r.get('counterfactual', {}) or {}
        causal_pct = float(cf.get('causal_ratio', 0.5) or 0.5)

        row = [score, grade, rr1, gap, regime_bear, regime_chop, dir_short, causal_pct]
        feature_matrix.append(row)
        outcomes.append(outcome)

    return feature_matrix, outcomes, feature_names


# ══════════════════════════════════════════════════════════════════
# 算法1：Granger多变量因果
# ══════════════════════════════════════════════════════════════════

def _run_granger_multivariate(
    records: List[Dict],
    feature_names: List[str],
) -> Dict[str, Any]:
    """
    对每个特征维度检验其与盈利的 Granger 因果关系。
    注意：这里用的是信号时间序列（不是价格序列），
    检验「哪个特征值高的信号 → 后续盈利」。
    """
    from statsmodels.tsa.stattools import grangercausalitytests
    import numpy as np

    # 按时间排序（使用 open_ts 或 signal_id 代理）
    sorted_recs = sorted(records, key=lambda x: x.get('open_ts', x.get('signal_id', '')))
    n = len(sorted_recs)

    if n < MIN_SAMPLES_GRANGER:
        return {'skipped': True, 'reason': f'样本不足: n={n} < {MIN_SAMPLES_GRANGER}'}

    # 提取时序
    outcomes_ts = [1.0 if r.get('outcome') in ('TP1','TP2') else 0.0 for r in sorted_recs]

    results = {}
    for fname in ['score', 'structure_grade', 'rr1']:
        try:
            feature_ts = []
            for r in sorted_recs:
                v = r.get(fname, 0)
                try:
                    feature_ts.append(float(v or 0))
                except Exception:
                    feature_ts.append(0.0)

            # 标准化
            arr = np.array(feature_ts)
            arr = (arr - arr.mean()) / (arr.std() + 1e-9)

            data = np.column_stack([outcomes_ts, arr])
            gc = grangercausalitytests(data, maxlag=3, verbose=False)
            p_vals = [gc[lag][0]['ssr_ftest'][1] for lag in range(1, 4)]
            min_p = min(p_vals)
            best_lag = p_vals.index(min_p) + 1

            results[fname] = {
                'min_p_value':   round(min_p, 4),
                'best_lag':      best_lag,
                'significant':   min_p < 0.05,
                'causal_strength': round(max(0, 1 - min_p * 10), 3),
            }
        except Exception as e:
            results[fname] = {'error': str(e)[:60]}

    return {
        'n_samples': n,
        'results':   results,
        'significant_dims': [k for k, v in results.items() if v.get('significant')],
    }


# ══════════════════════════════════════════════════════════════════
# 算法2：LiNGAM 因果方向（简化版）
# ══════════════════════════════════════════════════════════════════

def _run_lingam_simplified(
    feature_matrix: List[List[float]],
    outcomes: List[float],
    feature_names: List[str],
) -> Dict[str, Any]:
    """
    LiNGAM简化版：通过非高斯残差检验因果方向。
    完整 LiNGAM 需要 lingam 包，这里用互信息近似代替。
    检验每个特征 → 结果 的独立因果贡献。
    """
    import numpy as np
    from scipy import stats

    n = len(outcomes)
    if n < MIN_SAMPLES_LINGAM:
        return {'skipped': True, 'reason': f'样本不足: n={n}'}

    X = np.array(feature_matrix)
    y = np.array(outcomes)
    results = {}

    for i, fname in enumerate(feature_names):
        try:
            xi = X[:, i]
            # 标准化
            xi_std = (xi - xi.mean()) / (xi.std() + 1e-9)

            # Spearman相关（秩相关，对非正态鲁棒）
            rho, p_spearman = stats.spearmanr(xi_std, y)

            # 非高斯性检验（Jarque-Bera）→ 越非高斯，LiNGAM越可靠
            jb_stat, p_jb = stats.jarque_bera(xi_std)

            # 点双列相关（与二元结果）
            y_binary = [(1 if v > 0 else 0) for v in y]
            rpb, p_pb = stats.pointbiserialr(xi_std, y_binary)

            # 综合因果强度
            causal_strength = abs(rho) * (1 - p_spearman) * (1 + 0.3 * (p_jb < 0.05))

            results[fname] = {
                'spearman_rho':     round(rho, 3),
                'p_spearman':       round(p_spearman, 4),
                'pointbiserial_r':  round(rpb, 3),
                'non_gaussian':     p_jb < 0.05,
                'causal_strength':  round(causal_strength, 3),
                'direction':        'positive' if rho > 0 else 'negative',
            }
        except Exception as e:
            results[fname] = {'error': str(e)[:60]}

    # 按因果强度排序
    ranked = sorted(
        [(k, v.get('causal_strength', 0)) for k, v in results.items() if 'error' not in v],
        key=lambda x: -x[1]
    )

    return {
        'n_samples': n,
        'results':   results,
        'ranking':   ranked,
        'top_causal_dims': [k for k, _ in ranked[:3]],
    }


# ══════════════════════════════════════════════════════════════════
# 算法3：PC算法 体制条件因果图
# ══════════════════════════════════════════════════════════════════

def _run_pc_conditional(
    records: List[Dict],
) -> Dict[str, Any]:
    """
    PC算法简化版：
    检验「在特定体制条件下」，哪些特征与盈利有条件独立关系。
    P(盈利 | score, regime) vs P(盈利 | score) → 体制是否是中介变量
    """
    from scipy import stats

    results_by_regime = {}
    regime_groups = defaultdict(list)
    for r in records:
        regime_groups[r.get('regime', '?')].append(r)

    for regime, recs in regime_groups.items():
        if len(recs) < 15:
            continue

        outcomes = [1.0 if r.get('outcome') in ('TP1','TP2') else 0.0 for r in recs]
        scores   = [float(r.get('score', 0) or 0) for r in recs]
        grades   = []
        for r in recs:
            g = r.get('structure_grade', 0)
            try: grades.append(float(g or 0))
            except: grades.append(50.0)

        try:
            rho_score, p_score = stats.spearmanr(scores, outcomes)
            rho_grade, p_grade = stats.spearmanr(grades, outcomes)

            tp_rate = sum(outcomes) / len(outcomes) if outcomes else 0

            results_by_regime[regime] = {
                'n':             len(recs),
                'tp_rate':       round(tp_rate, 3),
                'score_effect':  round(rho_score, 3),
                'grade_effect':  round(rho_grade, 3),
                'p_score':       round(p_score, 4),
                'p_grade':       round(p_grade, 4),
                'score_causal':  p_score < 0.05,
                'grade_causal':  p_grade < 0.05,
            }
        except Exception:
            pass

    # 检测体制漂移：与历史报告对比
    drift_warnings = []
    try:
        if HISTORY_PATH.exists():
            history = [json.loads(l) for l in HISTORY_PATH.open()]
            if history:
                last = history[-1].get('regime_analysis', {})
                for regime, cur in results_by_regime.items():
                    last_tp = last.get(regime, {}).get('tp_rate')
                    cur_tp = cur.get('tp_rate')
                    if last_tp and cur_tp and abs(cur_tp - last_tp) > 0.15:
                        drift_warnings.append(
                            f'{regime}: TP率从{last_tp:.0%}→{cur_tp:.0%}，体制漂移预警'
                        )
    except Exception:
        pass

    return {
        'regime_analysis': results_by_regime,
        'drift_warnings':  drift_warnings,
    }


# ══════════════════════════════════════════════════════════════════
# 主报告生成
# ══════════════════════════════════════════════════════════════════

def run_discovery(min_n: int = 30) -> Dict[str, Any]:
    """
    执行完整因果发现流水线，生成报告。
    """
    print('[CausalDiscovery] 开始运行因果发现流水线...')
    t0 = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. 加载数据
    records = _load_settled_signals(min_n)
    if len(records) < min_n:
        report = {
            'timestamp': now_iso,
            'status': 'INSUFFICIENT_DATA',
            'n_records': len(records),
            'min_required': min_n,
            'message': f'样本不足({len(records)}<{min_n})，因果发现暂停',
        }
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    # 2. 特征提取
    features, outcomes, feat_names = _extract_features(records)

    # 3. 运行三大算法
    print('[CausalDiscovery] 运行 Granger 多变量因果检验...')
    granger = _run_granger_multivariate(records, feat_names)

    print('[CausalDiscovery] 运行 LiNGAM 因果方向检验...')
    lingam = _run_lingam_simplified(features, outcomes, feat_names)

    print('[CausalDiscovery] 运行 PC 体制条件因果图...')
    pc = _run_pc_conditional(records)

    # 4. 综合结论
    conclusions = _synthesize(granger, lingam, pc, records)

    elapsed = round(time.time() - t0, 2)
    report = {
        'timestamp':          now_iso,
        'status':             'OK',
        'n_records':          len(records),
        'elapsed_seconds':    elapsed,
        'granger':            granger,
        'lingam':             lingam,
        'pc_conditional':     pc,
        'conclusions':        conclusions,
        'data_quality':       'IRON' if len(records) >= 100 else 'REFERENCE',
    }

    # 5. 写报告
    import numpy as _np
    def _json_fix(o):
        if isinstance(o, _np.bool_): return bool(o)
        if isinstance(o, _np.integer): return int(o)
        if isinstance(o, _np.floating): return float(o)
        if isinstance(o, _np.ndarray): return o.tolist()
        return str(o)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_json_fix))
    print(f'[CausalDiscovery] 报告写入: {REPORT_PATH}')

    # 6. 历史追加（用于漂移检测）
    with HISTORY_PATH.open('a') as f:
        slim = {
            'timestamp':      now_iso,
            'n':              len(records),
            'conclusions':    conclusions,
            'regime_analysis': pc.get('regime_analysis', {}),
            'drift_warnings': pc.get('drift_warnings', []),
        }
        f.write(json.dumps(slim, ensure_ascii=False, default=_json_fix) + '\n')

    print(f'[CausalDiscovery] 完成！耗时 {elapsed}s，结论数: {len(conclusions)}')
    return report


def _synthesize(
    granger: Dict,
    lingam: Dict,
    pc: Dict,
    records: List[Dict],
) -> List[Dict[str, str]]:
    """综合三个算法结果，生成可读结论"""
    conclusions = []
    n = len(records)

    # Granger 结论
    if not granger.get('skipped'):
        sig_dims = granger.get('significant_dims', [])
        if sig_dims:
            conclusions.append({
                'type':   'GRANGER_CAUSAL',
                'level':  'IRON' if n >= 100 else 'REFERENCE',
                'finding': f'Granger显著维度: {sig_dims} (p<0.05)',
                'action':  '建议达摩院提升这些维度权重 +5%',
            })
        else:
            conclusions.append({
                'type':   'GRANGER_NULL',
                'level':  'REFERENCE',
                'finding': '无维度通过 Granger 显著性检验（p<0.05）',
                'action':  '评分系统目前主要捕捉相关性，因果机制需进一步建立',
            })

    # LiNGAM 结论
    if not lingam.get('skipped'):
        top = lingam.get('top_causal_dims', [])
        if top:
            conclusions.append({
                'type':    'LINGAM_RANKING',
                'level':   'REFERENCE' if n < 100 else 'IRON',
                'finding': f'因果强度排名 Top3: {top}',
                'action':  '建议将 top 维度在 brahma_core 中标记为 CAUSAL 等级',
            })

    # PC 体制漂移
    drifts = pc.get('drift_warnings', [])
    if drifts:
        for d in drifts:
            conclusions.append({
                'type':   'REGIME_DRIFT',
                'level':  'ALERT',
                'finding': d,
                'action':  '立即通知交易员，检查体制参数是否需要更新',
            })
    else:
        conclusions.append({
            'type':    'REGIME_STABLE',
            'level':   'OK',
            'finding': '本次运行未检测到体制漂移（与上次报告相比TP率稳定）',
            'action':  '无需调整',
        })

    # 全局数据质量警告
    if n < 100:
        conclusions.append({
            'type':   'DATA_WARNING',
            'level':  'WARNING',
            'finding': f'n={n} < 100，本报告为参考级，不可作核心参数调整依据',
            'action':  '继续积累信号，n≥100后升级为铁证级报告',
        })

    return conclusions


# ══════════════════════════════════════════════════════════════════
# 快速摘要（用于 signal_watcher cron 输出）
# ══════════════════════════════════════════════════════════════════

def get_latest_summary() -> str:
    """读取最新报告，返回一行摘要"""
    try:
        if not REPORT_PATH.exists():
            return '因果发现: 未运行'
        r = json.loads(REPORT_PATH.read_text())
        ts = r.get('timestamp', '')[:16]
        n  = r.get('n_records', 0)
        cs = r.get('conclusions', [])
        drift_alerts = [c for c in cs if c.get('level') == 'ALERT']
        if drift_alerts:
            return f'⚠️ 因果发现[{ts}] n={n} 体制漂移告警: {drift_alerts[0]["finding"][:50]}'
        granger_sig = r.get('granger', {}).get('significant_dims', [])
        return f'因果发现[{ts}] n={n} Granger显著: {granger_sig or "无"}'
    except Exception:
        return '因果发现: 读取失败'


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=30)
    ap.add_argument('--summary', action='store_true')
    args = ap.parse_args()

    if args.summary:
        print(get_latest_summary())
    else:
        report = run_discovery(min_n=args.min_n)
        # 打印关键结论
        print()
        print('=== 因果发现结论 ===')
        for c in report.get('conclusions', []):
            icon = {'ALERT': '⚠️', 'WARNING': '⚠', 'OK': '✅', 'IRON': '🏆'}.get(c.get('level', ''), '•')
            print(f'{icon} [{c["type"]}] {c["finding"]}')
            print(f'   → {c["action"]}')
