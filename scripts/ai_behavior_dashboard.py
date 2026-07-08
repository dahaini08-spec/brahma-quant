#!/usr/bin/env python3
"""
ai_behavior_dashboard.py — 梵天 AI 行为看板
设计院封印 2026-07-02 Phase 2

追踪四大 AI 行为指标：
  1. Token 消耗（按任务/层级分类）
  2. 推送质量（推送→入场→盈利转化率）
  3. 信号日志统计（score分布/体制分布）
  4. Cron 健康（任务成功率/漏报检测）

用法：
  python3 scripts/ai_behavior_dashboard.py          # 完整看板
  python3 scripts/ai_behavior_dashboard.py --json   # JSON输出
  python3 scripts/ai_behavior_dashboard.py --push   # 推送到 Jarvis
"""
import os, sys, json, time, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from typing import Dict, List, Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 配置 ──────────────────────────────────────────────────────────
DATA_DIR  = BASE / 'data'
LOG_DIR   = BASE / 'logs'

# Token 估算配置（私有版实测经验值）
TOKEN_ESTIMATES = {
    # cron任务名 → 估算token/次
    'brahma-360-daily':     8000,
    'smart-digest-6h':      3000,
    'brahma-ci-probe':      500,
    'brahma-scan-guard':    6000,
    'rsi-structure-watcher': 0,    # 零成本守望
    # 'ws-guardian-keepalive'  # 已删除 2026-07-08: 0,
    'pump-hunter':          500,
    'oi-surge-scanner':     800,
    'signal-watcher-6h':    200,
    'btc-regime-watcher':   0,
    'brahma-360-health':    200,
    'auto-executor-1h':     100,
    'live-signal-settle-2h': 100,
    'timesfm-bridge-4h':    300,
    'market-structure-4h':  200,
    # Square帖（P3/P4 lightContext=True）
    '晚盘深度帖-Square':    1000,
    '午盘快讯-Square':      800,
    '早间综合-Square':      1200,
    '互动钩子帖-Square':    600,
    'square-weekly-report': 2000,
}

# 每日运行次数
DAILY_RUNS = {
    'brahma-360-daily':     1,
    'smart-digest-6h':      4,
    'brahma-ci-probe':      4,
    'brahma-scan-guard':    2,
    'rsi-structure-watcher': 288,  # 每5m
    # 'ws-guardian-keepalive'  # 已删除 2026-07-08: 96,
    'pump-hunter':          48,    # 每30m = 48次/天（已优化）
    'regime-switch-monitor': 48,   # 新增 每30m
    'oi-surge-scanner':     6,
    'signal-watcher-6h':    4,
    'btc-regime-watcher':   288,   # 零成本守望，0 tokens
    'brahma-360-health':    12,
    'auto-executor-1h':     12,
    'live-signal-settle-2h': 12,
    'timesfm-bridge-4h':    6,
    'market-structure-4h':  6,
    '晚盘深度帖-Square':    1,
    '午盘快讯-Square':      1,
    '早间综合-Square':      1,
    '互动钩子帖-Square':    0.5,
    'square-weekly-report': 0.14,
}


# ════════════════════════════════════════════════════════════════════
# 模块1: Token 消耗估算
# ════════════════════════════════════════════════════════════════════

def estimate_token_usage() -> Dict:
    """估算当日 Token 消耗（基于 cron 任务）"""
    daily_tokens = 0
    task_breakdown = {}

    for task, tokens_per_run in TOKEN_ESTIMATES.items():
        runs = DAILY_RUNS.get(task, 1)
        daily = int(tokens_per_run * runs)
        task_breakdown[task] = {
            'tokens_per_run': tokens_per_run,
            'runs_per_day': runs,
            'daily_tokens': daily,
        }
        daily_tokens += daily

    # 按消耗排序
    sorted_tasks = sorted(task_breakdown.items(), key=lambda x: -x[1]['daily_tokens'])

    return {
        'total_daily_tokens': daily_tokens,
        'budget_pct': round(daily_tokens / 96000 * 100, 1),  # 96k = 活跃日预算
        'top_consumers': [(name, info['daily_tokens']) for name, info in sorted_tasks[:5]],
        'breakdown': task_breakdown,
        'zero_token_tasks': sum(1 for _, info in task_breakdown.items() if info['tokens_per_run'] == 0),
    }


# ════════════════════════════════════════════════════════════════════
# 模块2: 信号质量统计
# ════════════════════════════════════════════════════════════════════

