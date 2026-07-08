"""
online_learner_v2.py — 梵天在线学习参数自适应 v2.0
设计院 P3-C | 2026-07-08

功能:
  - 每周日 03:00 UTC 自动 Walk-Forward 重新校准评分权重
  - 实盘偏差监控: 预测WR vs 实际WR 偏离>15% → 触发紧急校准
  - 自适应维度: N21(FibMacro) / N22(VolExh) / s7(OBHeatmap) 权重
  - 滚动窗口: 最近60个信号为训练集，防止过拟合
  - 输出: calibrated_weights.json → brahma_core 热加载
"""
import json, time, sys
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR  = Path(__file__).parent.parent / 'data'
WEIGHT_FILE = DATA_DIR / 'calibrated_weights.json'
CALIB_LOG   = DATA_DIR / 'calibration_log.jsonl'
sys.path.insert(0, str(Path(__file__).parent.parent))

# 可自适应的评分维度（来自 brahma_core 的 N/s 编号）
ADAPTIVE_DIMS = {
    'N21_fib_macro':    {'default': 6,   'min': 2,   'max': 12},
    'N22_vol_exh':      {'default': 15,  'min': 8,   'max': 25},
    's7_ob_heatmap':    {'default': 8,   'min': 4,   'max': 15},
    's_cross_fr':       {'default': 3,   'min': 1,   'max': 6},
    'bull_bonus_cap':   {'default': 25,  'min': 15,  'max': 35},
    'mtf_neutral_mult': {'default': 0.98,'min': 0.94,'max': 1.00},
}

# 校准触发条件
CALIB_CONFIG = {
    'schedule_weekday': 6,        # 每周日(0=Mon)
    'schedule_hour_utc': 3,
    'min_samples':       20,      # 最少样本数
    'rolling_window':    60,      # 滚动窗口
    'deviation_trigger': 0.15,    # 实盘偏差>15%触发紧急校准
    'learning_rate':     0.1,     # 权重更新步长
}


def load_signal_outcomes(days: int = 60) -> list:
    """
    加载信号+实际结果
    从 auto_executor_log + live_signal_log 联合构建
    """
    cutoff = time.time() - days * 86400
    outcomes = []

    sig_path = DATA_DIR / 'live_signal_log.jsonl'
    exe_path = DATA_DIR / 'auto_executor_log.jsonl'
    if not sig_path.exists():
        return []

    # 读信号
    sigs = {}
    with open(sig_path) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                ts_str = d.get('ts_iso', '')
                if not ts_str: continue
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).timestamp()
                if ts >= cutoff and d.get('valid'):
                    sigs[d.get('signal_id', '')] = d
            except Exception:
                pass

    # 读执行结果
    if exe_path.exists():
        with open(exe_path) as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    sig_id = d.get('signal_id', '')
                    if sig_id in sigs:
                        sig = sigs[sig_id]
                        outcomes.append({
                            'signal_id': sig_id,
                            'symbol':    sig.get('symbol'),
                            'score':     float(sig.get('score', 0) or 0),
                            'regime':    sig.get('regime', ''),
                            'direction': sig.get('direction', ''),
                            'n21':       float(sig.get('n21_score', 0) or 0),
                            'n22':       float(sig.get('n22_score', 0) or 0),
                            's7':        float(sig.get('s7_score', 0) or 0),
                            'result':    d.get('result', ''),
                            'pnl':       float(d.get('pnl', 0) or 0),
                            'win':       float(d.get('pnl', 0) or 0) > 0,
                        })
                except Exception:
                    pass

    return outcomes


def load_current_weights() -> dict:
    """加载当前校准权重（不存在则返回默认值）"""
    if WEIGHT_FILE.exists():
        try:
            w = json.loads(WEIGHT_FILE.read_text())
            # 验证格式
            for k in ADAPTIVE_DIMS:
                if k not in w:
                    w[k] = ADAPTIVE_DIMS[k]['default']
            return w
        except Exception:
            pass
    return {k: v['default'] for k, v in ADAPTIVE_DIMS.items()}


def _clamp(val, dim_key):
    cfg = ADAPTIVE_DIMS[dim_key]
    return round(max(cfg['min'], min(cfg['max'], val)), 4)


