#!/usr/bin/env python3
"""
calibration_engine.py — P0 置信度校准引擎（QuantDinger Ensemble启发）
score + bull_bear差值 → 动态调整评分 → 真实等级

核心逻辑：
  高分但多空势均力敌 → 降级（虚高信号）
  中分但方向极度确定 → 升级（低估信号）

用法：
  from calibration_engine import calibrate, calibrated_label
  adj_score, report = calibrate(base_score=158, bull_score=52, bear_score=48)
"""

from __future__ import annotations

# ── 置信度校准参数（SSOT） ─────────────────────────────────
CONVICTION_THRESHOLDS = {
    'STRONG':  (35, +6),   # 差值≥35 → +6分（方向极度确定）
    'LEAN':    (20, +2),   # 差值≥20 → +2分（方向偏向明确）
    'NEUTRAL': (10,  0),   # 差值≥10 → ±0（轻微偏向）
    'WEAK':    ( 0, -6),   # 差值< 10 → -6分（方向模糊）
    'SPLIT':   (-1,-14),   # bear>bull（方向反转）→ -14分（警告）
}

# 五级标签映射（同push_hub.py保持一致）
def score_to_label(score: float) -> str:
    if score >= 170:  return '神级🏆'
    elif score >= 155: return 'A级✅'
    elif score >= 140: return 'B级📌'
    elif score >= 120: return 'C级👀'
    else:              return 'D级⚠️'


def calibrate(
    base_score: float,
    bull_score: float = 0,
    bear_score: float = 0,
    direction: str = '',
    regime: str = '',
    verbose: bool = False,
) -> tuple[float, dict]:
    """
    置信度校准主函数
    
    Returns:
        (adjusted_score, report_dict)
    """
    report = {
        'base_score': round(base_score, 1),
        'bull_score': round(bull_score, 1),
        'bear_score': round(bear_score, 1),
        'conviction': 0.0,
        'conviction_label': 'UNKNOWN',
        'calibration_adj': 0,
        'final_score': round(base_score, 1),
        'base_label': score_to_label(base_score),
        'final_label': score_to_label(base_score),
        'upgraded': False,
        'downgraded': False,
        'reason': '',
    }

    if bull_score == 0 and bear_score == 0:
        # 未提供bull/bear数据，跳过校准
        report['reason'] = 'bull_bear数据缺失，跳过校准'
        return base_score, report

    conviction = round(bull_score - bear_score, 1)
    report['conviction'] = conviction

    # 方向一致性检查（信号方向 vs bull_bear方向）
    # bull_score高 = 市场偏多； bear_score高 = 市场偏空
    # 做多时：需要bull>bear，属顺势
    # 做空时：需要bear>bull，属顺势
    is_long  = 'LONG'  in direction.upper() or '多' in direction
    is_short = 'SHORT' in direction.upper() or '空' in direction

    # 将conviction转换为该方向的顺势度（正就是顺势）
    # direction_conviction: 正值=方向顺势，负值=方向逆势
    # SHORT顺势 = bear > bull → direction_conviction = bear-bull
    # LONG顺势  = bull > bear → direction_conviction = bull-bear = conviction
    if is_short:
        direction_conviction = round(bear_score - bull_score, 1)
    else:
        direction_conviction = round(bull_score - bear_score, 1)

    report['conviction'] = direction_conviction

    direction_aligned = direction_conviction >= 0
    if not direction_aligned:
        if is_long:
            report['reason'] += f'⚠️ 逆势做多(bull={bull_score:.0f}<bear={bear_score:.0f}) '
        else:
            report['reason'] += f'⚠️ 逆势做空(bear={bear_score:.0f}<bull={bull_score:.0f}) '

    # 计算校准调整量
    abs_conv = abs(direction_conviction)
    if not direction_aligned:
        adj = CONVICTION_THRESHOLDS['SPLIT'][1]
        conv_label = 'SPLIT（方向背离）'
    elif abs_conv >= CONVICTION_THRESHOLDS['STRONG'][0]:
        adj = CONVICTION_THRESHOLDS['STRONG'][1]
        conv_label = 'STRONG'
    elif abs_conv >= CONVICTION_THRESHOLDS['LEAN'][0]:
        adj = CONVICTION_THRESHOLDS['LEAN'][1]
        conv_label = 'LEAN'
    elif abs_conv >= CONVICTION_THRESHOLDS['NEUTRAL'][0]:
        adj = CONVICTION_THRESHOLDS['NEUTRAL'][1]
        conv_label = 'NEUTRAL'
    else:
        adj = CONVICTION_THRESHOLDS['WEAK'][1]
        conv_label = 'WEAK（多空均衡）'

    # 体制加成：顺势体制且STRONG → 额外+3
    regime_bonus = 0
    if is_short and 'BEAR' in regime.upper() and conv_label == 'STRONG':
        regime_bonus = 3
        report['reason'] += ' 体制顺势加成+3'
    elif is_long and 'BULL' in regime.upper() and conv_label == 'STRONG':
        regime_bonus = 3
        report['reason'] += ' 体制顺势加成+3'

    total_adj = adj + regime_bonus
    final_score = round(base_score + total_adj, 1)

    # 更新报告
    report['conviction_label'] = conv_label
    report['calibration_adj'] = total_adj
    report['final_score'] = final_score
    report['base_label'] = score_to_label(base_score)
    report['final_label'] = score_to_label(final_score)
    report['upgraded'] = score_to_label(final_score) != score_to_label(base_score) and final_score > base_score
    report['downgraded'] = score_to_label(final_score) != score_to_label(base_score) and final_score < base_score

    if not report['reason']:
        report['reason'] = f'置信差={direction_conviction:+.1f}({conv_label}) → 调整{total_adj:+d}分'
    else:
        report['reason'] += f' 置信差={direction_conviction:+.1f}({conv_label}) → 调整{total_adj:+d}分'

    if verbose:
        print(f'[Calibration] {base_score:.0f}→{final_score:.0f} '
              f'({report["base_label"]}→{report["final_label"]}) '
              f'bull={bull_score:.0f} bear={bear_score:.0f} '
              f'conv={conviction:+.1f} adj={total_adj:+d}')

    return final_score, report


