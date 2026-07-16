#!/usr/bin/env python3
"""
online_learner_v2.py — 梵天在线学习引擎 v2.0
设计院 · 苏摩111批准 · 2026-07-15

职责：
  - 读取 performance_log 中已结算交易
  - 计算各维度信号的预测偏差
  - 当偏差 > 阈值时自动校准权重
  - 输出校准报告写入 data/calibration_log.jsonl

触发：brahma-online-calibrate cron every 24h
"""

import json, time, math
from pathlib import Path
from collections import defaultdict

BASE         = Path(__file__).parent.parent
PERF_LOG     = BASE / 'data' / 'live_performance_log.jsonl'
CALIB_LOG    = BASE / 'data' / 'calibration_log.jsonl'
WEIGHT_FILE  = BASE / 'data' / 'signal_weights.json'

# 偏差阈值：超过此值触发校准
BIAS_THRESHOLD = 0.15   # 15%
MIN_SAMPLES    = 5       # 最少样本数
MAX_WEIGHT_DELTA = 0.10  # 单次最大权重调整幅度

# 默认权重基准
DEFAULT_WEIGHTS = {
    'regime'     : 1.0,
    'structure'  : 1.0,
    'timing'     : 1.0,
    'kronos'     : 0.7,
    'macro'      : 0.8,
    'smart_money': 0.6,
    'obv'        : 0.8,
    'oi_signal'  : 0.7,
}


def load_weights() -> dict:
    if WEIGHT_FILE.exists():
        try:
            return json.loads(WEIGHT_FILE.read_text())
        except Exception:
            pass
    return DEFAULT_WEIGHTS.copy()


# [达摩院接入 2026-07-16 苏摩111] 维度映射表：online_learner_v2 → brahma_scoring INT-1 格式
# brahma_scoring 读取 data/calibrated_weights.json，键名为 s1_trend~s8 格式
_DIM_MAP = {
    'regime'     : 's1_trend',
    'structure'  : 's4_smc',
    'timing'     : 's3_momentum',
    'kronos'     : 's7_kronos',
    'macro'      : 's5_macro',
    'smart_money': 's6_whale',
    'obv'        : 's2_ob',
    'oi_signal'  : 's8_oi',
}
_CALIB_WEIGHTS_FILE = Path(__file__).parent.parent / 'data' / 'calibrated_weights.json'


def save_weights(w: dict):
    WEIGHT_FILE.parent.mkdir(exist_ok=True)
    WEIGHT_FILE.write_text(json.dumps(w, indent=2, ensure_ascii=False))
    # [达摩院接入 2026-07-16] 同步写入 calibrated_weights.json（brahma_scoring INT-1读取源）
    try:
        import time as _t
        calib = {}
        # 先加载现有文件保留未映射维度
        if _CALIB_WEIGHTS_FILE.exists():
            try:
                calib = json.loads(_CALIB_WEIGHTS_FILE.read_text())
            except Exception:
                calib = {}
        now_iso = _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime())
        for src_dim, tgt_dim in _DIM_MAP.items():
            if src_dim in w:
                calib[tgt_dim] = {
                    'mult'       : float(w[src_dim]),
                    'note'       : f'{src_dim}→{tgt_dim} [online_learner_v2]',
                    'last_calib' : now_iso,
                }
        _CALIB_WEIGHTS_FILE.write_text(json.dumps(calib, indent=2, ensure_ascii=False))
    except Exception as _se:
        pass  # 写入失败不阻断主流程


def load_performance(days: int = 30) -> list:
    """加载最近N天的已结算交易"""
    if not PERF_LOG.exists():
        return []
    cutoff = time.time() - days * 86400
    records = []
    for line in PERF_LOG.read_text().strip().splitlines():
        try:
            r = json.loads(line)
            if float(r.get('settled_at', r.get('ts', 0))) > cutoff:
                if r.get('result') in ('WIN', 'LOSS', 'BE'):
                    records.append(r)
        except Exception:
            pass
    return records