def analyze_signal_quality() -> Dict:
    """分析 live_signal_log.jsonl 的信号质量"""
    signal_log = DATA_DIR / 'live_signal_log.jsonl'
    feedback_log = DATA_DIR / 'signal_feedback.jsonl'

    result = {
        'total_signals': 0,
        'score_distribution': {},
        'regime_distribution': {},
        'direction_distribution': {},
        'avg_score': 0,
        'last_signal_age_h': None,
        'feedback_available': False,
        'push_to_entry_rate': None,
        'entry_to_profit_rate': None,
    }

    # 读取信号日志
    signals = []
    if signal_log.exists() and signal_log.stat().st_size > 0:
        try:
            for line in signal_log.read_text().splitlines():
                if line.strip():
                    signals.append(json.loads(line))
        except Exception:
            pass

    if signals:
        result['total_signals'] = len(signals)
        scores = [s.get('score', 0) for s in signals]
        result['avg_score'] = round(sum(scores) / len(scores), 1)

        # 分布统计
        score_ranges = {'<120': 0, '120-139': 0, '140-159': 0, '160-174': 0, '175+': 0}
        for s in scores:
            if s < 120: score_ranges['<120'] += 1
            elif s < 140: score_ranges['120-139'] += 1
            elif s < 160: score_ranges['140-159'] += 1
            elif s < 175: score_ranges['160-174'] += 1
            else: score_ranges['175+'] += 1
        result['score_distribution'] = score_ranges

        regimes = [s.get('regime', '?') for s in signals]
        result['regime_distribution'] = dict(Counter(regimes))

        dirs = [s.get('direction', s.get('signal_dir', '?')) for s in signals]
        result['direction_distribution'] = dict(Counter(dirs))

        # 最后信号时间
        last = signals[-1].get('timestamp', signals[-1].get('ts', ''))
        if last:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(last.replace('Z', '+00:00'))
                age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                result['last_signal_age_h'] = round(age, 1)
            except Exception:
                pass

    # 读取反馈日志（入场→盈利）
    feedbacks = []
    if feedback_log.exists():
        try:
            for line in feedback_log.read_text().splitlines():
                if line.strip():
                    feedbacks.append(json.loads(line))
        except Exception:
            pass

    if feedbacks:
        result['feedback_available'] = True
        entries = [f for f in feedbacks if f.get('type') == 'entry']
        profits = [f for f in feedbacks if f.get('type') == 'profit']
        if len(signals) > 0 and entries:
            result['push_to_entry_rate'] = round(len(entries) / len(signals) * 100, 1)
        if entries and profits:
            result['entry_to_profit_rate'] = round(len(profits) / len(entries) * 100, 1)

    return result


# ════════════════════════════════════════════════════════════════════
# 模块3: Cron 健康状态
# ════════════════════════════════════════════════════════════════════

def get_cron_health() -> Dict:
    """检查 cron 任务健康状态"""
    try:
        r = subprocess.run(
            ['openclaw', 'cron', 'list'],
            capture_output=True, text=True, timeout=10
        )
        lines = r.stdout.split('\n')
    except Exception:
        return {'error': 'openclaw cron list 失败'}

    total = 0
    ok_count = 0
    failed_count = 0
    tasks = []

    for line in lines:
        if 'every' in line or 'at ' in line:
            total += 1
            parts = line.split()
            if len(parts) >= 2:
                name = parts[1].rstrip('.')
                status = 'ok' if 'ok' in line else ('failed' if 'failed' in line else 'unknown')
                if status == 'ok': ok_count += 1
                elif status == 'failed': failed_count += 1
                tasks.append({'name': name, 'status': status})

    return {
        'total': total,
        'ok': ok_count,
        'failed': failed_count,
        'health_pct': round(ok_count / max(total, 1) * 100, 1),
        'tasks': tasks[:10],  # 前10个
    }


# ════════════════════════════════════════════════════════════════════
# 模块4: 系统整体健康快照
# ════════════════════════════════════════════════════════════════════

def get_system_snapshot() -> Dict:
    """系统整体状态快照"""
    snapshot = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'uptime': None,
        'data_freshness': {},
    }

    # 关键文件鲜度
    key_files = {
        'brahma_state': DATA_DIR / 'brahma_state.json',
        'regime_state': DATA_DIR / 'regime_switch_state.json',
        'wuqu_positions': DATA_DIR / 'wuqu_positions.json',
        'live_signal_log': DATA_DIR / 'live_signal_log.jsonl',
    }

    now = time.time()
    for name, path in key_files.items():
        if path.exists():
            age_h = (now - path.stat().st_mtime) / 3600
            snapshot['data_freshness'][name] = {
                'age_h': round(age_h, 1),
                'ok': age_h < 24,
            }
        else:
            snapshot['data_freshness'][name] = {'age_h': None, 'ok': False}

    return snapshot