def run_calibration(force: bool = False) -> dict:
    """
    主校准入口
    
    Returns: {
        'calibrated': bool,
        'old_weights': dict,
        'new_weights': dict,
        'delta': dict,
        'n_samples': int,
        'predicted_wr': float,
        'actual_wr': float,
    }
    """
    outcomes = load_signal_outcomes(days=CALIB_CONFIG['rolling_window'])
    old_weights = load_current_weights()

    if len(outcomes) < CALIB_CONFIG['min_samples']:
        return {
            'calibrated': False,
            'reason': f'样本不足 {len(outcomes)}<{CALIB_CONFIG["min_samples"]}',
            'n_samples': len(outcomes),
        }

    # 计算实际胜率
    wins = [o for o in outcomes if o.get('win')]
    actual_wr = len(wins) / len(outcomes)

    # 预测胜率（基于当前score分布的历史WR映射）
    score_avg = np.mean([o['score'] for o in outcomes])
    pred_wr = 0.50 + (score_avg - 135) * 0.003  # 线性近似

    deviation = abs(actual_wr - pred_wr)

    # 判断是否需要校准
    needs_calib = force or deviation > CALIB_CONFIG['deviation_trigger']
    if not needs_calib:
        return {
            'calibrated': False,
            'reason': f'偏差{deviation:.2%}<触发阈值{CALIB_CONFIG["deviation_trigger"]:.0%}',
            'actual_wr': round(actual_wr, 3),
            'predicted_wr': round(pred_wr, 3),
            'n_samples': len(outcomes),
        }

    # 梯度方向校准（简化梯度下降）
    lr = CALIB_CONFIG['learning_rate']
    new_weights = dict(old_weights)

    # 按维度贡献度调整
    for o in outcomes:
        if o.get('win'):
            # 盈利信号: 增强贡献较大的维度权重
            if o.get('n22', 0) > 10:
                new_weights['N22_vol_exh'] += lr * 0.5
            if o.get('s7', 0) > 5:
                new_weights['s7_ob_heatmap'] += lr * 0.3
        else:
            # 亏损信号: 降低可能导致误判的维度
            if o.get('n21', 0) > 5:
                new_weights['N21_fib_macro'] -= lr * 0.3
            # MTF NEUTRAL降权保护
            if 'CHOP' in o.get('regime', ''):
                new_weights['mtf_neutral_mult'] = max(
                    ADAPTIVE_DIMS['mtf_neutral_mult']['min'],
                    new_weights['mtf_neutral_mult'] - lr * 0.01
                )

    # 收益加权调整: 亏损>正常时收紧bull_bonus
    avg_pnl = np.mean([o['pnl'] for o in outcomes])
    if avg_pnl < 0:
        new_weights['bull_bonus_cap'] -= 1
    elif avg_pnl > 0.5:
        new_weights['bull_bonus_cap'] += 0.5

    # 约束裁剪
    for k in ADAPTIVE_DIMS:
        new_weights[k] = _clamp(new_weights[k], k)

    # 计算变化量
    delta = {k: round(new_weights[k] - old_weights[k], 4)
             for k in ADAPTIVE_DIMS}

    # 写入校准结果
    new_weights['_calibrated_at'] = datetime.now(timezone.utc).isoformat()
    new_weights['_actual_wr'] = round(actual_wr, 4)
    new_weights['_n_samples'] = len(outcomes)
    WEIGHT_FILE.write_text(json.dumps(new_weights, ensure_ascii=False, indent=2))

    # 写入校准日志
    log_entry = {
        'ts':           time.time(),
        'iso':          datetime.now(timezone.utc).isoformat(),
        'n_samples':    len(outcomes),
        'actual_wr':    round(actual_wr, 3),
        'predicted_wr': round(pred_wr, 3),
        'deviation':    round(deviation, 3),
        'delta':        delta,
        'forced':       force,
    }
    with open(CALIB_LOG, 'a') as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

    return {
        'calibrated':    True,
        'old_weights':   old_weights,
        'new_weights':   {k: new_weights[k] for k in ADAPTIVE_DIMS},
        'delta':         delta,
        'n_samples':     len(outcomes),
        'actual_wr':     round(actual_wr, 3),
        'predicted_wr':  round(pred_wr, 3),
        'deviation':     round(deviation, 3),
    }


def check_and_calibrate() -> dict:
    """
    定时检查入口（每次cron调用）
    仅在以下情况触发:
      1. 每周日 03:00 UTC
      2. 实盘偏差>15%
    """
    now = datetime.now(timezone.utc)

    # 检查实盘偏差（每次都检查，低成本）
    outcomes = load_signal_outcomes(days=14)  # 最近14天
    if len(outcomes) >= CALIB_CONFIG['min_samples']:
        wins = [o for o in outcomes if o.get('win')]
        actual_wr = len(wins) / len(outcomes)
        score_avg = np.mean([o['score'] for o in outcomes]) if outcomes else 135
        pred_wr   = 0.50 + (score_avg - 135) * 0.003
        deviation = abs(actual_wr - pred_wr)

        if deviation > CALIB_CONFIG['deviation_trigger']:
            result = run_calibration(force=True)
            result['trigger'] = 'deviation_alert'
            return result

    # 周计划检查
    is_schedule = (
        now.weekday() == CALIB_CONFIG['schedule_weekday']
        and now.hour == CALIB_CONFIG['schedule_hour_utc']
    )
    if is_schedule:
        result = run_calibration(force=True)
        result['trigger'] = 'weekly_schedule'
        return result

    return {'calibrated': False, 'reason': '未到校准时机', 'trigger': 'none'}


if __name__ == '__main__':
    print("梵天在线学习参数自适应 v2.0")
    print("=" * 50)
    current = load_current_weights()
    print(f"当前权重: {json.dumps(current, ensure_ascii=False, indent=2)}")
    print()
    result = check_and_calibrate()
    print(f"校准结果: calibrated={result['calibrated']} reason={result.get('reason','')}")
    if result.get('calibrated'):
        print(f"  样本数: {result['n_samples']}")
        print(f"  实际WR: {result['actual_wr']:.1%}")
        print(f"  权重变化: {result['delta']}")
