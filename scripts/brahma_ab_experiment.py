#!/usr/bin/env python3
"""
brahma_ab_experiment.py — A/B实验框架 [2026-10-03 苏摩111 梵天自进化Phase3]
接入位置: brahma_crontab.txt (每周日 03:00 UTC) + meta_cognition_state.json

功能：
  1. 自动从meta_cognition发现可测假设（IC异常维度）
  2. 生成A/B实验方案（对照组 vs 实验组参数）
  3. 跟踪实验进度（纸面盘对照）
  4. 实验结论 → 推送苏摩111审批 → 封印

铁律：
  实验只在纸面盘运行，不影响实盘逻辑
  实验组WR > 对照组5%以上 才推荐封印
  每次变更必须推送苏摩确认
"""
import json, time, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
EXPERIMENTS_FILE = DATA / 'ab_experiments.json'


def _load_experiments() -> list:
    if EXPERIMENTS_FILE.exists():
        try:
            return json.loads(EXPERIMENTS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_experiments(experiments: list):
    EXPERIMENTS_FILE.write_text(json.dumps(experiments, ensure_ascii=False, indent=2))


def discover_hypotheses() -> list:
    """从meta_cognition发现可测假设"""
    mc_path = DATA / 'meta_cognition_state.json'
    if not mc_path.exists():
        return []

    mc = json.loads(mc_path.read_text())
    dim = mc.get('dimension_scores', {})
    hypotheses = []

    for dim_name, stats in dim.items():
        ic = stats.get('ic', 0.0)
        total = stats.get('wins', 0) + stats.get('losses', 0)
        weight = stats.get('weight_mult', 1.0)

        if total < 10:
            continue

        # IC持续负 → 假设：降低该维度权重能提升WR
        if ic < -0.01 and weight > 0.3:
            hypotheses.append({
                'id': f'h_{dim_name}_downweight_{int(time.time())}',
                'type': 'dimension_weight',
                'dim': dim_name,
                'hypothesis': f'{dim_name}维度IC={ic:.4f}持续负，降权能提升WR',
                'control': {'weight': weight},
                'experiment': {'weight': round(weight * 0.7, 3)},
                'min_trades': 30,
                'success_threshold': 0.05,  # 实验组WR高5%才封印
            })

        # IC持续正 → 假设：提升该维度权重能进一步改善
        if ic > 0.05 and weight < 1.5:
            hypotheses.append({
                'id': f'h_{dim_name}_upweight_{int(time.time())}',
                'type': 'dimension_weight',
                'dim': dim_name,
                'hypothesis': f'{dim_name}维度IC={ic:.4f}持续正，升权能进一步改善',
                'control': {'weight': weight},
                'experiment': {'weight': round(weight * 1.3, 3)},
                'min_trades': 30,
                'success_threshold': 0.05,
            })

    # 固定假设：CHOP_MID + Hurst>0.65 是否值得降门槛
    hypotheses.append({
        'id': f'h_chop_hurst_gate_{int(time.time())}',
        'type': 'score_gate',
        'hypothesis': 'CHOP_MID + Hurst>0.65 组合信号，门槛从110降到95是否提升WR',
        'control': {'min_score_chop_hurst': 110},
        'experiment': {'min_score_chop_hurst': 95},
        'min_trades': 30,
        'success_threshold': 0.05,
        'filter': 'regime=CHOP_MID AND hurst>0.65',
    })

    return hypotheses


def create_experiment(hypothesis: dict) -> dict:
    """创建新实验"""
    exp = {
        'id': hypothesis['id'],
        'status': 'running',  # running / concluded / approved / rejected
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'hypothesis': hypothesis,
        'control_trades': [],
        'experiment_trades': [],
        'control_wr': None,
        'experiment_wr': None,
        'conclusion': None,
        'approved_by': None,
    }
    return exp


def evaluate_experiments() -> list:
    """评估所有运行中的实验"""
    experiments = _load_experiments()
    concluded = []

    for exp in experiments:
        if exp.get('status') != 'running':
            continue

        ctrl = exp.get('control_trades', [])
        expt = exp.get('experiment_trades', [])
        min_trades = exp['hypothesis'].get('min_trades', 30)

        if len(ctrl) < min_trades or len(expt) < min_trades:
            continue  # 样本不足

        ctrl_wr = sum(1 for t in ctrl if t.get('pnl', 0) > 0) / len(ctrl)
        expt_wr = sum(1 for t in expt if t.get('pnl', 0) > 0) / len(expt)
        threshold = exp['hypothesis'].get('success_threshold', 0.05)

        exp['control_wr'] = round(ctrl_wr, 4)
        exp['experiment_wr'] = round(expt_wr, 4)
        exp['status'] = 'concluded'

        diff = expt_wr - ctrl_wr
        if diff >= threshold:
            exp['conclusion'] = f'✅ 实验成功: 实验组WR={expt_wr:.1%} > 对照组{ctrl_wr:.1%} (+{diff:.1%}) ≥ 阈值{threshold:.0%}'
            exp['recommendation'] = 'APPROVE'
        else:
            exp['conclusion'] = f'❌ 实验未达标: 实验组WR={expt_wr:.1%} vs 对照组{ctrl_wr:.1%} (差{diff:+.1%} < 阈值{threshold:.0%})'
            exp['recommendation'] = 'REJECT'

        concluded.append(exp)

    _save_experiments(experiments)
    return concluded


def run_weekly_experiment_report():
    """每周日生成进化实验报告"""
    print(f'[ABExperiment] 开始周度实验报告 {time.strftime("%Y-%m-%d %H:%M UTC")}')

    # 1. 发现新假设
    hypotheses = discover_hypotheses()
    experiments = _load_experiments()
    existing_ids = {'_'.join(e['id'].split('_')[:-1]) for e in experiments}

    new_count = 0
    for h in hypotheses:
        # 避免重复创建同类实验
        same_type = [e for e in experiments
                     if e['hypothesis'].get('type') == h.get('type')
                     and e['hypothesis'].get('dim', '') == h.get('dim', '')
                     and e.get('status') == 'running']
        if not same_type:
            experiments.append(create_experiment(h))
            new_count += 1
            print(f'  新实验: {h["hypothesis"]}')

    # 2. 评估已有实验
    concluded = evaluate_experiments()

    # 3. 保存
    _save_experiments(experiments)

    # 4. 推送报告
    running = [e for e in experiments if e.get('status') == 'running']
    lines = [
        f'🧬 梵天自进化 | 周度实验报告 {time.strftime("%m/%d")}',
        f'新实验: {new_count}个 | 运行中: {len(running)}个 | 本周结论: {len(concluded)}个',
        '',
    ]
    for c in concluded:
        lines.append(c.get('conclusion', ''))
        if c.get('recommendation') == 'APPROVE':
            lines.append(f'  → 建议封印，等待苏摩111批准')

    if not concluded:
        lines.append('本周无实验结论，继续积累数据中...')

    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        import push_hub as _ph
        _ph.push_jarvis('\n'.join(lines), priority='P2')
        print(f'[ABExperiment] 报告已推送苏摩')
    except Exception as _pe:
        print(f'[ABExperiment] 推送失败: {_pe}')

    print(f'[ABExperiment] 完成: 新实验{new_count}个, 结论{len(concluded)}个')
    return {'new': new_count, 'concluded': len(concluded), 'running': len(running)}


if __name__ == '__main__':
    run_weekly_experiment_report()