# ════════════════════════════════════════════════════════════════════
# 看板渲染
# ════════════════════════════════════════════════════════════════════

def render_dashboard() -> str:
    """渲染完整 AI 行为看板"""
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = [
        f"🤖 梵天 AI 行为看板 | {now_str}",
        "═" * 50,
    ]

    # Token消耗
    tok = estimate_token_usage()
    budget_icon = '🟢' if tok['budget_pct'] < 70 else ('🟡' if tok['budget_pct'] < 90 else '🔴')
    lines += [
        "",
        "📊 Token 消耗估算",
        f"  日消耗: ~{tok['total_daily_tokens']:,} tokens",
        f"  预算占比: {budget_icon} {tok['budget_pct']}% (基准96k)",
        f"  零成本任务: {tok['zero_token_tasks']} 个",
        "  Top消耗:",
    ]
    for name, tokens in tok['top_consumers'][:3]:
        lines.append(f"    {name}: {tokens:,}/天")

    # 信号质量
    sig = analyze_signal_quality()
    lines += [
        "",
        "🎯 信号质量统计",
        f"  总信号: {sig['total_signals']} 笔",
    ]
    if sig['total_signals'] > 0:
        lines.append(f"  平均分: {sig['avg_score']}")
        if sig['last_signal_age_h'] is not None:
            age_icon = '🟢' if sig['last_signal_age_h'] < 6 else '🟡'
            lines.append(f"  最新信号: {age_icon} {sig['last_signal_age_h']}h 前")
        if sig['regime_distribution']:
            top_regime = max(sig['regime_distribution'], key=sig['regime_distribution'].get)
            lines.append(f"  主要体制: {top_regime}")
    else:
        lines.append("  ⏸ 近期无有效信号（震荡期守望中）")

    if sig['push_to_entry_rate'] is not None:
        lines.append(f"  推送→入场率: {sig['push_to_entry_rate']}%")
    if sig['entry_to_profit_rate'] is not None:
        lines.append(f"  入场→盈利率: {sig['entry_to_profit_rate']}%")

    # Cron健康
    cron = get_cron_health()
    if 'error' not in cron:
        health_icon = '🟢' if cron['health_pct'] >= 90 else ('🟡' if cron['health_pct'] >= 70 else '🔴')
        lines += [
            "",
            "⚙️ Cron 任务健康",
            f"  总任务: {cron['total']} | OK: {cron['ok']} | Failed: {cron['failed']}",
            f"  健康率: {health_icon} {cron['health_pct']}%",
        ]

    # 系统快照
    snap = get_system_snapshot()
    lines += ["", "💾 数据鲜度"]
    for name, info in snap['data_freshness'].items():
        if info['age_h'] is not None:
            icon = '🟢' if info['ok'] else '🔴'
            lines.append(f"  {icon} {name}: {info['age_h']}h")
        else:
            lines.append(f"  ❌ {name}: 不存在")

    lines.append("")
    lines.append("─" * 50)

    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true', help='JSON输出')
    parser.add_argument('--push', action='store_true', help='推送到Jarvis')
    parser.add_argument('--token-only', action='store_true', help='仅Token统计')
    args = parser.parse_args()

    if args.json:
        result = {
            'tokens': estimate_token_usage(),
            'signals': analyze_signal_quality(),
            'cron': get_cron_health(),
            'snapshot': get_system_snapshot(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.token_only:
        tok = estimate_token_usage()
        print(f"日Token消耗: {tok['total_daily_tokens']:,}")
        print(f"预算占比: {tok['budget_pct']}%")
        for name, tokens in tok['top_consumers']:
            print(f"  {name}: {tokens:,}/天")
    else:
        dashboard = render_dashboard()
        print(dashboard)
        if args.push:
            try:
                sys.path.insert(0, str(BASE / 'scripts'))
                from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
                subprocess.run([
                    'openclaw', 'msg', 'send',
                    '--to', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
                    '--channel', 'jarvis',
                    '--message', dashboard
                ])
                print("✅ 已推送到 Jarvis")
            except Exception as e:
                print(f"推送失败: {e}")
