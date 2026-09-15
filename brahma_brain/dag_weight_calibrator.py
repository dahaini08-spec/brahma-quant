#!/usr/bin/env python3
"""
dag_weight_calibrator.py — DAG权重自动校准器（蘑菇体机制）
============================================================
[2026-09-15 苏摩111] 果蝇脑→梵天系统 最大帮助落地

原理：
- 果蝇蘑菇体每天"清理弱连接、强化强连接"
- 梵天等价物：用WR反哺数据自动调整scoring_config.json的weights
- WR>60%的维度weight×1.2（强化强连接）
- WR<45%的维度weight×0.5（修剪弱连接）
- WR 45-60%的维度weight不变（中性）

数据源：
- dharma_iron_evidence.json → 体制×方向WR（铁证级n≥1000）
- live_signal_log.jsonl → 实盘WR（补充验证）

运行频率：每周一次（crontab周日03:00）
"""

import json, os, sys, time, shutil
from pathlib import Path
from collections import defaultdict

BRAIN = Path(__file__).parent
DATA = BRAIN.parent / "data"
CONFIG_FILE = DATA / "scoring_config.json"
EVIDENCE_FILE = DATA / "dharma_iron_evidence.json"
SIGNAL_LOG = DATA / "live_signal_log.jsonl"
BACKUP_DIR = DATA / "scoring_config_backups"

# 校准参数
STRONG_WR = 0.60    # WR>60% → 强化×1.2
WEAK_WR = 0.45      # WR<45% → 修剪×0.5
NEUTRAL_MIN = 0.45  # 45-60% → 不变
NEUTRAL_MAX = 0.60
BOOST_FACTOR = 1.2
PRUNE_FACTOR = 0.5
MIN_WEIGHT = 0.3    # 最低权重（不归零）
MAX_WEIGHT = 2.0    # 最高权重


def load_evidence():
    """加载达摩院铁证"""
    with open(EVIDENCE_FILE) as f:
        return json.load(f)


def load_live_signals():
    """加载实盘信号日志"""
    signals = []
    if SIGNAL_LOG.exists():
        with open(SIGNAL_LOG) as f:
            for line in f:
                if line.strip():
                    try:
                        signals.append(json.loads(line))
                    except:
                        pass
    return signals


def compute_dim_wr_by_regime(signals, evidence):
    """
    计算每个维度在不同体制×方向下的实际WR
    返回: {(regime, direction): {dim_name: {'wr': float, 'n': int}}}
    """
    # 从evidence获取体制×方向的总WR
    rm = evidence.get('regime_direction_matrix', {})
    regime_dir_wr = {}
    for k, v in rm.items():
        if isinstance(v, dict) and 'wr' in v:
            # 标准化key格式：BEAR_TREND_SHORT → (BEAR_TREND, SHORT)
            parts = k.rsplit('_', 1)
            if len(parts) == 2 and parts[1] in ('LONG', 'SHORT'):
                regime_dir_wr[(parts[0], parts[1])] = {
                    'wr': v['wr'],
                    'n': v.get('n', 0),
                    'pnl': v.get('avg_pnl', 0),
                }

    # 从live_signal_log补充维度级WR
    dim_stats = defaultdict(lambda: defaultdict(lambda: {'win': 0, 'loss': 0, 'settled': 0}))
    for s in signals:
        reg = s.get('regime', '')
        direction = s.get('direction', s.get('signal', ''))
        if direction == 1 or direction == 'LONG':
            direction = 'LONG'
        elif direction == -1 or direction == 'SHORT':
            direction = 'SHORT'
        result = s.get('result')
        if result not in ('WIN', 'LOSS'):
            continue

        # 从信号的breakdown中提取各维度得分
        breakdown = s.get('breakdown', s.get('confluence_breakdown', {}))
        for dim_key, dim_val in breakdown.items():
            if dim_key.startswith('s') and isinstance(dim_val, (int, float)):
                key = (reg, direction)
                dim_stats[key][dim_key]['settled'] += 1
                if result == 'WIN':
                    dim_stats[key][dim_key]['win'] += 1
                else:
                    dim_stats[key][dim_key]['loss'] += 1

    # 计算每维度WR
    dim_wr = {}
    for (reg, direction), dims in dim_stats.items():
        dim_wr[(reg, direction)] = {}
        for dim, stats in dims.items():
            if stats['settled'] >= 5:  # 至少5个样本
                wr = stats['win'] / stats['settled']
                dim_wr[(reg, direction)][dim] = {
                    'wr': wr,
                    'n': stats['settled'],
                    'source': 'live_signal_log',
                }

    return regime_dir_wr, dim_wr


