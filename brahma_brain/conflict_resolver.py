"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 信号冲突解决，多信号协调
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
conflict_resolver.py  — I2 多维度冲突解析器 (Brahma v12.8)
──────────────────────────────────────────────────────────
功能：
  检测15个维度之间的方向性冲突，计算置信衰减，
  给出仲裁建议（增强/维持/降权/拒绝）

冲突分类:
  C1 HARD_CONFLICT: 核心维度与信号方向强烈对立 → 拒绝
  C2 SOFT_CONFLICT: 辅助维度分歧 → 降权
  C3 CONSENSUS:     多数维度一致 → 增强

核心维度（权重最高）: D04 SMC, D05 Volume, D02 KeyLvl, D01 Trend
辅助维度: D03/D06/D07/D08/D09/D10/D11/D12/D13/D14/D15
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

# 维度元信息
DIM_META = {
    '趋势一致性':       {'id':'D01', 'tier':'CORE',  'polarity':'directional', 'weight':0.12},
    '关键位精确度':     {'id':'D02', 'tier':'CORE',  'polarity':'neutral',     'weight':0.10},
    '动量背离':         {'id':'D03', 'tier':'AUX',   'polarity':'directional', 'weight':0.07},
    'SMC结构':          {'id':'D04', 'tier':'CORE',  'polarity':'directional', 'weight':0.14},
    '量能验证':         {'id':'D05', 'tier':'CORE',  'polarity':'neutral',     'weight':0.12},
    '形态成熟度':       {'id':'D06', 'tier':'AUX',   'polarity':'neutral',     'weight':0.06},
    '清算/OI':          {'id':'D07', 'tier':'AUX',   'polarity':'directional', 'weight':0.07},
    '情绪/费率':        {'id':'D08', 'tier':'AUX',   'polarity':'directional', 'weight':0.06},
    '时段权重':         {'id':'D09', 'tier':'AUX',   'polarity':'neutral',     'weight':0.05},
    '谐波+多周期':      {'id':'D10', 'tier':'AUX',   'polarity':'neutral',     'weight':0.05},
    '鲸鱼+跨市场+微观': {'id':'D11', 'tier':'AUX',   'polarity':'directional', 'weight':0.07},
    '期权+订单流':      {'id':'D12', 'tier':'AUX',   'polarity':'directional', 'weight':0.06},
    'L2+贝叶斯+宏观':   {'id':'D13', 'tier':'AUX',   'polarity':'directional', 'weight':0.05},
    'ML+在线贝叶斯+滑点':{'id':'D14','tier':'AUX',   'polarity':'neutral',     'weight':0.05},
    'LSTM+NLP情绪':     {'id':'D15', 'tier':'AUX',   'polarity':'directional', 'weight':0.03},
}

CORE_DIMS = [k for k,v in DIM_META.items() if v['tier']=='CORE']
DIR_DIMS  = [k for k,v in DIM_META.items() if v['polarity']=='directional']


