#!/usr/bin/env python3
"""
ic_feedback_engine.py — 梵天IC自动反馈回路
# ponytail: IC计算+权重调整，stdlib only，单文件搞定

核心逻辑:
1. 读取 wuqu_paper_settled.jsonl（实测结算数据）
2. 计算每个评分段/体制的WR和IC
3. 生成权重调整建议写入 wr_matrix_realtime.json
4. 输出人类可读报告

IC(信息系数) = 评分与结果的点二列相关系数
  IC > 0.1: 评分有预测力
  IC < 0:   评分权重需要反转
  -0.3:     当前梵天状态，高分反而低胜率
"""
import json, time, statistics, math
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

def _load_settled():
    p = DATA / 'wuqu_paper_settled.jsonl'
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()
            and json.loads(l).get('outcome') in ('TP1','TP2','SL')]

def _calc_ic(scores, outcomes):
    """点二列相关系数: IC = cov(score,outcome) / (std_score * std_outcome)"""
    n = len(scores)
    if n < 5:
        return 0.0, 'insufficient_data'
    mean_s = sum(scores) / n
    mean_o = sum(outcomes) / n
    cov = sum((scores[i]-mean_s)*(outcomes[i]-mean_o) for i in range(n)) / n
    try:
        std_s = statistics.stdev(scores)
        std_o = statistics.stdev(outcomes)
        if std_s * std_o == 0:
            return 0.0, 'no_variance'
        return cov / (std_s * std_o), 'ok'
    except Exception:
        return 0.0, 'error'

def _wr_by_bucket(settled):
    """按评分段统计WR"""
    buckets = defaultdict(lambda: {'win':0,'loss':0})
    for p in settled:
        sc = p.get('score', 0)
        key = '<130' if sc<130 else '130-145' if sc<145 else '145-160' if sc<160 else '160-175' if sc<175 else '>175'
        if p['outcome'] in ('TP1','TP2'):
            buckets[key]['win'] += 1
        else:
            buckets[key]['loss'] += 1
    return buckets

def _wr_by_regime(settled):
    """按体制+方向统计WR"""
    matrix = defaultdict(lambda: {'win':0,'loss':0,'pnls':[]})
    for p in settled:
        key = f"{p.get('regime','?')}:{p.get('signal_dir','?')}"
        if p['outcome'] in ('TP1','TP2'):
            matrix[key]['win'] += 1
        else:
            matrix[key]['loss'] += 1
        matrix[key]['pnls'].append(p.get('pnl_pct', 0))
    return matrix

def _generate_weight_adjustments(ic, buckets, regime_matrix):
    """
    根据IC和WR数据生成权重调整建议
    核心逻辑: IC<0说明评分高分区间WR低，需要收紧高分阈值
    """
    adjustments = {}

    # 1. 信号阈值调整
    best_bucket = max(
        [(k, v['win']/(v['win']+v['loss'])) for k, v in buckets.items() if v['win']+v['loss'] >= 5],
        key=lambda x: x[1],
        default=('140-155', 0.82)
    )
    adjustments['best_wr_bucket'] = {'bucket': best_bucket[0], 'wr': round(best_bucket[1], 4)}

    # 2. IC修正方向
    if ic < -0.1:
        adjustments['ic_alert'] = {
            'ic': round(ic, 4),
            'diagnosis': '高分低胜率，评分权重倒置',
            'action': '建议收紧ENTER_FULL阈值，降低>175分信号的仓位'
        }
    elif ic < 0.05:
        adjustments['ic_alert'] = {
            'ic': round(ic, 4),
            'diagnosis': '评分与胜率弱相关',
            'action': '建议加强HCME和方仓维度权重'
        }
    else:
        adjustments['ic_alert'] = {
            'ic': round(ic, 4),
            'diagnosis': '评分与胜率正相关，权重合理',
            'action': 'HOLD'
        }

    # 3. 体制乘数调整
    for key, stats in regime_matrix.items():
        total = stats['win'] + stats['loss']
        if total < 5:
            continue
        wr = stats['win'] / total
        avg_pnl = sum(stats['pnls']) / len(stats['pnls']) if stats['pnls'] else 0
        # 当前体制乘数（参考MEMORY.md）
        base_multipliers = {
            'BEAR_TREND:SHORT': 1.6, 'BULL_TREND:LONG': 1.6,
            'BEAR_EARLY:SHORT': 1.2, 'CHOP_MID:SHORT': 0.88,
        }
        base = base_multipliers.get(key, 1.0)
        # WR>80%且avgPnl>2%: 可以提升乘数
        if wr >= 0.80 and avg_pnl >= 2.0:
            suggested = round(min(base * 1.1, 1.8), 2)
            adjustments[f'multiplier_{key}'] = {
                'current': base, 'suggested': suggested,
                'wr': round(wr, 4), 'n': total, 'avg_pnl': round(avg_pnl, 2),
                'reason': 'WR>=80%且avgPnl>=2%，建议提升乘数'
            }
        # WR<60%: 需要降低乘数
        elif wr < 0.60:
            suggested = round(max(base * 0.8, 0.3), 2)
            adjustments[f'multiplier_{key}'] = {
                'current': base, 'suggested': suggested,
                'wr': round(wr, 4), 'n': total, 'avg_pnl': round(avg_pnl, 2),
                'reason': 'WR<60%，建议降低乘数'
            }

    return adjustments

