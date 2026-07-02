#!/usr/bin/env python3
"""
data_freshness_guard.py — 梵天数据鲜度守门人
设计院封印 2026-07-02

替代brahma_ci_v2中的手动P6检查，变为主动实时守护：
- 监控所有关键数据文件的更新时间
- 超过TTL阈值时自动推送告警（而非等CI发现）
- 支持体制感知的动态TTL（震荡期可延长，趋势期严格）
"""
import os
import json
import time
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 关键数据文件 + TTL配置 ────────────────────────────────────────────
# 格式: (文件路径, 正常TTL小时, 严格TTL小时, 描述, 级别P0-P3)
WATCH_FILES = [
    # P0 关键实时数据
    ('logs/signal_trace.jsonl',          6,  3,  '信号追踪日志',   'P0'),
    ('data/live_signal_log.jsonl',       24, 12, '活跃信号日志',   'P0'),
    ('brahma_brain/brahma_state.json',   2,  1,  '梵天状态快照',   'P0'),
    
    # P1 重要业务数据
    ('data/wuqu_positions.json',         1,  0.5, '持仓记录',      'P1'),
    ('data/regime_state.json',           6,  4,  '体制状态',      'P1'),
    ('data/live_performance.json',       25, 13, '实盘表现',      'P1'),
    
    # P2 参考数据
    ('data/signal_feedback.jsonl',       48, 24, '信号反馈',      'P2'),
    ('logs/brahma_health.json',          3,  2,  '健康检查',      'P2'),
    
    # P3 定期数据（允许较长时间不更新）
    ('dharma/results',                   168, 72, '回测结果',      'P3'),
    ('data/backtest_summary.json',       168, 48, '回测摘要',      'P3'),
]


def get_file_age_hours(path: str) -> float:
    """获取文件距今小时数，文件不存在返回inf"""
    full_path = BASE / path
    if not full_path.exists():
        return float('inf')
    # 目录取最新子文件
    if full_path.is_dir():
        try:
            newest = max(full_path.rglob('*.json'), key=os.path.getmtime, default=None)
            if newest:
                return (time.time() - newest.stat().st_mtime) / 3600
        except Exception:
            pass
        return float('inf')
    return (time.time() - full_path.stat().st_mtime) / 3600


def get_current_regime() -> str:
    """读取当前体制（用于决定是否使用严格TTL）"""
    try:
        regime_file = BASE / 'data' / 'regime_state.json'
        if regime_file.exists():
            data = json.loads(regime_file.read_text())
            return data.get('regime', 'UNKNOWN')
    except Exception:
        pass
    return 'UNKNOWN'


def check_freshness(strict_mode: bool = False) -> dict:
    """
    检查所有关键文件鲜度
    strict_mode=True 时使用严格TTL（趋势体制）
    """
    regime = get_current_regime()
    is_trend = 'TREND' in regime  # BEAR_TREND / BULL_TREND 使用严格TTL
    use_strict = strict_mode or is_trend

    results = {
        'ok': [],
        'warn': [],
        'missing': [],
        'regime': regime,
        'strict_mode': use_strict,
        'timestamp': time.time(),
    }

    for file_path, normal_ttl, strict_ttl, desc, level in WATCH_FILES:
        ttl = strict_ttl if use_strict else normal_ttl
        age = get_file_age_hours(file_path)

        record = {
            'path': file_path,
            'desc': desc,
            'level': level,
            'age_hours': round(age, 1),
            'ttl_hours': ttl,
        }

        if age == float('inf'):
            record['status'] = 'MISSING'
            results['missing'].append(record)
        elif age > ttl:
            record['status'] = 'STALE'
            results['warn'].append(record)
        else:
            record['status'] = 'FRESH'
            results['ok'].append(record)

    return results


def format_report(results: dict) -> str:
    """格式化报告"""
    lines = []
    regime = results['regime']
    total = len(results['ok']) + len(results['warn']) + len(results['missing'])
    fresh = len(results['ok'])

    lines.append(f"🌊 梵天数据鲜度守门 | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    lines.append(f"体制: {regime} | 严格模式: {'是' if results['strict_mode'] else '否'}")
    lines.append(f"总计: {total} | ✅新鲜: {fresh} | ⚠️陈旧: {len(results['warn'])} | ❌缺失: {len(results['missing'])}")

    if results['warn']:
        lines.append("\n⚠️ 陈旧文件:")
        for r in results['warn']:
            lines.append(f"  [{r['level']}] {r['desc']}: {r['age_hours']}h (限{r['ttl_hours']}h)")

    if results['missing']:
        lines.append("\n❌ 缺失文件:")
        for r in results['missing']:
            lines.append(f"  [{r['level']}] {r['desc']}: {r['path']}")

    return '\n'.join(lines)


def run_check_and_save() -> None:
    """执行检查并保存结果，供brahma_ci_v2读取"""
    results = check_freshness()
    
    # 保存状态文件（brahma_ci_v2可读）
    status_file = BASE / 'logs' / 'data_freshness_status.json'
    status_file.parent.mkdir(exist_ok=True)
    with open(status_file, 'w') as f:
        json.dump(results, f, indent=2)

    # 输出报告
    report = format_report(results)
    print(report)

    # P0/P1 问题时推送告警
    p0_issues = [r for r in results['warn'] + results['missing']
                 if r['level'] in ('P0', 'P1')]
    if p0_issues:
        print(f"\n🚨 发现{len(p0_issues)}个P0/P1级数据问题，建议立即处理")

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--strict', action='store_true', help='严格模式')
    parser.add_argument('--json', action='store_true', help='输出JSON')
    args = parser.parse_args()

    results = check_freshness(strict_mode=args.strict)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_report(results))
        # 保存状态
        status_file = BASE / 'logs' / 'data_freshness_status.json'
        status_file.parent.mkdir(exist_ok=True)
        with open(status_file, 'w') as f:
            json.dump(results, f, indent=2)