def resolve(
    breakdown: dict,
    signal_dir: str,
    total_score: float,
) -> dict:
    """
    分析breakdown中各维度的一致性

    Args:
        breakdown:   {dim_name: score}
        signal_dir:  'LONG'|'SHORT'|'做多'|'做空'
        total_score: 总分

    Returns:
        {
          'verdict':        'APPROVE'|'DOWNWEIGHT'|'REJECT',
          'confidence_adj': float (0.5~1.2),
          'conflicts':      list[dict],
          'consensus_dims': list[str],
          'conflict_summary': str,
        }
    """
    is_long = signal_dir in ('LONG', '做多')

    conflicts = []
    consensus_dims = []
    weighted_agreement = 0.0
    total_weight = 0.0

    for dim, score in breakdown.items():
        meta = DIM_META.get(dim)
        if not meta: continue
        if meta['polarity'] == 'neutral': continue  # 中性维度不参与方向冲突判断

        w = meta['weight']
        total_weight += w

        # 方向性判断：正分=有利当前方向，负分=不利
        if is_long:
            # 做多：正分=支持，负分=冲突
            direction_ok = score >= 0
        else:
            # 做空：负分=支持（大多数维度空头时负分更好），正分=冲突
            # 实际上系统里各维度的score在做空时也可能为正（满足做空条件）
            # 判断：极度负分（<-5）在做空里意味着做多信号强 → 冲突
            direction_ok = score > -8  # 仅超级极端才视为冲突

        if direction_ok:
            weighted_agreement += w
            consensus_dims.append(dim)
        else:
            tier = meta['tier']
            severity = 'HARD' if tier == 'CORE' else 'SOFT'
            conflicts.append({
                'dim': dim, 'id': meta['id'], 'score': score,
                'tier': tier, 'severity': severity, 'weight': w
            })

    agreement_ratio = weighted_agreement / (total_weight + 1e-9)

    # 统计冲突级别
    hard_conflicts = [c for c in conflicts if c['severity'] == 'HARD']
    soft_conflicts = [c for c in conflicts if c['severity'] == 'SOFT']

    # ── 仲裁逻辑 ─────────────────────────────────────────
    if len(hard_conflicts) >= 2:
        verdict = 'REJECT'
        confidence_adj = 0.0
        summary = f'HARD CONFLICT x{len(hard_conflicts)}: {[c["id"] for c in hard_conflicts]} — signal rejected'
    elif len(hard_conflicts) == 1 and len(soft_conflicts) >= 3:
        verdict = 'REJECT'
        confidence_adj = 0.0
        summary = f'1 HARD + {len(soft_conflicts)} SOFT conflicts — reject'
    elif len(hard_conflicts) == 1:
        verdict = 'DOWNWEIGHT'
        confidence_adj = 0.60
        summary = f'1 HARD conflict [{hard_conflicts[0]["id"]}={hard_conflicts[0]["score"]}] — downweighted 40%'
    elif len(soft_conflicts) >= 4:
        verdict = 'DOWNWEIGHT'
        confidence_adj = 0.75
        summary = f'{len(soft_conflicts)} SOFT conflicts — downweighted 25%'
    elif agreement_ratio >= 0.75:
        verdict = 'APPROVE'
        confidence_adj = min(1.2, 0.8 + agreement_ratio * 0.5)
        summary = f'Strong consensus ({agreement_ratio:.0%}) — confidence boosted'
    else:
        verdict = 'APPROVE'
        confidence_adj = 0.85 + agreement_ratio * 0.2
        summary = f'Moderate consensus ({agreement_ratio:.0%}) — normal confidence'

    confidence_adj = max(0.0, min(1.2, confidence_adj))

    return {
        'verdict': verdict,
        'confidence_adj': round(confidence_adj, 3),
        'agreement_ratio': round(agreement_ratio, 3),
        'hard_conflicts': len(hard_conflicts),
        'soft_conflicts': len(soft_conflicts),
        'conflicts': conflicts,
        'consensus_dims': consensus_dims,
        'conflict_summary': summary,
        'ts': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    # 测试案例
    bd_long = {'趋势一致性':8, 'SMC结构':12, '量能验证':10, '动量背离':-3,
               '清算/OI':5, '情绪/费率':-1, 'ML+在线贝叶斯+滑点':1}
    r = resolve(bd_long, 'LONG', 115)
    print(f"LONG: {r['verdict']}  conf={r['confidence_adj']}  {r['conflict_summary']}")

    bd_short = {'趋势一致性':-6, 'SMC结构':-8, '量能验证':3, '动量背离':5,
                '清算/OI':-4, '情绪/费率':3}
    r2 = resolve(bd_short, 'SHORT', 108)
    print(f"SHORT: {r2['verdict']}  conf={r2['confidence_adj']}  {r2['conflict_summary']}")