def compute_dimension_bias(records: list) -> dict:
    """
    计算各维度的预测偏差
    偏差 = (实际胜率 - 预期胜率) / 预期胜率
    """
    dim_stats = defaultdict(lambda: {'win': 0, 'total': 0, 'expected_wr': []})

    for r in records:
        result  = r.get('result', 'LOSS')
        dims    = r.get('dimension_scores', {})
        exp_wr  = float(r.get('expected_wr', 0.6))

        for dim, score in dims.items():
            if score and float(score) > 0:
                dim_stats[dim]['total'] += 1
                dim_stats[dim]['expected_wr'].append(exp_wr)
                if result == 'WIN':
                    dim_stats[dim]['win'] += 1

    biases = {}
    for dim, stat in dim_stats.items():
        if stat['total'] < MIN_SAMPLES:
            continue
        actual_wr   = stat['win'] / stat['total']
        avg_exp_wr  = sum(stat['expected_wr']) / len(stat['expected_wr'])
        if avg_exp_wr > 0:
            bias = (actual_wr - avg_exp_wr) / avg_exp_wr
            biases[dim] = {
                'actual_wr'  : round(actual_wr, 4),
                'expected_wr': round(avg_exp_wr, 4),
                'bias'       : round(bias, 4),
                'samples'    : stat['total'],
            }
    return biases


def calibrate_weights(biases: dict, current_weights: dict) -> tuple[dict, list]:
    """
    根据偏差校准权重
    正偏差（实际>预期）→ 增加权重
    负偏差（实际<预期）→ 降低权重
    """
    new_weights = current_weights.copy()
    actions = []

    for dim, info in biases.items():
        bias = info['bias']
        if abs(bias) < BIAS_THRESHOLD:
            continue

        if dim not in new_weights:
            continue

        old_w = new_weights[dim]
        # 权重调整 = bias × 0.3，限制在MAX_WEIGHT_DELTA内
        delta = max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA, bias * 0.3))
        new_w = max(0.1, min(2.0, old_w + delta))
        new_weights[dim] = round(new_w, 4)

        actions.append({
            'dim'       : dim,
            'old_weight': old_w,
            'new_weight': new_w,
            'delta'     : round(delta, 4),
            'bias'      : bias,
            'samples'   : info['samples'],
            'actual_wr' : info['actual_wr'],
        })

    return new_weights, actions


def write_calib_log(actions: list, summary: dict):
    entry = {
        'ts'      : time.time(),
        'ts_iso'  : time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'actions' : actions,
        'summary' : summary,
    }
    CALIB_LOG.parent.mkdir(exist_ok=True)
    with open(CALIB_LOG, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def run(dry_run: bool = False) -> dict:
    """主入口"""
    records = load_performance(days=30)
    now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    if len(records) < MIN_SAMPLES:
        msg = f'样本不足({len(records)}<{MIN_SAMPLES})，跳过校准'
        print(f'[online_learner_v2] {now_iso} {msg}')
        return {'status': 'SKIP', 'reason': msg, 'samples': len(records)}

    biases  = compute_dimension_bias(records)
    weights = load_weights()
    new_weights, actions = calibrate_weights(biases, weights)

    triggered = len(actions) > 0
    summary = {
        'ts'          : now_iso,
        'total_records': len(records),
        'dims_analyzed': len(biases),
        'dims_adjusted': len(actions),
        'triggered'   : triggered,
    }

    if triggered and not dry_run:
        save_weights(new_weights)
        write_calib_log(actions, summary)
        print(f'[online_learner_v2] {now_iso} 校准完成: {len(actions)}个维度调整')
        for a in actions:
            print(f'  {a["dim"]}: {a["old_weight"]} → {a["new_weight"]} (bias={a["bias"]:+.3f} n={a["samples"]})')
    else:
        reason = 'dry_run' if dry_run else '无偏差超阈值维度'
        print(f'[online_learner_v2] {now_iso} 无需校准: {reason}')
        print(f'  分析维度: {len(biases)} | 超阈值: {len(actions)}')

    return {
        'status'  : 'CALIBRATED' if (triggered and not dry_run) else 'NO_CHANGE',
        'summary' : summary,
        'actions' : actions,
        'biases'  : biases,
    }


if __name__ == '__main__':
    import sys
    dry = '--dry-run' in sys.argv
    result = run(dry_run=dry)
    print(json.dumps(result.get('summary', {}), indent=2, ensure_ascii=False))