def run_ic_feedback(verbose=True):
    settled = _load_settled()
    if len(settled) < 10:
        print('数据不足（需要10条以上结算记录）')
        return {}

    scores   = [p.get('score', 0) for p in settled]
    outcomes = [1 if p['outcome'] in ('TP1','TP2') else 0 for p in settled]
    ic, ic_status = _calc_ic(scores, outcomes)
    buckets       = _wr_by_bucket(settled)
    regime_matrix = _wr_by_regime(settled)
    adjustments   = _generate_weight_adjustments(ic, buckets, regime_matrix)

    # 写入wr_matrix_realtime
    rt_path = DATA / 'wr_matrix_realtime.json'
    rt = json.loads(rt_path.read_text()) if rt_path.exists() else {}
    rt['ic_feedback'] = {
        'ic': round(ic, 4),
        'ic_status': ic_status,
        'n': len(settled),
        'overall_wr': round(sum(outcomes)/len(outcomes), 4),
        'adjustments': adjustments,
        'updated': time.strftime('%Y-%m-%d %H:%M')
    }
    # 写入各体制实测WR
    for key, stats in regime_matrix.items():
        total = stats['win'] + stats['loss']
        if total < 5:
            continue
        wr = stats['win'] / total
        avg_pnl = sum(stats['pnls']) / len(stats['pnls'])
        rt[f'paper_realtime:{key}'] = {
            'wr': round(wr, 4), 'n': total,
            'avg_pnl': round(avg_pnl, 4),
            'source': 'wuqu_paper_settled',
            'updated': time.strftime('%Y-%m-%d')
        }
    rt_path.write_text(json.dumps(rt, ensure_ascii=False, indent=2))

    if verbose:
        print('═' * 60)
        print(f'  🏛️ 梵天IC反馈报告  {time.strftime("%Y-%m-%d %H:%M")}')
        print('═' * 60)
        print(f'\n📊 基础统计')
        print(f'  有效结算: {len(settled)}条')
        print(f'  整体WR:   {sum(outcomes)/len(outcomes)*100:.1f}%')
        print(f'  IC值:     {ic:.4f}  ({ic_status})')

        print(f'\n📈 评分段WR分布')
        for k in ['<130','130-145','145-160','160-175','>175']:
            b = buckets.get(k, {'win':0,'loss':0})
            total = b['win'] + b['loss']
            if total == 0:
                continue
            wr = b['win']/total*100
            bar = '█' * int(wr/5) + '░' * (20-int(wr/5))
            print(f'  {k:>8}: [{bar}] {wr:5.1f}% (n={total})')

        print(f'\n🎯 体制WR矩阵')
        for k, s in sorted(regime_matrix.items(), key=lambda x: -(x[1]['win']+x[1]['loss'])):
            total = s['win'] + s['loss']
            if total < 3:
                continue
            wr = s['win']/total*100
            avg_p = sum(s['pnls'])/len(s['pnls'])
            print(f'  {k:35}: WR={wr:5.1f}% avgPnL={avg_p:+.2f}% (n={total})')

        print(f'\n🔧 权重调整建议')
        for k, v in adjustments.items():
            if k == 'ic_alert':
                print(f'  IC诊断: {v["diagnosis"]}')
                print(f'  建议:   {v["action"]}')
            elif k == 'best_wr_bucket':
                print(f'  最优评分段: {v["bucket"]} WR={v["wr"]*100:.1f}%')
            elif k.startswith('multiplier_'):
                regime = k.replace('multiplier_','')
                print(f'  {regime}: {v["current"]}→{v["suggested"]} (WR={v["wr"]*100:.0f}% {v["reason"]})')

        print(f'\n✅ 已写入 wr_matrix_realtime.json')
        print('═' * 60)

    return rt.get('ic_feedback', {})

if __name__ == '__main__':
    import sys
    run_ic_feedback(verbose=True)