def calibrate_weights(config, regime_dir_wr, dim_wr):
    """
    校准scoring_config的weights
    规则：
    - 体制×方向总WR>60% → 所有active_dims weight×1.2（强化）
    - 体制×方向总WR<45% → 所有active_dims weight×0.5（修剪）
    - 体制×方向总WR 45-60% → 不变
    - 如果有维度级WR数据，按维度单独校准
    """
    changes = []
    calibrated = json.loads(json.dumps(config))  # 深拷贝

    for regime in ['CHOP_MID', 'BEAR_TREND', 'BEAR_EARLY', 'BULL_TREND', 'BEAR_RECOVERY', 'BULL_EARLY']:
        if regime not in calibrated:
            continue

        for direction in ['LONG', 'SHORT']:
            dir_cfg = calibrated[regime].get(direction, {})
            weights = dir_cfg.get('weights', {})
            if not weights:
                continue

            # 体制×方向总WR
            key = (regime, direction)
            total_wr = regime_dir_wr.get(key, {}).get('wr', 0.5)  # 默认中性
            total_n = regime_dir_wr.get(key, {}).get('n', 0)

            # 维度级WR（如果有）
            dim_data = dim_wr.get(key, {})

            for dim in weights:
                old_w = weights[dim]

                # 优先用维度级WR，否则用体制×方向总WR
                if dim in dim_data:
                    dim_wr_val = dim_data[dim]['wr']
                    dim_n = dim_data[dim]['n']
                    source = f'live(n={dim_n})'
                else:
                    dim_wr_val = total_wr
                    source = f'regime(n={total_n})'

                # 校准逻辑
                if dim_wr_val >= STRONG_WR:
                    new_w = min(old_w * BOOST_FACTOR, MAX_WEIGHT)
                    action = 'BOOST'
                elif dim_wr_val <= WEAK_WR:
                    new_w = max(old_w * PRUNE_FACTOR, MIN_WEIGHT)
                    action = 'PRUNE'
                else:
                    new_w = old_w
                    action = 'KEEP'

                if new_w != old_w:
                    weights[dim] = round(new_w, 3)
                    changes.append({
                        'regime': regime,
                        'direction': direction,
                        'dim': dim,
                        'old_weight': old_w,
                        'new_weight': round(new_w, 3),
                        'wr': dim_wr_val,
                        'action': action,
                        'source': source,
                    })

    return calibrated, changes


def main():
    print("=" * 60)
    print("DAG权重自动校准器（蘑菇体机制）")
    print("=" * 60)

    # 1. 加载数据
    print("\n[1] 加载WR反哺数据...")
    evidence = load_evidence()
    signals = load_live_signals()
    print(f"  达摩院铁证: {len(evidence.get('regime_direction_matrix', {}))}条")
    print(f"  实盘信号: {len(signals)}条")

    # 2. 计算维度WR
    print("\n[2] 计算维度级WR...")
    regime_dir_wr, dim_wr = compute_dim_wr_by_regime(signals, evidence)
    print(f"  体制×方向WR: {len(regime_dir_wr)}组")
    print(f"  维度级WR: {len(dim_wr)}组")
    for (reg, direction), dims in dim_wr.items():
        if dims:
            print(f"    {reg}:{direction}: {len(dims)}个维度有实盘数据")

    # 3. 校准
    print("\n[3] 校准权重...")
    with open(CONFIG_FILE) as f:
        config = json.load(f)

    calibrated, changes = calibrate_weights(config, regime_dir_wr, dim_wr)

    if not changes:
        print("  无变化（所有权重在合理范围内）")
    else:
        print(f"  {len(changes)}项调整:")
        for c in changes:
            print(f"    {c['regime']}:{c['direction']} {c['dim']}: {c['old_weight']:.2f}→{c['new_weight']:.2f} ({c['action']}, WR={c['wr']:.1%}, {c['source']})")

    # 4. 备份+保存
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    backup = BACKUP_DIR / f"scoring_config_{ts}.json"
    shutil.copy2(CONFIG_FILE, backup)
    print(f"\n[4] 备份: {backup}")

    with open(CONFIG_FILE, 'w') as f:
        json.dump(calibrated, f, indent=2, ensure_ascii=False)
    print(f"  保存: {CONFIG_FILE}")

    # 5. 总结
    boost_count = sum(1 for c in changes if c['action'] == 'BOOST')
    prune_count = sum(1 for c in changes if c['action'] == 'PRUNE')
    keep_count = sum(1 for c in changes if c['action'] == 'KEEP')

    print(f"\n[5] 校准总结:")
    print(f"  强化(BOOST): {boost_count}项")
    print(f"  修剪(PRUNE): {prune_count}项")
    print(f"  保持(KEEP): {keep_count}项")
    print(f"  总调整: {len(changes)}项")

    # 输出JSON供日志
    result = {
        'timestamp': time.time(),
        'boost': boost_count,
        'prune': prune_count,
        'keep': keep_count,
        'changes': changes,
    }
    print(f"\n{json.dumps(result, indent=2)}")

    return changes


if __name__ == '__main__':
    main()