def calibrated_label(base_score: float, bull_score: float, bear_score: float,
                     direction: str = '', regime: str = '') -> str:
    """快速获取校准后的等级标签"""
    final, _ = calibrate(base_score, bull_score, bear_score, direction, regime)
    return score_to_label(final)


def full_calibration_pipeline(
    symbol: str,
    direction: str,
    base_score: float,
    regime: str = '',
) -> tuple[float, dict, dict]:
    """
    完整校准流水线：自动调用bull_bear_engine → 校准
    Returns: (final_score, calibration_report, bull_bear_result)
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    bb_result = {'bull_score': 0, 'bear_score': 0, 'bias_label': 'UNKNOWN', 'error': None}
    try:
        from bull_bear_engine import debate
        bb_result = debate(symbol)
    except Exception as e:
        bb_result['error'] = str(e)
        print(f'[Calibration] bull_bear_engine失败，跳过校准: {e}')
        return base_score, {'base_score': base_score, 'final_score': base_score,
                            'reason': f'bull_bear失败: {e}'}, bb_result

    final, report = calibrate(
        base_score=base_score,
        bull_score=bb_result.get('bull_score', 0),
        bear_score=bb_result.get('bear_score', 0),
        direction=direction,
        regime=regime,
        verbose=True,
    )
    return final, report, bb_result


if __name__ == '__main__':
    import sys
    print('=== 置信度校准演示 ===\n')

    cases = [
        ('ETH', 'SHORT', 158, 'BEAR_TREND', 62, 18, '高分+方向确定'),
        ('ETH', 'SHORT', 158, 'BEAR_TREND', 42, 40, '高分+多空均衡'),
        ('BTC', 'LONG',  145, 'BEAR_TREND', 18, 55, '做多但Bear更强'),
        ('BTC', 'SHORT', 152, 'BEAR_TREND', 70, 28, '顺势+体制加成'),
        ('SOL', 'SHORT', 140, 'NEUTRAL',    35, 33, '中性体制弱差值'),
    ]

    for sym, direction, score, regime, bull, bear, desc in cases:
        final, report = calibrate(score, bull, bear, direction, regime)
        change = '⬆️升级' if report['upgraded'] else ('⬇️降级' if report['downgraded'] else '持平')
        print(f'{desc}')
        print(f'  {score:.0f}分({report["base_label"]}) → {final:.0f}分({report["final_label"]}) {change}')
        print(f'  {report["reason"]}')
        print()
